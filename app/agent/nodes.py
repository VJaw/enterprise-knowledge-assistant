"""Agent nodes (§B9.1): classify -> retrieve -> agent <-> tool_node -> synthesize -> grounding_check ->
finalize.

- classify: the Phase 5 query router (QueryPlan) plus the model choice (§B12.2).
- retrieve: hybrid retrieval; its chunks and graph facts become the first citable contexts.
- agent: the routed model with the tools bound. A reply without tool calls is the draft answer.
- tool_node: runs the calls concurrently, gives citable results their turn-wide ids, traces each as
  `tool.<name>`.
- synthesize: only calls the LLM when the agent stopped at the step bound, or to regenerate after a failed
  grounding check; otherwise it passes the agent's reply through.
- grounding_check: gpt-4o-mini judge, grounded if >= 90% of claims are supported (§B10.2). Phase 8 replaces it
  with the full guardrail action.
- safe_fallback: drops the unsupported sentences and adds a disclaimer."""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel
from rapidfuzz import fuzz

from app.agent.prompts import (
    AGENT_PROMPT,
    CHAT_PROMPT,
    IDK,
    JUDGE_PROMPT,
    REGENERATE,
    SYNTH_PROMPT,
)
from app.agent.state import AgentState
from app.core.types import GroundingResult, RetrievedChunk, ToolResult
from app.llm.router import ModelRouter
from app.observability.langfuse import get_client, llm_callbacks
from app.retrieval.context import CITE, format_items, label
from app.retrieval.rerank import TOP_K
from app.retrieval.router import HISTORY_TURNS
from app.tools.common import mask_contacts, truncate

GROUNDED_MIN = 0.9  # §B10.2
HISTORY_CHARS = 600
UNSUPPORTED_MATCH = 75
DISCLAIMER = ("Note: parts of this answer could not be verified against the sources and were removed. "
              "Please check the cited sources.")
MCP_PREFIXES = ("sqlite_", "filesystem_")
FILE_DOC_ID = re.compile(r"[A-Z]+-[A-Z]+-\d{3}|INC-\d{4}-\d{3}", re.I)


class Claim(BaseModel):
    claim: str
    supported: bool


class Judgement(BaseModel):
    claims: list[Claim]


def text_of(content) -> str:
    """Message / tool content as plain text (MCP tools return a list of content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def history_pairs(messages: list[AnyMessage]) -> list[tuple[str, str]]:
    pairs, question = [], None
    for m in messages:
        if isinstance(m, HumanMessage):
            question = text_of(m.content)
        elif isinstance(m, AIMessage) and question is not None:
            pairs.append((question, text_of(m.content)))
            question = None
    return pairs[-HISTORY_TURNS:]


def is_abstention(answer: str) -> bool:
    return answer.strip().rstrip(".") == IDK.rstrip(".")


def mcp_artifact(name: str, args: dict, content: str) -> list[RetrievedChunk] | None:
    """MCP read results become citable sources like their native counterparts."""
    if name == "sqlite_read_query":
        query = " ".join(str(args.get("query", "")).split())
        digest = hashlib.sha1(query.encode()).hexdigest()[:12]
        return [RetrievedChunk(chunk_id=f"sql:mcp:{digest}", doc_id="sql", text=content, score=1.0,
                               source_type="sql", metadata={"query": query, "via": "mcp"})]
    if name in ("filesystem_read_file", "filesystem_read_text_file"):
        path = str(args.get("path", ""))
        m = FILE_DOC_ID.match(Path(path).name)  # data/raw files are named <DOC-ID>_<slug>.<ext>
        return [RetrievedChunk(chunk_id=f"file:{path}", doc_id=m.group(0).upper() if m else Path(path).name,
                               text=content, score=1.0, source_type="document",
                               metadata={"title": Path(path).name, "source_path": path, "via": "mcp"})]
    return None


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


class AgentNodes:
    def __init__(self, *, router, retriever, models: ModelRouter, tools: list[BaseTool], judge=None,
                 max_steps: int = 5):
        self.router = router
        self.retriever = retriever
        self.models = models
        self.tools = list(tools)
        self.judge = judge.with_structured_output(Judgement) if judge is not None else None
        self.max_steps = max_steps

    # ------------------------------------------------------------------ helpers

    def tools_for(self, state: AgentState) -> list[BaseTool]:
        return [t for t in self.tools if state.get("allow_web", True) or t.name != "web_search"]

    def question_block(self, state: AgentState) -> str:
        question = text_of(state["messages"][-1].content)
        plan = state.get("plan")
        parts = []
        if history := history_pairs(state["messages"][:-1]):
            parts.append("Conversation so far:\n" + "\n".join(
                f"User: {u}\nAssistant: {truncate(a, HISTORY_CHARS)}" for u, a in history))
        parts.append("Context:\n" + (format_items(state.get("contexts") or []) or "(nothing retrieved)"))
        rewritten = plan.rewritten_query if plan else question
        parts.append(f"Question: {rewritten}")
        if rewritten.strip() != question.strip():
            parts.append(f"(The user's own words: {question})")
        if plan and plan.sub_questions:  # the router's multi-hop decomposition (§B9.4 step 1)
            subs = "\n".join(f"{i}. {q}" for i, q in enumerate(plan.sub_questions, 1))
            parts.append(f"Sub-questions:\n{subs}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ nodes

    async def classify(self, state: AgentState) -> dict:
        question = text_of(state["messages"][-1].content)
        plan = await self.router.plan(question, history_pairs(state["messages"][:-1]))
        return {"plan": plan, "model": self.models.choose(plan, 0)}

    async def retrieve(self, state: AgentState) -> dict:
        res = await self.retriever.search(state["plan"], state.get("filters"), k=TOP_K)
        contexts, _ = label([], [*res.chunks, *res.facts])
        return {"contexts": contexts, "flags": [*state.get("flags", []), *res.flags]}

    async def agent(self, state: AgentState) -> dict:
        steps, flags = state.get("steps", 0), list(state.get("flags", []))
        model = self.models.fast if "model_fallback" in flags else self.models.choose(state["plan"], steps)
        messages = [SystemMessage(AGENT_PROMPT.format(max_steps=self.max_steps - 1)),
                    HumanMessage(self.question_block(state)), *state.get("scratch", [])]
        ai, used, fell_back = await self.models.ainvoke(model, messages, tools=self.tools_for(state),
                                                        run_name=f"agent.step_{steps + 1}")
        if fell_back and "model_fallback" not in flags:
            flags.append("model_fallback")
        return {"scratch": [*state.get("scratch", []), ai], "steps": steps + 1, "model": used, "flags": flags}

    async def _run_tool(self, tool: BaseTool | None, call: dict) -> tuple[str, list | None, str, int]:
        name, args = call["name"], call.get("args") or {}
        t0 = time.perf_counter()
        with get_client().start_as_current_observation(name=f"tool.{name}", as_type="tool",
                                                       input=args) as span:
            artifact = None
            if tool is None:
                content = f"error: unknown tool {name!r}"
            else:
                try:
                    msg = await tool.ainvoke({**call, "type": "tool_call"})
                    content, artifact = text_of(msg.content), getattr(msg, "artifact", None)
                    if getattr(msg, "status", "success") == "error" and not content.startswith("error"):
                        content = f"error: {content}"
                except Exception as e:  # noqa: BLE001 - e.g. arguments that fail the schema
                    content = f"error: {type(e).__name__}: {e}"
            if name.startswith(MCP_PREFIXES):
                content = mask_contacts(truncate(content))
                artifact = None if content.startswith("error") else mcp_artifact(name, args, content)
            status = "error" if content.startswith("error") else "ok"
            span.update(output=truncate(content, 2000), level="WARNING" if status == "error" else "DEFAULT")
        return content, artifact, status, int((time.perf_counter() - t0) * 1000)

    async def tool_node(self, state: AgentState) -> dict:
        calls = state["scratch"][-1].tool_calls
        by_name = {t.name: t for t in self.tools_for(state)}
        results = await asyncio.gather(*(self._run_tool(by_name.get(c["name"]), c) for c in calls))
        contexts = list(state.get("contexts", []))
        messages, records = [], []
        for call, (content, artifact, status, ms) in zip(calls, results, strict=True):
            if isinstance(artifact, list) and artifact and isinstance(artifact[0], RetrievedChunk):
                contexts, labelled = label(contexts, artifact)
                content = format_items(labelled)  # same sources, now with their turn-wide citation ids
            messages.append(ToolMessage(content=content, tool_call_id=call["id"], name=call["name"]))
            records.append(ToolResult(name=call["name"], args=call.get("args") or {}, duration_ms=ms,
                                      status=status, output=truncate(content, 500)))
        return {"scratch": [*state["scratch"], *messages], "contexts": contexts,
                "tool_results": [*state.get("tool_results", []), *records]}

    async def synthesize(self, state: AgentState) -> dict:
        plan, flags = state.get("plan"), list(state.get("flags", []))
        if plan is not None and plan.intent == "chit_chat":
            history = [m for pair in history_pairs(state["messages"][:-1])
                       for m in (HumanMessage(pair[0]), AIMessage(pair[1]))]
            ai, used, _ = await self.models.ainvoke(
                self.models.fast, [SystemMessage(CHAT_PROMPT), *history, state["messages"][-1]],
                run_name="synthesize")
            return {"draft_answer": text_of(ai.content).strip(), "model": used}

        grounding, retries = state.get("grounding"), state.get("retries", 0)
        regenerate = grounding is not None and not grounding.grounded
        scratch = state.get("scratch", [])
        last = scratch[-1] if scratch else None
        answered = isinstance(last, AIMessage) and not last.tool_calls and text_of(last.content).strip()
        if answered and not regenerate:
            return {"draft_answer": text_of(last.content).strip()}

        if isinstance(last, AIMessage) and last.tool_calls and not regenerate:
            flags.append("agent_step_limit")
        messages = [SystemMessage(SYNTH_PROMPT), HumanMessage(self.question_block(state))]
        if regenerate:
            unsupported = "\n".join(f"- {c}" for c in grounding.unsupported_claims) or "- (unspecified)"
            messages += [AIMessage(state.get("draft_answer") or ""),
                         HumanMessage(REGENERATE.format(unsupported=unsupported))]
            retries += 1
            flags.append("regenerated_ungrounded")
        model = self.models.fast if "model_fallback" in flags else (state.get("model") or self.models.fast)
        ai, used, fell_back = await self.models.ainvoke(model, messages, run_name="synthesize")
        if fell_back and "model_fallback" not in flags:
            flags.append("model_fallback")
        return {"draft_answer": text_of(ai.content).strip(), "model": used, "retries": retries,
                "flags": flags}

    async def grounding_check(self, state: AgentState) -> dict:
        draft, plan = state.get("draft_answer") or "", state.get("plan")
        if plan is not None and plan.intent == "chit_chat":
            return {"grounding": GroundingResult(score=None, grounded=True, skipped="chit_chat")}
        if not draft or is_abstention(draft):
            return {"grounding": GroundingResult(score=None, grounded=True, skipped="abstained")}
        if self.judge is None:
            return {"grounding": GroundingResult(score=None, grounded=True, skipped="no_judge")}
        sources = format_items(state.get("contexts") or []) or "(no sources)"
        try:
            verdict: Judgement = await self.judge.ainvoke(
                [SystemMessage(JUDGE_PROMPT), HumanMessage(f"Sources:\n{sources}\n\nAnswer:\n{draft}")],
                config={"callbacks": llm_callbacks(), "run_name": "grounding_judge"})
        except Exception:  # noqa: BLE001 - a judge outage must not fail the answer
            return {"grounding": GroundingResult(score=None, grounded=True, skipped="judge_error"),
                    "flags": [*state.get("flags", []), "grounding_check_failed"]}
        claims = verdict.claims
        score = sum(c.supported for c in claims) / len(claims) if claims else 1.0
        return {"grounding": GroundingResult(score=round(score, 3), grounded=score >= GROUNDED_MIN,
                                             unsupported_claims=[c.claim for c in claims if not c.supported])}

    async def safe_fallback(self, state: AgentState) -> dict:
        kept = drop_unsupported(state.get("draft_answer") or "", state["grounding"].unsupported_claims)
        answer = f"{kept}\n\n{DISCLAIMER}" if CITE.search(kept) else IDK
        return {"draft_answer": answer, "flags": [*state.get("flags", []), "ungrounded_fallback"]}

    async def finalize(self, state: AgentState) -> dict:
        answer = state.get("draft_answer") or IDK
        # pii_map is request-only (§B10.4): cleared so it never lands in the checkpoint.
        return {"answer": answer, "messages": [AIMessage(answer)], "pii_map": {}}

    # ------------------------------------------------------------------ edges

    def after_classify(self, state: AgentState) -> str:
        plan = state.get("plan")
        return "synthesize" if plan is not None and plan.intent == "chit_chat" else "retrieve"

    def after_agent(self, state: AgentState) -> str:
        last = state["scratch"][-1]
        return "tool_node" if last.tool_calls and state["steps"] < self.max_steps else "synthesize"

    def after_grounding(self, state: AgentState) -> str:
        if state["grounding"].grounded:
            return "finalize"
        return "synthesize" if state.get("retries", 0) < 1 else "safe_fallback"
