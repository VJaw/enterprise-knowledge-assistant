"""GET /health: async versions of the Phase 0 service checks (§B4.3, §B11.1)."""
import asyncio

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

CHECK_TIMEOUT_S = 8.0
TAVILY_USAGE_URL = "https://api.tavily.com/usage"  # validates the key without spending search credits


async def check_openai(state) -> None:
    ids = {m.id async for m in state.openai.models.list()}
    missing = [m for m in (state.settings.model_strong, state.settings.model_fast) if m not in ids]
    if missing:
        raise RuntimeError(f"models not available: {missing}")


async def check_neo4j(state) -> None:
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


CHECKS = {
    "openai": check_openai,
    "neo4j": check_neo4j,
    "langfuse": check_langfuse,
    "tavily": check_tavily,
}


async def _run(fn, state) -> str:
    try:
        await asyncio.wait_for(fn(state), timeout=CHECK_TIMEOUT_S)
        return "ok"
    except Exception as e:  # noqa: BLE001
        # Exception type only: messages can echo request details, and keys must never leak.
        if isinstance(e, httpx.HTTPStatusError):
            return f"error: HTTP {e.response.status_code}"
        return f"error: {type(e).__name__}"


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    state = request.app.state
    statuses = await asyncio.gather(*(_run(fn, state) for fn in CHECKS.values()))
    body = dict(zip(CHECKS, statuses, strict=True))
    ok = all(s == "ok" for s in statuses)
    return JSONResponse(body, status_code=200 if ok else 503)
