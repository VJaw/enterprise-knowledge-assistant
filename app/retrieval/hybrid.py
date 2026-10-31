"""Hybrid retrieval (§B8.2): dense + BM25 + graph in parallel -> RRF -> cross-encoder -> top 6 + graph facts.

Feature flags (`USE_BM25`, `USE_GRAPH`, `USE_RERANK`) switch branches off for ablations. If Aura is
unavailable (at startup, or the graph branch errors / exceeds its budget) retrieval continues with
dense + BM25 and the result carries the `graph_unavailable` flag (§B12.3).

`prefetch()` starts the dense + BM25 branches and their re-rank scores on the user's own message before the
router has planned the query (first turns only, D-028); `search()` then uses them when the filters match, so
retrieval runs alongside the router instead of after it.
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


@dataclass
class Branches:
    """Dense + BM25 candidates and the re-rank scores of their fused top 20."""
    dense: list[RetrievedChunk]
    bm25: list[RetrievedChunk]
    scores: dict[str, float]
    timings_ms: dict[str, int]


@dataclass
class Prefetch:
    query: str
    filters: dict | None
    task: asyncio.Task  # -> Branches | None (None: bad filters, which search() reports)

    def matches(self, filters: dict | None) -> bool:
        return self.filters == filters

    def cancel(self) -> None:
        if not self.task.done():
            self.task.cancel()


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

    def prefetch(self, query: str, filters: dict | None = None) -> Prefetch:
        """Start the dense + BM25 branches and the re-ranking of their candidates for `query` now (a task),
        for a later `search()` with the same filters to pick up."""
        filters = filters or None
        return Prefetch(query, filters, asyncio.create_task(self._prefetch(query, filters)))

    @observe(name="retrieval.prefetch", as_type="retriever", capture_output=False)
    async def _prefetch(self, query: str, filters: dict | None) -> Branches | None:
        try:
            build_filter(filters)
        except ValueError:
            return None
        timings: dict[str, int] = {}
        dense, bm25 = await self._branches(query, filters, timings)
        scores = {}
        if self.use_rerank:
            t0 = time.perf_counter()
            scores = await self.reranker.ascore(query, fuse({"dense": dense, "bm25": bm25})[:TOP_N])
            timings["prefetch_rerank"] = int((time.perf_counter() - t0) * 1000)
        update_span(metadata={"query": query, "filters": filters, "timings_ms": timings})
        return Branches(dense, bm25, scores, timings)

    async def _branches(self, q: str, filters: dict | None, timings: dict) -> tuple[list, list]:
        return await asyncio.gather(
            timed(timings, "dense", self.dense.retrieve(q, filters, k=BRANCH_K)),
            timed(timings, "bm25", self.bm25.retrieve(q, filters, k=BRANCH_K)) if self.use_bm25 else none())

    @observe(name="retrieval.hybrid", as_type="retriever", capture_output=False)
    async def search(self, plan: QueryPlan, filters: dict | None = None, k: int = TOP_K,
                     prefetched: Prefetch | None = None) -> HybridResult:
        """Request filters win over the router's. If the router's filters leave nothing to return, the search
        runs again with the request's filters only and the result carries `router_filters_relaxed` (D-020).
        A `prefetched` result is used by whichever pass has the same filters it was started with."""
        merged = {**plan.filters, **(filters or {})} or None
        try:
            build_filter(merged)  # reject bad keys before any branch runs
            res = await self._search(plan, merged, k, pre=prefetched)
            if not res.chunks and merged != (filters or None):
                res = await self._search(plan, filters or None, k, flags=["router_filters_relaxed"],
                                         pre=prefetched)
            return res
        finally:
            if prefetched is not None:
                prefetched.cancel()

    async def _search(self, plan: QueryPlan, filters: dict | None, k: int, flags: list[str] | None = None,
                      pre: Prefetch | None = None) -> HybridResult:
        """Dense, BM25 and graph run concurrently. Re-ranking starts on the dense + BM25 candidates as soon as
        they are in, while the graph branch is still waiting on Aura; once it returns, only the graph-anchored
        newcomers to the fused top 20 are scored. Pair scores are independent, so the result is the same as
        re-ranking the final fused top 20 in one go. With a matching prefetch, the dense + BM25 candidates and
        their scores are already (being) computed on the user's message, which is then the re-rank query."""
        timings: dict[str, int] = {}
        flags = list(flags or [])
        t0 = time.perf_counter()

        graph_task = (asyncio.create_task(timed(timings, "graph", self._graph(plan, filters, flags)))
                      if self.use_graph else None)
        branches = await pre.task if pre is not None and pre.matches(filters) else None
        early = None
        if branches is not None:
            q, dense, bm25 = pre.query, branches.dense, branches.bm25
            timings |= branches.timings_ms
        else:
            q = plan.rewritten_query
            dense, bm25 = await self._branches(q, filters, timings)
            if self.use_rerank and graph_task:
                candidates = fuse({"dense": dense, "bm25": bm25})[:TOP_N]
                early = asyncio.create_task(self.reranker.ascore(q, candidates))
        graph = await graph_task if graph_task else GraphResult()

        lists = {"dense": dense, "bm25": bm25, "graph": graph.chunks}
        fused = rrf({name: chunks for name, chunks in lists.items() if chunks})
        if self.use_rerank:
            t_rr = time.perf_counter()
            candidates = fused[:TOP_N]
            scores = dict(branches.scores) if branches is not None else (await early if early else {})
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
                              "counts": counts, "use": use, "prefetched": branches is not None})
        return HybridResult(chunks=top, facts=graph.facts, flags=flags, timings_ms=timings)

    async def retrieve(self, query: str, filters: dict | None = None, k: int = TOP_K) -> list[RetrievedChunk]:
        """`Retriever` protocol: document chunks only, for a query that wasn't routed (no graph seeds)."""
        return (await self.search(QueryPlan.fallback(query), filters, k)).chunks
