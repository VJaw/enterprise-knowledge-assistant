"""One chat turn (§B3.2): input rails -> (cache hook) -> agent -> output rails, following §B10.3's integration
pattern (NeMo wraps the agent; the agent stays independent of NeMo).

- Input rails run first and are fast (local models). A refusal returns at once, without the agent.
- NeMo's `self check input` (an LLM call) runs alongside the agent on the masked message. If it blocks, the
  agent is cancelled and the refusal returned; if the agent finishes first its answer waits for the verdict.
  When LLM Guard's DeBERTa suspected an injection, the self-check runs *before* the agent instead (D-023).
- Output rails run on the agent's answer. The checkpointed answer is replaced by the final masked one, so the
  session history never holds a draft that the rails changed or blocked.
- PII placeholders from the user's own message are restored in the returned answer only (§B10.4).

`USE_GUARDRAILS=false` (or no `app.state.guardrails`) runs the agent alone, as in Phase 7.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import run_config, turn_input
from app.core.types import GroundingResult, RetrievedChunk, ToolResult
from app.guardrails.actions import GuardState, guard_state
from app.guardrails.pii import get_masker
from app.guardrails.rails import Guardrails, Verdict


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


async def cache_lookup(masked_message: str, filters: dict | None) -> None:
    """Semantic cache hook (§B12.1): a no-op until Phase 9."""
    return None


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


def _refused(v: Verdict, masked: str, gs: GuardState) -> TurnResult:
    return TurnResult(answer=gs.pii.unmask_user(v.text), masked_message=masked, masked_answer=v.text,
                      flags=list(gs.flags), blocked=True, model="guardrails")


async def run_turn(*, agent, guardrails: Guardrails | None, session_id: str, message: str,
                   filters: dict | None = None, allow_web: bool = True) -> TurnResult:
    gs = GuardState()
    with guard_state(gs):
        result = await _turn(agent, guardrails, gs, session_id, message, filters, allow_web)
    result.timings = {**{k: round(v, 1) for k, v in gs.timings.items()}, **result.timings}
    if guardrails is not None:
        # Latency the rails add on the critical path. The grounding judge is the agent's own node (timed
        # separately as "groundedness").
        on_path = ("input", "self_check_before_agent", "untrusted", "output", "self_check_wait")
        overhead = sum(gs.timings.get(k, 0.0) for k in on_path)
        result.timings["guardrails"] = round(overhead, 1)
    return result


async def _turn(agent, guardrails, gs, session_id, message, filters, allow_web) -> TurnResult:
    masked = message
    check: asyncio.Task | None = None
    if guardrails is not None:
        v = await guardrails.check_input(message)
        if v.blocked:  # the message may not have reached the masking rail: mask it for the trace here
            return _refused(v, get_masker().mask(message, gs.pii, from_user=True)[0], gs)
        masked = v.text
        if v.suspected:  # DeBERTa hit: the LLM check decides before anything runs
            t0 = time.perf_counter()
            sc = await guardrails.self_check_input(masked)
            gs.timings["self_check_before_agent"] = (time.perf_counter() - t0) * 1000
            if sc.blocked:
                return _refused(sc, masked, gs)
        else:
            check = asyncio.create_task(guardrails.self_check_input(masked))

    await cache_lookup(masked, filters)

    t0 = time.perf_counter()
    run = asyncio.create_task(agent.ainvoke(turn_input(masked, filters, allow_web),
                                            config=run_config(session_id)))
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
                        steps=out.get("steps", 0), plan=plan, timings={"agent": round(agent_ms, 1)})
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
    return result
