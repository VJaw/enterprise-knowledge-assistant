"""One chat turn (§B3.2): input rails -> semantic cache -> agent -> output rails -> cache write, following
§B10.3's integration pattern (NeMo wraps the agent; the agent stays independent of NeMo).

- Input rails run first and are fast (local models). A refusal returns at once, without the agent.
- NeMo's `self check input` (an LLM call) runs alongside the agent on the masked message. If it blocks, the
  agent is cancelled and the refusal returned; if the agent finishes first its answer waits for the verdict.
  When LLM Guard's DeBERTa suspected an injection, the self-check runs *before* the agent instead (D-023).
- Output rails run on the agent's answer. The checkpointed answer is replaced by the final masked one, so the
  session history never holds a draft that the rails changed or blocked.
- PII placeholders from the user's own message are restored in the returned answer only (§B10.4).
- Semantic cache (§B12.1): looked up on the masked message after the input rails. A hit skips the agent and
  the LLM self-check (the local input rails have run, and the answer was vetted when it was written) and goes
  through the output rails with its stored grounding result, so no LLM is called (D-026). A grounded answer
  is written back after the output rails. Cache errors never fail a turn.

`USE_GUARDRAILS=false` (or no `app.state.guardrails`) runs the agent alone, as in Phase 7. `USE_CACHE=false`
(no `app.state.cache`) skips the cache.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import run_config, turn_input
from app.cache.semantic_cache import CachedAnswer, CacheHit, SemanticCache, depends_on_history, write_refusal
from app.core.types import GroundingResult, RetrievedChunk, ToolResult
from app.guardrails.actions import GuardState, guard_state
from app.guardrails.pii import get_masker
from app.guardrails.rails import Guardrails, Verdict
from app.observability.langfuse import get_client
from app.retrieval.context import cited_ids

log = logging.getLogger("uvicorn.error")


@dataclass
class TurnResult:
    answer: str  # what the client gets (user-supplied PII restored)
    masked_message: str  # what was logged and given to the agent
    masked_answer: str
    contexts: list[RetrievedChunk] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    grounding: GroundingResult | None = None
    model: str = ""
    steps: int = 0
    plan: object | None = None
    blocked: bool = False
    timings: dict[str, float] = field(default_factory=dict)  # ms: rails, agent, guardrail overhead
    intent: str | None = None
    cache: str = "off"  # hit | miss | skipped (follow-up) | error | off
    cache_similarity: float | None = None


async def _has_history(agent, session_id: str) -> bool:
    snapshot = await agent.aget_state(run_config(session_id))
    return bool((snapshot.values or {}).get("messages"))


async def _lookup(cache: SemanticCache, masked: str, filters: dict | None, history: bool
                  ) -> tuple[str, CacheHit | None]:
    if depends_on_history(masked, history):
        return "skipped", None
    try:
        hit = await cache.lookup(masked, filters)
    except Exception as e:  # noqa: BLE001 - the cache is an optimisation
        log.warning("cache lookup failed: %s", type(e).__name__)
        return "error", None
    return ("hit" if hit else "miss"), hit


async def _write(cache: SemanticCache, result: TurnResult, filters: dict | None, history: bool) -> None:
    cited = set(cited_ids(result.masked_answer))
    sources = [c for c in result.contexts if c.metadata.get("cite_id") in cited]
    reason = write_refusal(answer=result.masked_answer, masked_message=result.masked_message,
                           grounding=result.grounding, intent=result.intent, blocked=result.blocked,
                           tools=[r.name for r in result.tool_results], contexts=sources)
    if reason is not None:
        return
    # The history-resolved question: a follow-up is keyed by the router's rewrite (§B12.1).
    key = result.plan.rewritten_query if history and result.plan is not None else result.masked_message
    entry = CachedAnswer(answer=result.masked_answer, contexts=sources, grounding=result.grounding,
                         model=result.model, intent=result.intent)
    try:
        await cache.write(key, filters, entry)
    except Exception as e:  # noqa: BLE001
        log.warning("cache write failed: %s", type(e).__name__)


async def _store_answer(agent, session_id: str, text: str) -> None:
    """Make the checkpointed conversation end with `text`: replace this turn's answer, or answer the question
    a cancelled run left behind."""
    config = run_config(session_id)
    snapshot = await agent.aget_state(config)
    messages = (snapshot.values or {}).get("messages") or []
    if not messages:
        return
    last = messages[-1]
    if isinstance(last, AIMessage):
        if last.content != text:
            await agent.aupdate_state(config, {"messages": [AIMessage(text, id=last.id)]}, as_node="finalize")
    elif isinstance(last, HumanMessage):
        await agent.aupdate_state(config, {"messages": [AIMessage(text)]}, as_node="finalize")


async def _append_turn(agent, session_id: str, masked: str, answer: str) -> None:
    """A cache hit doesn't run the agent: record the exchange in the session so follow-ups see it."""
    await agent.aupdate_state(run_config(session_id), {"messages": [HumanMessage(masked), AIMessage(answer)]},
                              as_node="finalize")


def _refused(v: Verdict, masked: str, gs: GuardState) -> TurnResult:
    return TurnResult(answer=gs.pii.unmask_user(v.text), masked_message=masked, masked_answer=v.text,
                      flags=list(gs.flags), blocked=True, model="guardrails")


async def _serve_hit(agent, guardrails, gs: GuardState, session_id: str, masked: str, hit: CacheHit
                     ) -> TurnResult:
    e = hit.entry
    result = TurnResult(answer="", masked_message=masked, masked_answer=e.answer, contexts=e.contexts,
                        grounding=e.grounding, model="cache", intent=e.intent, cache="hit",
                        cache_similarity=round(hit.similarity, 4))
    if guardrails is not None:
        # The stored grounding result stands for this exact answer and these sources, so the groundedness
        # backstop and the output self-check don't call the LLM again (D-026).
        gs.contexts, gs.grounding, gs.intent = e.contexts, e.grounding, e.intent
        v = await guardrails.check_output(masked, e.answer)
        result.masked_answer, result.blocked = v.text, v.blocked
        if v.blocked:
            result.model = "guardrails"
    await _append_turn(agent, session_id, masked, result.masked_answer)
    result.answer = gs.pii.unmask_user(result.masked_answer)
    result.flags = list(gs.flags)
    return result


async def run_turn(*, agent, guardrails: Guardrails | None, session_id: str, message: str,
                   filters: dict | None = None, allow_web: bool = True,
                   cache: SemanticCache | None = None) -> TurnResult:
    gs = GuardState()
    with guard_state(gs):
        result = await _turn(agent, guardrails, cache, gs, session_id, message, filters, allow_web)
    result.timings = {**{k: round(v, 1) for k, v in gs.timings.items()}, **result.timings}
    if guardrails is not None:
        # Latency the rails add on the critical path. The grounding judge is the agent's own node (timed
        # separately as "groundedness").
        on_path = ("input", "self_check_before_agent", "untrusted", "output", "self_check_wait")
        overhead = sum(gs.timings.get(k, 0.0) for k in on_path)
        result.timings["guardrails"] = round(overhead, 1)
    return result


async def _run_agent(agent, masked: str, filters, allow_web: bool, session_id: str) -> dict:
    """The agent graph under one `agent` span (§B13.1): router, retrieval, steps, tools, synthesis, judge."""
    with get_client().start_as_current_observation(name="agent", as_type="agent",
                                                   input={"message": masked}) as span:
        out = await agent.ainvoke(turn_input(masked, filters, allow_web), config=run_config(session_id))
        span.update(output={"model": out.get("model"), "steps": out.get("steps"),
                            "tools": [r.name for r in out.get("tool_results", [])]})
    return out


async def _turn(agent, guardrails, cache, gs, session_id, message, filters, allow_web) -> TurnResult:
    masked = message
    check: asyncio.Task | None = None
    suspected = False
    if guardrails is not None:
        v = await guardrails.check_input(message)
        if v.blocked:  # the message may not have reached the masking rail: mask it for the trace here
            return _refused(v, get_masker().mask(message, gs.pii, from_user=True)[0], gs)
        masked, suspected = v.text, v.suspected
        if suspected:  # DeBERTa hit: the LLM check decides before anything runs
            t0 = time.perf_counter()
            sc = await guardrails.self_check_input(masked)
            gs.timings["self_check_before_agent"] = (time.perf_counter() - t0) * 1000
            if sc.blocked:
                return _refused(sc, masked, gs)

    status, history = "off", False
    if cache is not None:
        t0 = time.perf_counter()
        history = await _has_history(agent, session_id)
        status, hit = await _lookup(cache, masked, filters, history)
        gs.timings["cache_lookup"] = (time.perf_counter() - t0) * 1000
        if hit is not None:
            return await _serve_hit(agent, guardrails, gs, session_id, masked, hit)

    if guardrails is not None and not suspected:
        check = asyncio.create_task(guardrails.self_check_input(masked))

    t0 = time.perf_counter()
    run = asyncio.create_task(_run_agent(agent, masked, filters, allow_web, session_id))
    try:
        if check is not None:
            await asyncio.wait({run, check}, return_when=asyncio.FIRST_COMPLETED)
            if check.done() and check.result().blocked:
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run
                sc = check.result()
                await _store_answer(agent, session_id, sc.text)
                return _refused(sc, masked, gs)
        out = await run
    except BaseException:
        run.cancel()
        if check is not None:
            check.cancel()
        raise
    agent_ms = (time.perf_counter() - t0) * 1000
    if check is not None:
        t1 = time.perf_counter()
        sc = await check
        gs.timings["self_check_wait"] = (time.perf_counter() - t1) * 1000
        if sc.blocked:
            await _store_answer(agent, session_id, sc.text)
            return _refused(sc, masked, gs)

    answer = out["answer"]
    flags = list(out.get("flags", []))
    plan = out.get("plan")
    result = TurnResult(answer=answer, masked_message=masked, masked_answer=answer, contexts=out["contexts"],
                        tool_results=out["tool_results"], grounding=out.get("grounding"), model=out["model"],
                        steps=out.get("steps", 0), plan=plan, timings={"agent": round(agent_ms, 1)},
                        intent=plan.intent if plan is not None else None, cache=status)
    if guardrails is not None:
        gs.contexts, gs.grounding = out["contexts"], out.get("grounding")
        gs.intent = plan.intent if plan is not None else None
        v = await guardrails.check_output(masked, answer)
        result.masked_answer = v.text
        result.blocked = v.blocked
        result.grounding = gs.grounding
        if v.blocked:
            result.model = "guardrails"
        await _store_answer(agent, session_id, v.text)
    result.answer = gs.pii.unmask_user(result.masked_answer)
    result.flags = flags + [f for f in gs.flags if f not in flags]
    if cache is not None:
        await _write(cache, result, filters, history)
    return result
