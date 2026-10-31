"""GET /health and GET /metrics.

/health: deep readiness checks (§B4.3, §B11.1), run concurrently with an 8 s timeout each.

Critical (any failure -> 503 `status: "down"`): OpenAI (both routed models listed), Qdrant (`documents` has
points), SQLite (the read-only database answers), models (embedder, re-ranker and guard models loaded).
Degradable (failure -> 200 `status: "degraded"`): the graph (Aura unreachable or marked unavailable ->
`graph: "degraded"`, retrieval runs dense + BM25, §B12.3), Langfuse, Tavily, MCP.
"""
import asyncio
import sqlite3
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.observability import metrics

router = APIRouter(tags=["ops"])

CHECK_TIMEOUT_S = 8.0
TAVILY_USAGE_URL = "https://api.tavily.com/usage"  # validates the key without spending search credits


async def check_openai(state) -> None:
    ids = {m.id async for m in state.openai.models.list()}
    missing = [m for m in (state.settings.model_strong, state.settings.model_fast) if m not in ids]
    if missing:
        raise RuntimeError(f"models not available: {missing}")


async def check_qdrant(state) -> None:
    from app.retrieval.dense import COLLECTION

    n = await asyncio.to_thread(lambda: state.qdrant.count(COLLECTION, exact=False).count)
    if n == 0:
        raise RuntimeError("documents collection is empty")


def _sqlite_ro(path: str) -> None:
    p = Path(path)
    ro = p.with_name(f"{p.stem}_ro{p.suffix}")  # data/acme_ro.db, the copy the agent's tools read
    with sqlite3.connect(f"file:{ro if ro.exists() else p}?mode=ro", uri=True, timeout=2) as conn:
        conn.execute("SELECT count(*) FROM tickets").fetchone()


async def check_sqlite(state) -> None:
    await asyncio.to_thread(_sqlite_ro, state.settings.sqlite_path)


async def check_models(state) -> None:
    from app.retrieval.embeddings import get_embedder
    from app.retrieval.rerank import get_cross_encoder

    loaded = {"embedder": get_embedder.cache_info().currsize > 0}
    if state.settings.use_rerank:
        loaded["reranker"] = get_cross_encoder.cache_info().currsize > 0
    if state.settings.use_guardrails:
        loaded["guardrails"] = getattr(state, "guardrails", None) is not None
    missing = [k for k, ok in loaded.items() if not ok]
    if missing:
        raise RuntimeError(f"not loaded: {missing}")


async def check_graph(state) -> None:
    if not getattr(state, "graph_available", True):
        raise RuntimeError("graph marked unavailable")
    await state.neo4j.verify_connectivity()


async def check_langfuse(state) -> None:
    if not await asyncio.to_thread(state.langfuse.auth_check):
        raise RuntimeError("auth_check returned False")


async def check_tavily(state) -> None:
    r = await state.http.get(
        TAVILY_USAGE_URL,
        headers={"Authorization": f"Bearer {state.settings.tavily_api_key.get_secret_value()}"},
    )
    r.raise_for_status()


async def check_mcp(state) -> None:
    mcp = getattr(state, "mcp", None)
    if mcp is not None and not getattr(mcp, "tools", None):
        raise RuntimeError("no MCP tools bound")


CRITICAL = {"openai": check_openai, "qdrant": check_qdrant, "sqlite": check_sqlite, "models": check_models}
DEGRADABLE = {"graph": check_graph, "langfuse": check_langfuse, "tavily": check_tavily, "mcp": check_mcp}


async def _run(fn, state) -> str:
    try:
        await asyncio.wait_for(fn(state), timeout=CHECK_TIMEOUT_S)
        return "ok"
    except Exception as e:  # noqa: BLE001
        # Exception type only: messages can echo request details, and keys must never leak.
        if isinstance(e, httpx.HTTPStatusError):
            return f"error: HTTP {e.response.status_code}"
        return f"error: {type(e).__name__}"


@router.get("/health", responses={
    200: {"description": "Ready (`status: ok`), or serving with a degraded dependency (`status: degraded`)",
          "content": {"application/json": {"example": {
              "status": "degraded", "openai": "ok", "qdrant": "ok", "sqlite": "ok", "models": "ok",
              "graph": "degraded", "langfuse": "ok", "tavily": "ok", "mcp": "ok"}}}},
    503: {"description": "A critical dependency failed (`status: down`)"}})
async def health(request: Request) -> JSONResponse:
    """Liveness/readiness of Qdrant, Neo4j Aura, SQLite, the local models and the external APIs."""
    state = request.app.state
    checks = {**CRITICAL, **DEGRADABLE}
    if not state.settings.use_graph:
        checks.pop("graph")
    results = await asyncio.gather(*(_run(fn, state) for fn in checks.values()))
    statuses = dict(zip(checks, results, strict=True))
    if "graph" in statuses and statuses["graph"] != "ok":
        statuses["graph"] = "degraded"
    down = any(statuses[k] != "ok" for k in CRITICAL)
    degraded = any(statuses[k] != "ok" for k in statuses if k not in CRITICAL)
    status = "down" if down else "degraded" if degraded else "ok"
    if not state.settings.use_graph:
        statuses["graph"] = "disabled"
    return JSONResponse({"status": status, **statuses}, status_code=503 if down else 200)


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics() -> PlainTextResponse:
    """Prometheus text format: HTTP requests and latency by route, chat turns by cache result and model,
    end-to-end latency histograms, guardrail triggers by flag, refusals, errors by code, tokens and estimated
    cost by model."""
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")
