"""Hybrid retrieval (§B8.2): dense + BM25 + graph in parallel -> RRF -> cross-encoder -> top 6 + graph facts.

Feature flags (`USE_BM25`, `USE_GRAPH`, `USE_RERANK`) switch branches off for ablations. If Aura is
unavailable (at startup, or the graph branch errors / exceeds its budget) retrieval continues with
dense + BM25 and the result carries the `graph_unavailable` flag (§B12.3).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.types import RetrievedChunk
from app.observability.langfuse import observe, update_span
from app.retrieval.bm25 import BM25Retriever
from app.retrieval.dense import DenseRetriever, build_filter
from app.retrieval.fusion import fuse, rrf
from app.retrieval.graph import GraphResult, GraphRetriever, GraphUnavailable
from app.retrieval.rerank import TOP_K, TOP_N, CrossEncoderReranker
from app.retrieval.router import QueryPlan

BRANCH_K = 20  # dense and BM25 candidates each (§B8.2)


@dataclass
class HybridResult:
    chunks: list[RetrievedChunk]  # top document chunks, cited as [1]..[n]
    facts: list[RetrievedChunk] = field(default_factory=list)  # graph facts, cited as [G1]..[Gn]
    flags: list[str] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)


async def none() -> list:
    return []


async def timed(timings: dict, name: str, coro):
    t0 = time.perf_counter()
    try:
        return await coro
    finally:
        timings[name] = int((time.perf_counter() - t0) * 1000)


class HybridRetriever:
    def __init__(self, dense: DenseRetriever, bm25: BM25Retriever | None = None,
                 graph: GraphRetriever | None = None, reranker: CrossEncoderReranker | None = None, *,
                 use_bm25: bool = True, use_graph: bool = True, use_rerank: bool = True,
                 graph_available: Callable[[], bool] = lambda: True):
        self.dense, self.bm25, self.graph, self.reranker = dense, bm25, graph, reranker
        self.use_bm25 = use_bm25 and bm25 is not None
        self.use_graph = use_graph and graph is not None
        self.use_rerank = use_rerank and reranker is not None
        self.graph_available = graph_available

    async def _graph(self, plan: QueryPlan, filters: dict | None, flags: list[str]) -> GraphResult:
        if not self.graph_available():
            flags.append("graph_unavailable")
            return GraphResult()
        try:
            return await self.graph.retrieve(plan.rewritten_query, plan.entities, filters)
        except GraphUnavailable:
            flags.append("graph_unavailable")
            return GraphResult()

    @observe(name="retrieval.hybrid", as_type="retriever", capture_output=False)
    async def search(self, plan: QueryPlan, filters: dict | None = None, k: int = TOP_K) -> HybridResult:
        """Dense, BM25 and graph run concurrently. Re-ranking starts on the dense + BM25 candidates as soon as
        they are in, while the graph branch is still waiting on Aura; once it returns, only the graph-anchored
        newcomers to the fused top 30 are scored. Pair scores are independent, so the result is the same as
        re-ranking the final fused top 30 in one go."""
        q = plan.rewritten_query
        filters = {**plan.filters, **(filters or {})} or None  # request filters win over the router's
        build_filter(filters)  # reject bad keys before any branch runs
        timings: dict[str, int] = {}
        flags: list[str] = []
        t0 = time.perf_counter()

        graph_task = (asyncio.create_task(timed(timings, "graph", self._graph(plan, filters, flags)))
                      if self.use_graph else None)
        dense, bm25 = await asyncio.gather(
            timed(timings, "dense", self.dense.retrieve(q, filters, k=BRANCH_K)),
            timed(timings, "bm25", self.bm25.retrieve(q, filters, k=BRANCH_K)) if self.use_bm25 else none())
        early = None
        if self.use_rerank and graph_task:
            early = asyncio.create_task(self.reranker.ascore(q, fuse({"dense": dense, "bm25": bm25})[:TOP_N]))
        graph = await graph_task if graph_task else GraphResult()

        lists = {"dense": dense, "bm25": bm25, "graph": graph.chunks}
        fused = rrf({name: chunks for name, chunks in lists.items() if chunks})
        if self.use_rerank:
            t_rr = time.perf_counter()
            candidates = fused[:TOP_N]
            scores = await early if early else {}
            missing = [c for c in candidates if c.chunk_id not in scores]
            if missing:
                scores |= await self.reranker.ascore(q, missing)
            top = self.reranker.select(candidates, scores, k)
            timings["rerank_wait"] = int((time.perf_counter() - t_rr) * 1000)
            timings["rerank_pairs"] = len(scores)
        else:
            top = fused[:k]
        timings["total"] = int((time.perf_counter() - t0) * 1000)
        facts = [f"[G{i}] {f.text}" for i, f in enumerate(graph.facts, 1)]
        counts = {n: len(v) for n, v in lists.items()} | {"fused": len(fused)}
        use = {"bm25": self.use_bm25, "graph": self.use_graph, "rerank": self.use_rerank}
        update_span(output=[f"{c.chunk_id} ({c.score:.3f})" for c in top] + facts,
                    metadata={"query": q, "filters": filters, "flags": flags, "timings_ms": timings,
                              "counts": counts, "use": use})
        return HybridResult(chunks=top, facts=graph.facts, flags=flags, timings_ms=timings)

    async def retrieve(self, query: str, filters: dict | None = None, k: int = TOP_K) -> list[RetrievedChunk]:
        """`Retriever` protocol: document chunks only, for a query that wasn't routed (no graph seeds)."""
        return (await self.search(QueryPlan.fallback(query), filters, k)).chunks
