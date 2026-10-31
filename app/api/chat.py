"""POST /chat (Phase 7): the LangGraph agent (§B9) with the session's checkpoint (`thread_id = session_id`).

`tool_calls`, `model_used`, `grounding_score` and `usage` come from the agent run. Usage sums every LLM call
in the request (router, agent steps, synthesis, grounding judge) by model. `guardrail_flags` carries retrieval
and agent flags (`graph_unavailable`, `agent_step_limit`, `regenerated_ungrounded`, ...) until Phase 8 adds
the rails."""
from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from langchain_core.callbacks import get_usage_metadata_callback

from app.agent.graph import run_config, turn_input
from app.api.schemas import ChatRequest, ChatResponse, ToolCall
from app.observability.langfuse import current_trace_id, get_client, propagate_attributes
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


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    state = request.app.state
    t0 = time.perf_counter()
    session_id = req.session_id or str(uuid.uuid4())

    span_input = {"message": req.message, "filters": req.filters, "allow_web": req.allow_web}
    with get_client().start_as_current_observation(name="chat", as_type="span", input=span_input) as root, \
            propagate_attributes(session_id=session_id, trace_name="chat", tags=["phase-7"]):
        with get_usage_metadata_callback() as usage_cb:
            try:
                out = await asyncio.wait_for(
                    state.agent.ainvoke(turn_input(req.message, req.filters, req.allow_web),
                                        config=run_config(session_id)),
                    timeout=REQUEST_BUDGET_S)
            except ValueError as e:  # bad filter keys
                raise HTTPException(status_code=422, detail=str(e)) from e
            except TimeoutError as e:
                raise HTTPException(status_code=504, detail="UPSTREAM_TIMEOUT") from e
        answer = out["answer"]
        citations = citations_for(answer, out["contexts"])
        tool_calls = [ToolCall(name=r.name, args=r.args, duration_ms=r.duration_ms, status=r.status)
                      for r in out["tool_results"]]
        grounding = out.get("grounding")
        usage = usage_summary(usage_cb.usage_metadata, out.get("steps", 0))
        trace_id = current_trace_id()
        cited = [f"{c.id}:{c.doc_id or c.title}" for c in citations]
        root.update(output={"answer": answer, "citations": cited},
                    metadata={"plan": out["plan"].model_dump() if out.get("plan") else None,
                              "model": out["model"], "flags": out["flags"],
                              "tools": [f"{t.name}:{t.status}" for t in tool_calls],
                              "grounding": grounding.model_dump() if grounding else None, "usage": usage})

    return ChatResponse(
        session_id=session_id, answer=answer, citations=citations, tool_calls=tool_calls,
        guardrail_flags=out["flags"], grounding_score=grounding.score if grounding else None,
        model_used=out["model"], cache_hit=False, latency_ms=int((time.perf_counter() - t0) * 1000),
        usage=usage, trace_id=trace_id,
    )
