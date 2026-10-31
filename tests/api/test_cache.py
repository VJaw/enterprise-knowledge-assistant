"""Semantic cache (§B12.1): the Qdrant store with the real embedder, and the /chat hit path."""
import time

import pytest
from langchain_core.messages import AIMessage

from app.cache.semantic_cache import (
    CachedAnswer,
    bump_corpus_version,
    depends_on_history,
    filters_hash,
    normalise,
    specifics,
    write_refusal,
)
from app.core.types import GroundingResult
from app.guardrails.rails import Verdict
from tests.api.conftest import ANSWER, client, post
from tests.fakes import CHUNKS, artifact_tool, build_agent, plan, tool_call

GROUNDED = GroundingResult(score=1.0, grounded=True)
Q = "What is the retention period for customer logs?"


def entry(answer=ANSWER) -> CachedAnswer:
    cited = [c.model_copy(update={"metadata": {**c.metadata, "cite_id": i}})
             for c, i in zip(CHUNKS, ("1", "2"), strict=True)]
    return CachedAnswer(answer=answer, contexts=cited, grounding=GROUNDED, model="gpt-4o-mini",
                        intent="factual")


# ---------------------------------------------------------------------------------------------- store


async def test_hit_on_same_and_paraphrased_question(cache):
    await cache.write(Q, None, entry())
    hit = await cache.lookup("  what is the retention period for customer logs ", None)
    assert hit is not None and hit.similarity > 0.99 and hit.entry.answer == ANSWER
    assert hit.entry.contexts[1].metadata["cite_id"] == "2"
    para = await cache.lookup("What's the retention period of customer logs?", None)
    assert para is not None and para.similarity >= 0.92


async def test_miss_on_different_question_filters_version_and_ttl(cache, tmp_path):
    await cache.write(Q, {"domain": "compliance"}, entry())
    assert await cache.lookup("Who owns the Ledger DB?", {"domain": "compliance"}) is None
    assert await cache.lookup(Q, None) is None  # different filters_hash
    assert await cache.lookup(Q, {"domain": "compliance"}) is not None

    cache.ttl_s = -1  # everything is older than the TTL
    assert await cache.lookup(Q, {"domain": "compliance"}) is None
    cache.ttl_s = 86400

    bump_corpus_version(tmp_path / "corpus_version")  # what an index build does
    assert cache.count() == 1
    assert cache.reload_version() == 2 and cache.count() == 0  # stale entries purged
    assert await cache.lookup(Q, {"domain": "compliance"}) is None


async def test_same_question_overwrites_its_entry(cache):
    await cache.write(Q, None, entry("old [1]."))
    await cache.write(Q + "?", None, entry())
    assert cache.count() == 1 and (await cache.lookup(Q, None)).entry.answer == ANSWER


def test_key_helpers():
    assert normalise("  What IS\tthe  period?? ") == "what is the period"
    assert filters_hash(None) == filters_hash({}) == "none"
    assert filters_hash({"a": "1", "b": "2"}) == filters_hash({"b": "2", "a": "1"})
    assert filters_hash({"a": "1"}) != filters_hash({"a": "1", "b": "2"})
    assert depends_on_history("and who manages that team?", True)
    assert depends_on_history("What about its owner?", True)
    assert not depends_on_history("and who manages that team?", False)  # nothing to refer back to
    assert not depends_on_history("Who manages the Data-Platform team?", True)


async def test_specifics_guard_blocks_close_but_different_questions(cache):
    for cached, asked in [("How many SEV1 tickets did Ledger DB have?",
                           "How many SEV2 tickets did Ledger DB have?"),
                          ("What happened in INC-2025-012?", "What happened in INC-2025-017?"),
                          ("What depends on Auth Service?", "What does Auth Service depend on?")]:
        await cache.write(cached, None, entry())
        assert await cache.lookup(asked, None) is None, asked
        assert await cache.lookup(cached, None) is not None
    assert specifics(normalise("Refunds over $5,000?")) == (frozenset({"$5,000"}), ())
    assert specifics("which teams own what payment gateway depends on") == (frozenset(), ("",))


@pytest.mark.parametrize(("kw", "reason"), [
    ({}, None),
    ({"blocked": True}, "blocked"),
    ({"intent": "chit_chat"}, "chit_chat"),
    ({"grounding": None}, "not_grounded"),
    ({"grounding": GroundingResult(score=0.8, grounded=False)}, "not_grounded"),
    ({"grounding": GroundingResult(score=None, grounded=True, skipped="abstained")}, "not_grounded"),
    ({"masked_message": "My email is <EMAIL_ADDRESS_1>, which SOP?"}, "pii_placeholder"),
    ({"answer": "Call <PHONE_NUMBER_1> [1]."}, "pii_placeholder"),
    ({"tools": ["sql_query", "web_search"]}, "web_search"),
    ({"contexts": [CHUNKS[0].model_copy(update={"metadata": {"sensitivity": "confidential"}})]},
     "confidential_source"),
])
def test_write_rule(kw, reason):
    args = {"answer": ANSWER, "masked_message": Q, "grounding": GROUNDED, "intent": "factual",
            "blocked": False, "tools": [], "contexts": CHUNKS, **kw}
    assert write_refusal(**args) == reason


# ---------------------------------------------------------------------------------------------- /chat


async def test_second_identical_question_is_a_fast_cache_hit(state, cache):
    first = (await post({"message": Q})).json()
    assert first["cache_hit"] is False and cache.count() == 1
    llm_calls = len(state.script.calls)

    r = await post({"message": Q, "session_id": "s-hit"})
    body = r.json()
    assert body["cache_hit"] is True and body["model_used"] == "cache"
    assert len(state.script.calls) == llm_calls and len(state.router.calls) == 1  # no LLM, no router
    assert body["answer"] == first["answer"] and body["citations"] == first["citations"]
    assert body["grounding_score"] == 1.0 and body["tool_calls"] == []
    assert body["usage"]["prompt_tokens"] == 0 and body["latency_ms"] < 300
    assert "cache_lookup" in r.headers["Server-Timing"]

    async with client() as c:  # the hit is recorded in the session like any other turn
        hist = (await c.get("/sessions/s-hit")).json()
    assert [m["role"] for m in hist["messages"]] == ["user", "assistant"]
    assert hist["messages"][1]["content"] == ANSWER


async def test_follow_up_skips_the_lookup_and_is_written_under_the_rewrite(state, cache):
    state.router.make_plan = lambda m: plan("Who manages the Data-Platform team?" if "that team" in m else m)
    await post({"message": "Who owns the Ledger DB?", "session_id": "f1"})
    calls = len(state.script.calls)
    body = (await post({"message": "and who manages that team?", "session_id": "f1"})).json()
    assert body["cache_hit"] is False and len(state.script.calls) > calls  # the agent ran
    # The rewrite is self-contained, so a fresh session asking it directly hits.
    hit = await cache.lookup("Who manages the Data-Platform team?", None)
    assert hit is not None and hit.similarity > 0.99


async def test_web_answers_and_ungrounded_answers_are_not_cached(state, cache):
    web = AIMessage(content="", tool_calls=[tool_call("web_search", {"query": "openssl cve"})])
    state.script.replies = [web, "The latest OpenSSL CVE is X [1]."]
    state.agent = build_agent(state.script, router=state.router, retriever=state.retriever, judge=state.judge,
                              tools=[artifact_tool("web_search")])
    await post({"message": "What is the latest OpenSSL CVE?"})
    assert cache.count() == 0

    state.judge.verdicts = [[("Customer logs are retained for 13 months", False)]]
    await post({"message": Q})
    assert cache.count() == 0


async def test_use_cache_false_never_touches_the_cache(state, cache):
    state.settings.use_cache = False
    await post({"message": Q})
    await post({"message": Q})
    assert cache.count() == 0


class FakeGuardrails:
    """Records which rails ran; passes everything."""

    def __init__(self):
        self.calls = []

    async def check_input(self, message):
        self.calls.append("input")
        return Verdict(blocked=False, text=message)

    async def self_check_input(self, masked):
        self.calls.append("self_check")
        return Verdict(blocked=False, text=masked)

    async def check_output(self, masked_question, answer):
        from app.guardrails.actions import current

        self.calls.append(("output", current().grounding.score if current().grounding else None))
        return Verdict(blocked=False, text=answer)

    async def scan_chunks(self, chunks, name):
        return chunks

    async def scan_text(self, text, source):
        return text

    async def prescan_output(self, text):
        return None


async def test_hit_runs_output_rails_with_stored_grounding_and_no_self_check(state, cache):
    state.guardrails = FakeGuardrails()
    await cache.write(Q, None, entry())
    t0 = time.perf_counter()
    body = (await post({"message": Q})).json()
    assert body["cache_hit"] is True and (time.perf_counter() - t0) < 1.0
    assert state.guardrails.calls == ["input", ("output", 1.0)]


async def test_cache_errors_do_not_fail_the_turn(state, cache, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(cache, "lookup", boom)
    monkeypatch.setattr(cache, "write", boom)
    r = await post({"message": Q})
    assert r.status_code == 200 and r.json()["cache_hit"] is False and r.json()["answer"] == ANSWER
