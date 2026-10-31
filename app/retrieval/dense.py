"""Dense retrieval over the Qdrant `documents` collection (§B6.2, §B8.2), with metadata filters."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from app.core.types import RetrievedChunk
from app.observability.langfuse import observe, update_span
from app.retrieval.embeddings import embed_query

COLLECTION = "documents"
FILTERABLE = {"doc_type", "domain", "sensitivity", "doc_id", "entities"}


def build_filter(filters: dict | None) -> Filter | None:
    if not filters:
        return None
    unknown = set(filters) - FILTERABLE
    if unknown:
        raise ValueError(f"unsupported filter keys: {sorted(unknown)}; allowed: {sorted(FILTERABLE)}")

    def match(v):
        return MatchAny(any=list(v)) if isinstance(v, list | tuple) else MatchValue(value=v)

    return Filter(must=[FieldCondition(key=k, match=match(v)) for k, v in filters.items()])


def to_chunk(point, source_type: str = "document") -> RetrievedChunk:
    p = dict(point.payload)
    return RetrievedChunk(chunk_id=p.pop("chunk_id"), doc_id=p["doc_id"], text=p.pop("text"),
                          score=float(point.score or 0.0), source_type=source_type, metadata=p)


class DenseRetriever:
    """Implements `app.core.types.Retriever`."""

    def __init__(self, client: QdrantClient, embed: Callable[[str], list[float]] = embed_query,
                 collection: str = COLLECTION):
        self.client = client
        self.embed = embed
        self.collection = collection

    def search(self, query: str, flt: Filter | None, k: int) -> list[RetrievedChunk]:
        res = self.client.query_points(self.collection, query=self.embed(query), query_filter=flt, limit=k,
                                       with_payload=True)
        return [to_chunk(p) for p in res.points]

    @observe(name="qdrant.dense", as_type="retriever", capture_output=False)
    async def retrieve(self, query: str, filters: dict | None = None, k: int = 5) -> list[RetrievedChunk]:
        flt = build_filter(filters)
        # Embedding + embedded Qdrant are CPU-bound and synchronous: keep them off the event loop.
        chunks = await asyncio.to_thread(self.search, query, flt, k)
        update_span(
            output=[{"chunk_id": c.chunk_id, "score": round(c.score, 4)} for c in chunks],
            metadata={"k": k, "filters": filters, "top_score": chunks[0].score if chunks else None},
        )
        return chunks
