"""POST /ingest (§B11.1): re-index the corpus from data/raw, protected by the `X-Admin-Token` header (§B17).

Runs the same steps as `make ingest` minus SQLite and the graph (raw -> docs.jsonl -> chunks -> Qdrant +
BM25), on the app's own Qdrant client (embedded Qdrant locks its folder). The index build bumps
`corpus_version`, which invalidates the semantic cache. One re-index at a time; requests in flight during
the rebuild may see an empty `documents` collection for a few seconds. The graph is reloaded with
`make graph`, not here.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Header, Request

from app.api.errors import ApiError
from app.api.schemas import ErrorResponse, IngestResponse

log = logging.getLogger("uvicorn.error")
router = APIRouter(tags=["admin"])
_lock = asyncio.Lock()


def reindex(client) -> dict:
    from ingestion.run import index_documents, process_documents

    docs = process_documents()
    stats = index_documents(docs, client)
    return {"documents": len(docs), "chunks": stats["qdrant_points"],
            "corpus_version": stats["corpus_version"]}


def _authorised(request: Request, token: str | None) -> bool:
    expected = request.app.state.settings.admin_token.get_secret_value()
    return bool(token) and hmac.compare_digest(token.encode(), expected.encode())


@router.post("/ingest", response_model=IngestResponse, responses={
    401: {"model": ErrorResponse, "description": "UNAUTHORIZED: missing or wrong X-Admin-Token"},
    409: {"model": ErrorResponse, "description": "CONFLICT: a re-index is already running"}})
async def ingest(request: Request,
                 x_admin_token: Annotated[str | None, Header(description="ADMIN_TOKEN")] = None
                 ) -> IngestResponse:
    """Re-index documents and invalidate the semantic cache."""
    if not _authorised(request, x_admin_token):
        raise ApiError("UNAUTHORIZED", 401, "Missing or invalid X-Admin-Token.")
    if _lock.locked():
        raise ApiError("CONFLICT", 409, "A re-index is already running.")
    state = request.app.state
    async with _lock:
        t0 = time.perf_counter()
        stats = await asyncio.to_thread(reindex, state.qdrant)
        retriever = getattr(state, "retriever", None)
        if getattr(retriever, "bm25", None) is not None:  # the BM25 index is held in memory
            from app.retrieval.bm25 import BM25Retriever

            retriever.bm25 = await asyncio.to_thread(BM25Retriever)
        dropped = 0
        cache = getattr(state, "cache", None)
        if cache is not None:
            before = await asyncio.to_thread(cache.count)
            await asyncio.to_thread(cache.reload_version)
            dropped = before - await asyncio.to_thread(cache.count)
        if getattr(state, "guardrails", None) is not None:  # re-scan the new passages in the background
            from app.guardrails.actions import prefill_corpus

            state.guard_prefill = asyncio.create_task(asyncio.to_thread(prefill_corpus))
        ms = int((time.perf_counter() - t0) * 1000)
    log.warning("ingest: %s docs, %s chunks, corpus_version %s, %s cache entries dropped, %s ms",
                stats["documents"], stats["chunks"], stats["corpus_version"], dropped, ms)
    return IngestResponse(**stats, cache_entries_dropped=dropped, duration_ms=ms)
