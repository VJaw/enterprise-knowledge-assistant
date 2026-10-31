"""POST /chat: one turn of app/pipeline.py (input rails -> agent -> output rails) with the session's
checkpoint (`thread_id = session_id`).

`tool_calls`, `model_used`, `grounding_score` and `usage` come from the agent run. Usage sums every LLM call
in the request (guard self-checks, router, agent steps, synthesis, grounding judge) by model.
`guardrail_flags` carries the rail flags (`prompt_injection`, `pii_masked_input`, `GUARDRAIL_BLOCKED`, ...)
and the retrieval / agent flags (`graph_unavailable`, `agent_step_limit`, `regenerated_ungrounded`, ...).
Only masked text reaches the trace (§B10.4). Per-stage times go out in a `Server-Timing` header."""
from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from langchain_core.callbacks import get_usage_metadata_callback

from app.api.schemas import ChatRequest, ChatResponse, ToolCall
from app.observability.langfuse import current_trace_id, get_client, propagate_attributes
from app.pipeline import run_turn
from app.retrieval.context import citations_for

router = APIRouter()

# USD per 1M tokens (input, output); indicative, see §B12.2. Longest prefix wins (gpt-4o-mini before gpt-4o).
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00)}
REQUEST_BUDGET_S = 45.0  # §B12.3


def price_of(model: str) -> tuple[float, float]:
    for name in sorted(PRICES, key=len, reverse=True):
        if model.startswith(name):
            return PRICES[name]
    return 0.0, 0.0


def usage_summary(by_model: dict, steps: int) -> dict:
    """Totals plus a per-model breakdown; `by_model` is `UsageMetadataCallbackHandler.usage_metadata`."""
    out = {"prompt_tokens": 0, "completion_tokens": 0, "est_cost_usd": 0.0, "by_model": {},
           "agent_steps": steps}
    for model, u in by_model.items():
        pin, pout = u.get("input_tokens", 0), u.get("output_tokens", 0)
        price_in, price_out = price_of(model)
        out["prompt_tokens"] += pin
        out["completion_tokens"] += pout
        out["est_cost_usd"] += (pin * price_in + pout * price_out) / 1e6
        out["by_model"][model] = {"prompt_tokens": pin, "completion_tokens": pout}
    out["est_cost_usd"] = round(out["est_cost_usd"], 6)
    return out


def server_timing(timings: dict[str, float]) -> str:
    return ", ".join(f"{k.replace('.', '_')};dur={v:.0f}" for k, v in timings.items())


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request, response: Response) -> ChatResponse:
    state = request.app.state
    t0 = time.perf_counter()
    session_id = req.session_id or str(uuid.uuid4())
    guardrails = getattr(state, "guardrails", None) if state.settings.use_guardrails else None

    with get_client().start_as_current_observation(name="chat", as_type="span") as root, \
            propagate_attributes(session_id=session_id, trace_name="chat", tags=["phase-8"]):
        with get_usage_metadata_callback() as usage_cb:
            try:
                turn = await asyncio.wait_for(
                    run_turn(agent=state.agent, guardrails=guardrails, session_id=session_id,
                             message=req.message, filters=req.filters, allow_web=req.allow_web),
                    timeout=REQUEST_BUDGET_S)
            except ValueError as e:  # bad filter keys
                raise HTTPException(status_code=422, detail=str(e)) from e
            except TimeoutError as e:
                raise HTTPException(status_code=504, detail="UPSTREAM_TIMEOUT") from e
        citations = citations_for(turn.masked_answer, turn.contexts)
        tool_calls = [ToolCall(name=r.name, args=r.args, duration_ms=r.duration_ms, status=r.status)
                      for r in turn.tool_results]
        usage = usage_summary(usage_cb.usage_metadata, turn.steps)
        trace_id = current_trace_id()
        cited = [f"{c.id}:{c.doc_id or c.title}" for c in citations]
        root.update(input={"message": turn.masked_message, "filters": req.filters,
                           "allow_web": req.allow_web},
                    output={"answer": turn.masked_answer, "citations": cited},
                    metadata={"plan": turn.plan.model_dump() if turn.plan else None, "model": turn.model,
                              "flags": turn.flags, "blocked": turn.blocked, "timings_ms": turn.timings,
                              "tools": [f"{t.name}:{t.status}" for t in tool_calls],
                              "grounding": turn.grounding.model_dump() if turn.grounding else None,
                              "usage": usage})

    latency_ms = int((time.perf_counter() - t0) * 1000)
    response.headers["Server-Timing"] = server_timing({**turn.timings, "total": latency_ms})
    return ChatResponse(
        session_id=session_id, answer=turn.answer, citations=citations, tool_calls=tool_calls,
        guardrail_flags=turn.flags, grounding_score=turn.grounding.score if turn.grounding else None,
        model_used=turn.model, cache_hit=False, latency_ms=latency_ms, usage=usage, trace_id=trace_id,
    )
