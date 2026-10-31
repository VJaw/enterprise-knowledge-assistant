"""Guardrail actions (§B10.2). NeMo's Colang flows call them (app/guardrails/rails.py); the agent calls
`groundedness_check`, `safe_fallback` and the untrusted-content scan directly.

Per-request state (`GuardState`: the PII map, flags, rail timings, and the agent's contexts and grounding for
the output rails) lives in a ContextVar set by the pipeline, so the actions need no arguments NeMo can't pass.

Detector roles, from measurements on this corpus (D-023):
- Prompt injection: regex heuristics block. LLM Guard's DeBERTa classifier flags 4 of the 30 golden questions
  ("secret rotation", "card number"), so a DeBERTa hit alone doesn't block: it makes the LLM self-check run
  before the agent instead of alongside it.
- Untrusted content: the heuristics on every passage and tool output; DeBERTa only on web results (it flags
  29 of 413 corpus paragraphs, mostly password / card FAQs, and misses the poisoned passage scanned whole).
- Output: LLM Guard Toxicity plus a zero-shot topic ban scored multi-label (LLM Guard's BanTopics normalises
  the scores across the banned topics, so the top one is always high or never decisive).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel
from rapidfuzz import fuzz

from app.agent.prompts import IDK, JUDGE_PROMPT
from app.core.types import GroundingResult, RetrievedChunk
from app.guardrails.pii import INPUT_ENTITIES, OUTPUT_ENTITIES, PIIMap, get_masker
from app.observability.langfuse import get_client, llm_callbacks
from app.retrieval.context import CITE, format_items

GROUNDED_MIN = 0.9  # §B10.2 L5
INJECTION_MIN = 0.92  # LLM Guard's default threshold
TOXICITY_BLOCK, TOXICITY_REVIEW = 0.7, 0.5  # "kill -9 the PID" scores 0.6
TOPIC_BLOCK, TOPIC_REVIEW = 0.6, 0.4
MEMO_SIZE = 4096
BANNED_TOPICS = ["violence or threats", "weapons", "self-harm", "hate speech", "sexual content",
                 "illegal drugs", "legal advice"]
UNSUPPORTED_MATCH = 75
DISCLAIMER = ("Note: parts of this answer could not be verified against the sources and were removed. "
              "Please check the cited sources.")
REMOVED = "[removed: text addressed to AI assistants]"

CHUNKS_PATH = Path("data/indexes/chunks.jsonl")

INJECTION_MODEL = "protectai/deberta-v3-base-prompt-injection-v2"
TOXICITY_MODEL = "unitary/unbiased-toxic-roberta"
TOPIC_MODEL = "MoritzLaurer/roberta-base-zeroshot-v2.0-c"


# ---------------------------------------------------------------------------------------------- request state

@dataclass
class GuardState:
    pii: PIIMap = field(default_factory=PIIMap)
    flags: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)  # ms per rail, summed
    contexts: list[RetrievedChunk] = field(default_factory=list)
    grounding: GroundingResult | None = None
    intent: str | None = None
    review_output: bool = False  # a scanner score in the grey zone: run the LLM self-check on the answer

    def flag(self, *names: str) -> None:
        self.flags += [n for n in names if n not in self.flags]


_state: ContextVar[GuardState | None] = ContextVar("guard_state", default=None)


def current() -> GuardState:
    """The request's GuardState; a throwaway one outside a request (unit tests, scripts)."""
    gs = _state.get()
    if gs is None:
        gs = GuardState()
        _state.set(gs)
    return gs


@contextmanager
def guard_state(gs: GuardState):
    token = _state.set(gs)
    try:
        yield gs
    finally:
        _state.reset(token)


@contextmanager
def rail_span(name: str, timing: str | None = None, **kw):
    """One Langfuse span per rail (§B13.1). Only masked text or scores go into it; the time is added to
    `GuardState.timings[timing or name]`."""
    t0 = time.perf_counter()
    try:
        with get_client().start_as_current_observation(name=name, as_type="guardrail", **kw) as span:
            yield span
    finally:
        gs = current()
        key = timing or name
        gs.timings[key] = gs.timings.get(key, 0.0) + (time.perf_counter() - t0) * 1000


# ---------------------------------------------------------------------------------------------- models

class Scanners:
    """LLM Guard scanners, loaded once (weights baked into the image by app/bootstrap/download_models.py)."""

    def __init__(self):
        import logging

        import structlog
        from llm_guard.input_scanners import BanTopics, PromptInjection, Toxicity
        from llm_guard.input_scanners.ban_topics import MODEL_ROBERTA_BASE_C_V2
        from llm_guard.input_scanners.prompt_injection import V2_MODEL, MatchType
        from llm_guard.input_scanners.toxicity import _toxic_labels

        # LLM Guard logs every scan at debug level through structlog, including the scanned text.
        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
        self.injection = PromptInjection(model=V2_MODEL, threshold=INJECTION_MIN, match_type=MatchType.FULL)
        self.toxicity = Toxicity(threshold=TOXICITY_REVIEW)
        self.toxic_labels = set(_toxic_labels)
        self.topics = BanTopics(topics=BANNED_TOPICS, threshold=TOPIC_BLOCK, model=MODEL_ROBERTA_BASE_C_V2)
        # Scores are memoised by text: the same answer is scanned by the pre-scan and the output rail.
        self.injection_score = lru_cache(maxsize=MEMO_SIZE)(self._injection_score)
        self.toxicity_score = lru_cache(maxsize=MEMO_SIZE)(self._toxicity_score)
        self._topics_memo = lru_cache(maxsize=MEMO_SIZE)(self._topic_scores)

    # The scanners' own scan() returns a rescaled risk score and hides sub-threshold scores, so the scores
    # come straight from their pipelines.

    def _injection_score(self, text: str) -> float:
        """DeBERTa injection probability."""
        r = self.injection._pipeline(text)[0]
        return r["score"] if r["label"] == "INJECTION" else 1.0 - r["score"]

    def _toxicity_score(self, text: str) -> float:
        """Highest probability over the toxic labels."""
        results = self.toxicity._pipeline([text])[0]
        return max((r["score"] for r in results if r["label"] in self.toxic_labels), default=0.0)

    def _topic_scores(self, text: str) -> tuple[tuple[str, float], ...]:
        out = self.topics._classifier(text, BANNED_TOPICS, multi_label=True)
        return tuple(zip(out["labels"], out["scores"], strict=True))

    def topic_scores(self, text: str) -> dict[str, float]:
        return dict(self._topics_memo(text))


@lru_cache
def get_scanners() -> Scanners:
    return Scanners()


# ---------------------------------------------------------------------------------------------- heuristics

ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff\u00ad]")
INJECTION_PATTERNS = {
    "ignore_instructions": (r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}"
                            r"\b(previous|prior|above|earlier|preceding|all|any|your|the|system|these|those)\b"
                            r"[^.\n]{0,25}"
                            r"\b(instructions?|prompts?|guidelines|directives|guardrails|programming|rules)\b"),
    "reveal_prompt": (r"\b(reveal|print|show|repeat|output|display|tell me|give me|share|leak|dump"
                      r"|what (is|are|were))\b[^.\n]{0,30}"
                      r"\b(system prompt|initial prompt|hidden prompt|developer message"
                      r"|(your|hidden|secret|original|initial) (instructions|rules|guidelines))\b"),
    "role_override": (r"\b(you are now|from now on,? (you|act|respond|answer)"
                      r"|pretend (to be|you are|you're|that you)|role-?play as"
                      r"|act as (if you|an? (unrestricted|unfiltered|uncensored|evil|rogue))"
                      r"|you will now (act|behave|answer|respond))\b"),
    "jailbreak_persona": (r"\b(do anything now|developer mode|dev ?mode|jailbr(eak|oken)"
                          r"|(answer|respond|reply|act|behave|operate)\w*\b[^.\n]{0,30}"
                          r"\b(with no|without( any)?)"
                          r" (restrictions|filters|rules|censorship|limitations))\b"),
    "grandma": (r"\b(grandma|grandmother|granny)\b[^.\n]{0,80}"
                r"\b(passwords?|keys?|secrets?|credentials?|instructions)\b"),
    "ai_directed_note": (r"\b(notes?|messages?|instructions?) (for|to) (the )?"
                         r"(ai|llm|language model|assistant|automated tool|chatbot)s?\b"),
    "exfiltration": (r"\b(send|forward|email|e-mail|post|submit)\b[^.\n]{0,40}"
                     r"\b(credentials?|passwords?|login|tokens?|api keys?)\b[^.\n]{0,40}\b(to|at)\b"),
    "concealment": (r"\bdo not (mention|reveal|disclose|tell)\b[^.\n]{0,30}"
                    r"\b(this|these) (note|instructions?|messages?)\b"),
}
INJECTION_RES = {k: re.compile(v, re.I) for k, v in INJECTION_PATTERNS.items()}
DAN = re.compile(r"\bDAN\b")  # case-sensitive: "Dan" is a name
BASE64 = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
DECODE_CUE = re.compile(r"\b(decode|base64|rot13|encoded)\b", re.I)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))).strip()


def _decoded_payloads(text: str) -> list[str]:
    out = []
    for token in BASE64.findall(text):
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if len(raw) >= 8 and " " in raw and sum(c.isprintable() for c in raw) / len(raw) > 0.9:
            out.append(raw)
    return out


def injection_patterns(text: str) -> list[str]:
    """Names of the injection heuristics the text matches, including inside base64 payloads."""
    t = normalise(text)
    hits = [k for k, rx in INJECTION_RES.items() if rx.search(t)]
    if DAN.search(t):
        hits.append("jailbreak_persona")
    for payload in _decoded_payloads(t):
        if injection_patterns(payload) or DECODE_CUE.search(t):
            hits.append("encoded_payload")
            break
    return sorted(set(hits))


# Topic policy (§B10.2 L4). Questions *about* passwords, secrets or MFA (reset, rotation, policy) are allowed.
CRED_NOUN = (r"(passwords?|passwd|passphrases?|credentials?|api[- ]?keys?|secret keys?|private keys?"
             r"|access tokens?|auth(entication)? tokens?|bearer tokens?|session tokens?|connection strings?)")
# Case-insensitive except the person name in hr_decision, which must be capitalised (`(?i:...)` groups).
TOPIC_PATTERNS = {
    "credential_request": [
        rf"(?i)\b(what('s| is| are)|tell me|give me|share|send( me)?|provide|reveal|show( me)?|list|print"
        rf"|dump|leak|get me|i need)\b[^.?!\n]{{0,40}}\b{CRED_NOUN}",
        rf"(?i)\b{CRED_NOUN} (for|to|of) (the )?\w+",
    ],
    "legal_advice": [
        r"(?i)\blegal advice\b", r"(?i)\b(should|can|could) (i|we) (sue|file a lawsuit|press charges)\b",
        r"(?i)\b(is it|would it be) legal for (me|us)\b", r"(?i)\blawsuit against\b",
        r"(?i)\bmy (lawyer|attorney)\b",
    ],
    "hr_decision": [
        r"\b(?i:should|can|could|would you)\b[^.?!\n]{0,30}"
        r"\b(?i:fire|terminate|dismiss|lay off|let go|demote|discipline|promote)\s+"
        r"((?i:him|her|them)|[A-Z][a-z]+(\s[A-Z][a-z]+)?"
        r"|(?i:(an?|the|this|that|my|our)\s+(employee|engineer|manager|teammate|coworker|colleague|person|report)))\b",
        r"(?i)\b(deserves?|earned)\b[^.?!\n]{0,20}"
        r"\b(a )?(raise|promotion|bonus|pay rise|demotion|to be fired)\b",
        r"(?i)\bwho should (be|get) (fired|laid off|let go|demoted|promoted|a raise|a bonus)\b",
    ],
}
TOPIC_RES = {k: [re.compile(p) for p in ps] for k, ps in TOPIC_PATTERNS.items()}
CRED_BENIGN = re.compile(r"\b(reset|chang|rotat|forgot|forget|updat|expir|polic|process|procedure|requirement"
                         r"|length|complex|how (do|can|should|often|long|is|are)|store|stored|storage|hash"
                         r"|manag|lockout|locked|mfa|multi-factor|rules?|minimum)\w*", re.I)


def topic_category(text: str) -> str | None:
    t = normalise(text)
    for category, patterns in TOPIC_RES.items():
        if any(p.search(t) for p in patterns):
            if category == "credential_request" and CRED_BENIGN.search(t):
                continue
            return category
    return None


SECRET_VALUE = re.compile(r"\b(password|passwd|passphrase|secret|api[- ]?key|token)\b[^.\n]{0,20}?"
                          r"\b(is|:|=)\s*[`'\"]?(?=[^\s`'\"]*[\d!@#$%^&*])[\w!@#$%^&*.\-]{8,}", re.I)


# ---------------------------------------------------------------------------------------------- input rails

async def llm_guard_injection(text: str) -> dict:
    """L2: heuristics block; a DeBERTa hit makes the LLM self-check mandatory before the agent runs."""
    gs = current()
    with rail_span("llm_guard.injection") as span:
        patterns = injection_patterns(text)
        score = 0.0 if patterns else await asyncio.to_thread(get_scanners().injection_score, normalise(text))
        suspected = score >= INJECTION_MIN
        if patterns:
            gs.flag("prompt_injection")
        elif suspected:
            gs.flag("injection_suspected")
        span.update(output={"block": bool(patterns), "suspected": suspected, "deberta": round(score, 3),
                            "patterns": patterns})
    return {"block": bool(patterns), "suspected": suspected, "score": score}


async def llm_guard_toxicity_input(text: str) -> dict:
    gs = current()
    with rail_span("llm_guard.toxicity_input") as span:
        score = await asyncio.to_thread(get_scanners().toxicity_score, normalise(text))
        if score >= TOXICITY_BLOCK:
            gs.flag("toxic_input")
        span.update(output={"block": score >= TOXICITY_BLOCK, "score": round(score, 3)})
    return {"block": score >= TOXICITY_BLOCK, "score": score}


async def mask_pii_input(text: str) -> str:
    """L3: user PII -> placeholders; the values are remembered as user-supplied (§B10.4)."""
    gs = current()
    with rail_span("presidio.mask") as span:
        masked, types = get_masker().mask(text, gs.pii, entities=INPUT_ENTITIES, from_user=True)
        if types:
            gs.flag("pii_masked_input")
        span.update(output={"masked": masked, "entities": types})
    return masked


async def check_topic_policy(text: str) -> dict:
    """L4: legal advice, HR decisions about named people, credential requests."""
    gs = current()
    with rail_span("topic_policy") as span:
        category = topic_category(text)
        if category:
            gs.flag(f"topic_{category}")
        span.update(output={"category": category})
    return {"category": category}


# ------------------------------------------------------------------------------------- L2b / L7 on content

def _strip_injections(text: str, deberta: bool) -> tuple[str, int]:
    """Replace each paragraph that reads as instructions to the assistant. DeBERTa only for web content."""
    paragraphs = re.split(r"(\n\s*\n)", text)
    removed = 0
    for i in range(0, len(paragraphs), 2):
        p = paragraphs[i]
        if not p.strip():
            continue
        hit = bool(injection_patterns(p))
        if not hit and deberta and len(p.split()) >= 5:
            hit = get_scanners().injection_score(normalise(p)) >= INJECTION_MIN
        if hit:
            paragraphs[i] = REMOVED
            removed += 1
    return "".join(paragraphs), removed


@lru_cache(maxsize=MEMO_SIZE)
def _scan_one(text: str, deberta: bool) -> tuple[str, int, tuple]:
    """Deterministic, so memoised: retrieved corpus passages repeat across requests (and the memo is
    pre-filled with the whole corpus at startup, `prefill_corpus`). Placeholders are assigned per request."""
    clean, removed = _strip_injections(text, deberta)
    return clean, removed, tuple(get_masker().analyze(clean, OUTPUT_ENTITIES))


def prefill_corpus(chunks_path: Path = CHUNKS_PATH) -> int:
    """Scan every indexed passage once, so retrieval never pays for it on the request path."""
    if not chunks_path.exists():
        return 0
    n = 0
    with chunks_path.open() as f:
        for line in f:
            _scan_one(json.loads(line)["text"], False)
            n += 1
    return n


async def scan_untrusted(text: str, source: str = "tool") -> str:
    """L2b + L7 on one tool output: strip injected instructions, mask PII (placeholders stay masked)."""
    return (await scan_texts([text], [source == "web"], f"untrusted.{source}"))[0]


async def scan_chunks(chunks: list[RetrievedChunk],
                      name: str = "untrusted.retrieved") -> list[RetrievedChunk]:
    """The same scan on retrieved passages and tool artifacts. Graph facts are template output: skipped."""
    todo = [i for i, c in enumerate(chunks) if c.source_type != "graph"]
    if not todo:
        return chunks
    texts = await scan_texts([chunks[i].text for i in todo],
                             [chunks[i].source_type == "web" for i in todo], name)
    out = list(chunks)
    for i, text in zip(todo, texts, strict=True):
        if text != chunks[i].text:
            out[i] = chunks[i].model_copy(update={"text": text})
    return out


async def scan_texts(texts: list[str], deberta: list[bool], name: str) -> list[str]:
    gs = current()
    with rail_span(name, timing="untrusted") as span:
        results = await asyncio.gather(*(asyncio.to_thread(_scan_one, t, d)
                                         for t, d in zip(texts, deberta, strict=True)))
        out, removed, pii = [], 0, []
        for clean, n, hits in results:  # placeholders are assigned here, in order, on the event loop
            masked, types = get_masker().apply(clean, hits, gs.pii, from_user=False)
            out.append(masked)
            removed += n
            pii += types
        if removed:
            gs.flag("indirect_injection")
        if pii:
            gs.flag("pii_masked_context")
        span.update(output={"items": len(texts), "injections_removed": removed, "pii_masked": len(pii)})
    return out


# ---------------------------------------------------------------------------------------- L5 groundedness

class Claim(BaseModel):
    claim: str
    supported: bool


class Judgement(BaseModel):
    claims: list[Claim]


def is_abstention(answer: str) -> bool:
    return answer.strip().rstrip(".") == IDK.rstrip(".")


async def groundedness_check(judge, answer: str, contexts: list[RetrievedChunk],
                             intent: str | None = None) -> tuple[GroundingResult, list[str]]:
    """L5: the judge splits the answer into claims and checks each against the sources; grounded if >= 90% are
    supported. `judge` is a chat model with `with_structured_output(Judgement)` applied. Returns (result,
    flags)."""
    if intent == "chit_chat":
        return GroundingResult(score=None, grounded=True, skipped="chit_chat"), []
    if not answer or is_abstention(answer):
        return GroundingResult(score=None, grounded=True, skipped="abstained"), []
    if judge is None:
        return GroundingResult(score=None, grounded=True, skipped="no_judge"), []
    sources = format_items(contexts) or "(no sources)"
    with rail_span("groundedness", timing="groundedness") as span:
        try:
            verdict: Judgement = await judge.ainvoke(
                [SystemMessage(JUDGE_PROMPT), HumanMessage(f"Sources:\n{sources}\n\nAnswer:\n{answer}")],
                config={"callbacks": llm_callbacks(), "run_name": "groundedness_judge"})
        except Exception:  # noqa: BLE001 - a judge outage must not fail the answer
            span.update(level="WARNING", output={"error": "judge_error"})
            failed = GroundingResult(score=None, grounded=True, skipped="judge_error")
            return failed, ["grounding_check_failed"]
        claims = verdict.claims
        score = sum(c.supported for c in claims) / len(claims) if claims else 1.0
        result = GroundingResult(score=round(score, 3), grounded=score >= GROUNDED_MIN,
                                 unsupported_claims=[c.claim for c in claims if not c.supported])
        span.update(output=result.model_dump())
        try:
            get_client().score_current_trace(name="grounding_score", value=result.score)
        except Exception:  # noqa: BLE001 - scoring is best effort
            pass
    return result, []


def drop_unsupported(answer: str, unsupported: list[str]) -> str:
    """Remove the sentences that carry an unsupported claim; keep the rest of the layout."""
    kept_lines = []
    for line in answer.splitlines():
        sentences = re.split(r"(?<=[.!?])\s+", line)
        keep = [s for s in sentences
                if not any(fuzz.partial_ratio(c.lower(), CITE.sub("", s).lower()) >= UNSUPPORTED_MATCH
                           for c in unsupported)]
        if any(s.strip() for s in keep):
            kept_lines.append(" ".join(keep))
    return "\n".join(kept_lines).strip()


def safe_fallback(answer: str, unsupported: list[str]) -> str:
    """Partial answer + disclaimer, or "I don't know" when no cited sentence survives."""
    kept = drop_unsupported(answer, unsupported)
    return f"{kept}\n\n{DISCLAIMER}" if CITE.search(kept) else IDK


# ---------------------------------------------------------------------------------------------- output rails

NON_FACTUAL = re.compile(
    r"(don't|do not) know|(not|n't) (be )?(found|available|specified|mentioned|stated|included|provided"
    r"|covered|listed|contain)|no (information|details|data|mention)"
    r"|(couldn't|could not|cannot|can't) (find|determine|answer|verify)"
    r"|(doesn't|does not|don't|do not) (say|specify|mention|state|include|cover|provide|contain|give"
    r"|indicate)|"
    r"^note:|let me know|happy to help|i can help|feel free|please (check|refer|contact|consult)", re.I)
# A conclusion restates the cited sentences before it ("Therefore, Anthony Gonzalez manages ...").
CONCLUSION = re.compile(r"^(therefore|thus|hence|so|in summary|to summari[sz]e|in short|overall"
                        r"|in conclusion)\b", re.I)
LIST_ITEM = re.compile(r"^\s*([-*•]|\d+[.)])\s+")
HEADING = re.compile(r"^\s*(#{1,6}\s|\*\*[^*]+\*\*:?\s*$)")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9*`\"(<\[])")


def _needs_citation(sentence: str) -> bool:
    s = CITE.sub("", sentence).strip()
    if len(s.split()) < 4 or s.endswith((":", "?")) or HEADING.match(s) or NON_FACTUAL.search(s) \
            or CONCLUSION.match(s):
        return False
    return s != DISCLAIMER


def _covered_lists(lines: list[str]) -> set[int]:
    """Indexes of list lines whose list is cited as a whole: by its intro line, the line right after it
    ("The steps are: 1. ... 2. ... These steps are from SOP-OPS-014 [1].") or any of its items (models often
    cite a list from one source on its last item only)."""
    filled = [i for i, ln in enumerate(lines) if ln.strip()]
    covered: set[int] = set()
    run: list[int] = []
    for i in [*filled, None]:
        if i is not None and (LIST_ITEM.match(lines[i]) or (run and lines[i][:1].isspace())):
            run.append(i)
            continue
        if run:
            pos = filled.index(run[0])
            before = lines[filled[pos - 1]] if pos > 0 else ""
            after = lines[i] if i is not None else ""
            if CITE.search(before) or CITE.search(after) or any(CITE.search(lines[j]) for j in run):
                covered.update(run)
            run = []
    return covered


def enforce_citation_text(answer: str, valid_ids: set[str]) -> tuple[str, list[str]]:
    """L6: drop citation ids that don't exist, then sentences with a factual claim and no citation. A sentence
    counts as cited when a citation follows later in the same line (paragraph-end citations); a list counts as
    cited when its intro, the line after it or one of its items cites; a line that introduces a list needs
    none. No cited sentence left -> "I don't know"."""
    flags = []

    def fix(m: re.Match) -> str:
        ids = [i.strip() for i in m.group(1).split(",")]
        good = [i for i in ids if i in valid_ids]
        if len(good) != len(ids):
            flags.append("invalid_citations_removed")
        return f"[{', '.join(good)}]" if good else ""

    text = CITE.sub(fix, answer)
    lines = text.splitlines()
    cited_lists = _covered_lists(lines)
    out, dropped = [], 0
    for n, line in enumerate(lines):
        if not line.strip():
            out.append(line)
            continue
        next_line = next((ln for ln in lines[n + 1:] if ln.strip()), "")
        intro = bool(LIST_ITEM.match(next_line)) and not LIST_ITEM.match(line)
        marker = m.group(0) if (m := LIST_ITEM.match(line)) else ""
        sentences = SENTENCE.split(line[len(marker):])
        keep = []
        for i, s in enumerate(sentences):
            covered = n in cited_lists or any(CITE.search(x) for x in sentences[i:])
            if covered or not _needs_citation(s) or (intro and i == len(sentences) - 1):
                keep.append(s)
            else:
                dropped += 1
        if keep and any(k.strip() for k in keep):
            out.append(marker + " ".join(keep))
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    result = re.sub(r"[ \t]+([.,;:])", r"\1", result)
    if dropped:
        flags.append("uncited_claims_removed")
    if not CITE.search(result) and (dropped or any(_needs_citation(s) for s in SENTENCE.split(result))):
        flags.append("uncited_answer")
        result = IDK
    return result or IDK, sorted(set(flags))


async def enforce_citations(answer: str) -> str:
    gs = current()
    if gs.intent == "chit_chat" or answer.strip().startswith(IDK):
        return answer
    with rail_span("citations.enforce") as span:
        valid = {c.metadata["cite_id"] for c in gs.contexts if "cite_id" in c.metadata}
        result, flags = enforce_citation_text(answer, valid)
        gs.flag(*flags)
        span.update(output={"flags": flags, "changed": result != answer})
    return result


async def groundedness_backstop(answer: str, judge) -> str:
    """L5 in the output rails: only when the agent's grounding node didn't judge this answer (a cache hit,
    Phase 9). Ungrounded -> safe fallback."""
    gs = current()
    if gs.grounding is not None:
        return answer
    result, flags = await groundedness_check(judge, answer, gs.contexts, gs.intent)
    gs.grounding = result
    gs.flag(*flags)
    if result.grounded:
        return answer
    gs.flag("ungrounded_fallback")
    return safe_fallback(answer, result.unsupported_claims)


async def mask_pii_output(text: str) -> str:
    """L7: Presidio on the answer. Placeholders from the user's own input are restored later, by the pipeline,
    after everything else has seen the masked text."""
    gs = current()
    with rail_span("presidio.output") as span:
        hits = await asyncio.to_thread(get_masker().analyze, text, OUTPUT_ENTITIES)
        masked, types = get_masker().apply(text, hits, gs.pii, from_user=False)
        if types:
            gs.flag("pii_masked_output")
        span.update(output={"entities": types})
    return masked


async def prescan_output(text: str) -> None:
    """Score a draft answer for toxicity / banned topics ahead of the output rails (the agent runs this
    alongside the groundedness judge); the output rail then reads the memoised scores if the text is
    unchanged. Off the critical path, so timed separately."""
    if not text.strip():
        return
    s = get_scanners()
    with rail_span("llm_guard.prescan", timing="prescan"):
        await asyncio.gather(asyncio.to_thread(s.toxicity_score, text),
                             asyncio.to_thread(s.topic_scores, text))


async def llm_guard_toxicity_output(text: str) -> dict:
    """L8: Toxicity and the banned-topic classifier, in parallel. A grey-zone score asks for the LLM
    self-check."""
    gs = current()
    s = get_scanners()

    async def toxicity():
        with rail_span("llm_guard.toxicity", timing="llm_guard.output") as span:
            score = await asyncio.to_thread(s.toxicity_score, text)
            span.update(output={"score": round(score, 3)})
        return score

    async def topics():
        with rail_span("llm_guard.ban_topics", timing="llm_guard.output") as span:
            scores = await asyncio.to_thread(s.topic_scores, text)
            span.update(output={k: round(v, 3) for k, v in scores.items()})
        return scores

    t0 = time.perf_counter()
    tox, topic = await asyncio.gather(toxicity(), topics())
    gs.timings["llm_guard.output"] = (time.perf_counter() - t0) * 1000  # wall clock, not the sum
    top, top_score = max(topic.items(), key=lambda kv: kv[1]) if topic else (None, 0.0)
    block = tox >= TOXICITY_BLOCK or top_score >= TOPIC_BLOCK
    if tox >= TOXICITY_BLOCK:
        gs.flag("toxic_output")
    if top_score >= TOPIC_BLOCK:
        gs.flag(f"banned_topic_output:{top}")
    if not block and (tox >= TOXICITY_REVIEW or top_score >= TOPIC_REVIEW):
        gs.review_output = True
    return {"block": block, "toxicity": tox, "topic": top, "topic_score": top_score}


async def check_output_policy(text: str) -> dict:
    """L8: never return something that looks like a secret value (defence in depth for poisoned sources)."""
    gs = current()
    with rail_span("output_policy") as span:
        leak = bool(SECRET_VALUE.search(text))
        if leak:
            gs.flag("secret_leak")
        span.update(output={"block": leak})
    return {"block": leak}


async def needs_output_self_check() -> bool:
    """The LLM self-check on the answer runs when nothing else vouched for it: the groundedness judge didn't
    run (chit-chat, judge error) or a scanner score is in the grey zone (D-023)."""
    gs = current()
    verified = gs.grounding is not None and gs.grounding.score is not None
    abstained = gs.grounding is not None and gs.grounding.skipped == "abstained"
    return gs.review_output or not (verified or abstained)
