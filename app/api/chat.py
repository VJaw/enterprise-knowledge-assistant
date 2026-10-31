"""POST /chat: one turn of app/pipeline.py (input rails -> semantic cache -> agent -> output rails) with the
session's checkpoint (`thread_id = session_id`).

`tool_calls`, `model_used`, `grounding_score` and `usage` come from the agent run. Usage sums every LLM call
in the request (guard self-checks, router, agent steps, synthesis, grounding judge) by model; a cache hit
calls none (`model_used: "cache"`). `guardrail_flags` carries the rail flags (`prompt_injection`,
`pii_masked_input`, `GUARDRAIL_BLOCKED`, ...) and the retrieval / agent flags (`graph_unavailable`,
`agent_step_limit`, `regenerated_ungrounded`, ...). Only masked text reaches the trace (§B10.4). Per-stage
times go out in a `Server-Timing` header.

Rate limit (§B11.3): RATE_LIMIT (default 30/minute) per session, or per client IP for requests without a
`session_id`, counted in slowapi's limiter storage.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid

import openai
from fastapi import APIRouter, Request, Response
from langchain_core.callbacks import get_usage_metadata_callback
from limits import parse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.api.errors import ApiError
from app.api.schemas import ChatRequest, ChatResponse, ErrorResponse, ToolCall
from app.observability import metrics
from app.observability.langfuse import current_trace_id, get_client, propagate_attributes, set_trace_tags
from app.pipeline import TurnResult, run_turn
from app.retrieval.context import citations_for
from app.retrieval.dense import FilterError

log = logging.getLogger("uvicorn.error")
router = APIRouter(tags=["chat"])
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")

# USD per 1M tokens (input, output); indicative, see §B12.2. Longest prefix wins (gpt-4o-mini before gpt-4o).
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00)}
REQUEST_BUDGET_S = 45.0  # §B12.3
# Flags that name the rail behind a refusal, for the `blocked:*` trace tags.
BLOCK_FLAGS = ("prompt_injection", "toxic_input", "topic_", "self_check_input_blocked", "toxic_output",
               "banned_topic_output", "secret_leak")


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
        cost = (pin * price_in + pout * price_out) / 1e6
        out["prompt_tokens"] += pin
        out["completion_tokens"] += pout
        out["est_cost_usd"] += cost
        out["by_model"][model] = {"prompt_tokens": pin, "completion_tokens": pout,
                                  "est_cost_usd": round(cost, 6)}
    out["est_cost_usd"] = round(out["est_cost_usd"], 6)
    return out


def server_timing(timings: dict[str, float]) -> str:
    return ", ".join(f"{k.replace('.', '_')};dur={v:.0f}" for k, v in timings.items())


def anon_user(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return "anon-" + hashlib.sha256(host.encode()).hexdigest()[:12]


def trace_tags(turn: TurnResult) -> list[str]:
    tags = [f"intent:{turn.intent or 'none'}", f"model:{turn.model}", f"cache:{turn.cache}"]
    if turn.blocked:
        reasons = [f.split(":")[0] for f in turn.flags if f.startswith(BLOCK_FLAGS)] or ["output_self_check"]
        tags += [f"blocked:{r}" for r in reasons]
    return tags


def check_rate_limit(request: Request, req: ChatRequest) -> None:
    item = parse(request.app.state.settings.rate_limit)
    key = f"session:{req.session_id}" if req.session_id else f"ip:{get_remote_address(request)}"
    if not limiter.limiter.hit(item, "chat", key):
        reset = limiter.limiter.get_window_stats(item, "chat", key).reset_time
        retry = max(1, int(reset - time.time()))
        raise ApiError("RATE_LIMITED", 429, f"Rate limit of {item} exceeded; retry in {retry} s.",
                       headers={"Retry-After": str(retry)})


@router.post("/chat", response_model=ChatResponse, responses={
    422: {"model": ErrorResponse, "description": "VALIDATION_ERROR: bad body or filter key"},
    429: {"model": ErrorResponse, "description": "RATE_LIMITED: too many requests for this session / IP"},
    500: {"model": ErrorResponse, "description": "INTERNAL_ERROR, captured in Langfuse under trace_id"},
    504: {"model": ErrorResponse, "description": "UPSTREAM_TIMEOUT: LLM/tool timeout or 45 s budget"}})
async def chat(req: ChatRequest, request: Request, response: Response) -> ChatResponse:
    """Ask a question. Guardrail refusals are normal 200 responses with `GUARDRAIL_BLOCKED` in
    `guardrail_flags` (§B11.3); repeated questions may be served from the semantic cache (`cache_hit`)."""
    state = request.app.state
    check_rate_limit(request, req)
    t0 = time.perf_counter()
    session_id = req.session_id or str(uuid.uuid4())
    guardrails = getattr(state, "guardrails", None) if state.settings.use_guardrails else None
    cache = getattr(state, "cache", None) if state.settings.use_cache else None

    with get_client().start_as_current_observation(name="chat", as_type="span") as root, \
            propagate_attributes(session_id=session_id, user_id=anon_user(request), trace_name="chat",
                                 tags=["chat"]):
        trace_id = current_trace_id()
        with get_usage_metadata_callback() as usage_cb:
            try:
                turn = await asyncio.wait_for(
                    run_turn(agent=state.agent, guardrails=guardrails, cache=cache, session_id=session_id,
                             message=req.message, filters=req.filters, allow_web=req.allow_web),
                    timeout=REQUEST_BUDGET_S)
            except FilterError as e:
                root.update(level="WARNING", status_message="VALIDATION_ERROR")
                raise ApiError("VALIDATION_ERROR", 422, str(e), trace_id) from e
            except (TimeoutError, openai.APITimeoutError) as e:
                root.update(level="ERROR", status_message="UPSTREAM_TIMEOUT")
                raise ApiError("UPSTREAM_TIMEOUT", 504, "The language model or a tool timed out.",
                               trace_id) from e
            except Exception as e:
                log.exception("chat turn failed")
                root.update(level="ERROR", status_message=type(e).__name__)
                raise ApiError("INTERNAL_ERROR", 500, "Internal error. Quote the trace id when reporting.",
                               trace_id) from e
        citations = citations_for(turn.masked_answer, turn.contexts)
        tool_calls = [ToolCall(name=r.name, args=r.args, duration_ms=r.duration_ms, status=r.status)
                      for r in turn.tool_results]
        usage = usage_summary(usage_cb.usage_metadata, turn.steps)
        cited = [f"{c.id}:{c.doc_id or c.title}" for c in citations]
        root.update(input={"message": turn.masked_message, "filters": req.filters,
                           "allow_web": req.allow_web},
                    output={"answer": turn.masked_answer, "citations": cited},
                    metadata={"plan": turn.plan.model_dump() if turn.plan else None, "model": turn.model,
                              "flags": turn.flags, "blocked": turn.blocked, "timings_ms": turn.timings,
                              "cache": turn.cache, "cache_similarity": turn.cache_similarity,
                              "tools": [f"{t.name}:{t.status}" for t in tool_calls],
                              "grounding": turn.grounding.model_dump() if turn.grounding else None,
                              "usage": usage})
        set_trace_tags(root, ["chat", *trace_tags(turn)])

    latency_ms = int((time.perf_counter() - t0) * 1000)
    response.headers["Server-Timing"] = server_timing({**turn.timings, "total": latency_ms})
    metrics.record_chat(cache=turn.cache, model=turn.model, latency_s=latency_ms / 1000, flags=turn.flags,
                        blocked=turn.blocked, usage=usage)
    return ChatResponse(
        session_id=session_id, answer=turn.answer, citations=citations, tool_calls=tool_calls,
        guardrail_flags=turn.flags, grounding_score=turn.grounding.score if turn.grounding else None,
        model_used=turn.model, cache_hit=turn.cache == "hit", latency_ms=latency_ms, usage=usage,
        trace_id=trace_id,
    )
