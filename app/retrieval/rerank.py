"""Cross-encoder re-ranking (§B8.4): bge-reranker-base over the top 20 fused chunks -> top 6, min score 0.1.

§B8.4 says top 30. Phase 11 measured top 20: the full pipeline's Recall@5 / MRR@10 / nDCG@10 on the 20
labelled queries are unchanged (0.990 / 0.967 / 0.947) and re-ranking takes ~28% less time (D-028).

The scored passage is the chunk's index text, `<title> (<doc_id>) | <heading path>` plus the chunk text, as
embedded and BM25-indexed (D-011); without the doc id, ID lookups ("What happened in INC-2025-012?") are
re-ranked away. Pairs are scored shortest-first in batches of 8: on CPU this is ~1.5x faster than one padded
batch of 30 and gives identical scores.

Scores are per (query, chunk) pair, so they can be computed in stages (`ascore`) and combined (`select`): the
hybrid retriever scores the dense + BM25 candidates while the graph branch is still waiting on Aura.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache

from app.config import get_settings
from app.core.types import RetrievedChunk
from app.observability.langfuse import observe, update_span

TOP_N = 20  # §B8.4: 30 (D-028)
TOP_K = 6
MIN_SCORE = 0.1
BATCH = 8


@lru_cache
def get_cross_encoder():
    from sentence_transformers import CrossEncoder  # heavy import, deferred until first use

    return CrossEncoder(get_settings().rerank_model, device="cpu", max_length=512)


def passage(c: RetrievedChunk) -> str:
    m = c.metadata
    return f"{m.get('title')} ({c.doc_id}) | {m.get('heading_path') or ''}\n{c.text}"


class CrossEncoderReranker:
    """Implements `app.core.types.Reranker`. `model` is injectable for tests."""

    def __init__(self, model=None, min_score: float = MIN_SCORE):
        self._model = model
        self.min_score = min_score

    @property
    def model(self):
        if self._model is None:
            self._model = get_cross_encoder()
        return self._model

    def score(self, query: str, chunks: list[RetrievedChunk]) -> dict[str, float]:
        """chunk_id -> cross-encoder score (sigmoid, 0-1)."""
        if not chunks:
            return {}
        texts = [passage(c) for c in chunks]
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        scores = self.model.predict([(query, texts[i]) for i in order], batch_size=BATCH,
                                    show_progress_bar=False)
        return {chunks[i].chunk_id: float(s) for i, s in zip(order, scores, strict=True)}

    def select(self, chunks: list[RetrievedChunk], scores: dict[str, float],
               top_k: int = TOP_K) -> list[RetrievedChunk]:
        """Top `top_k` of `chunks` by score, dropping any below `min_score`. The fused (RRF) score moves to
        `metadata["rrf"]`."""
        ranked = sorted(chunks, key=lambda c: scores[c.chunk_id], reverse=True)
        return [c.model_copy(update={"score": scores[c.chunk_id], "metadata": {**c.metadata, "rrf": c.score}})
                for c in ranked[:top_k] if scores[c.chunk_id] >= self.min_score]

    def rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int = TOP_K) -> list[RetrievedChunk]:
        candidates = chunks[:TOP_N]
        return self.select(candidates, self.score(query, candidates), top_k)

    @observe(name="rerank.cross_encoder", capture_input=False, capture_output=False)
    async def ascore(self, query: str, chunks: list[RetrievedChunk]) -> dict[str, float]:
        scores = await asyncio.to_thread(self.score, query, chunks)
        update_span(output={k: round(v, 4) for k, v in scores.items()},
                    metadata={"pairs": len(chunks), "max": max(scores.values(), default=None),
                              "min": min(scores.values(), default=None)})
        return scores
