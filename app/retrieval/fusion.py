"""Reciprocal Rank Fusion (§B8.4): score(d) = sum over lists of 1 / (k + rank), deduped by chunk_id."""
from __future__ import annotations

from app.core.types import RetrievedChunk
from app.observability.langfuse import observe, update_span

RRF_K = 60


def fuse(lists: dict[str, list[RetrievedChunk]], k: int = RRF_K) -> list[RetrievedChunk]:
    """Fuse named ranked lists. The returned chunks are copies whose `score` is the RRF score and whose
    `metadata["ranks"]` records the 1-based rank in each list that contained them."""
    fused: dict[str, RetrievedChunk] = {}
    for name, chunks in lists.items():
        for rank, c in enumerate(chunks, 1):
            if c.chunk_id not in fused:
                meta = {**c.metadata, "ranks": {}}
                fused[c.chunk_id] = c.model_copy(update={"score": 0.0, "metadata": meta})
            f = fused[c.chunk_id]
            if name not in f.metadata["ranks"]:  # a duplicate within one list counts once, at its best rank
                f.metadata["ranks"][name] = rank
                f.score += 1 / (k + rank)
    return sorted(fused.values(), key=lambda c: c.score, reverse=True)


@observe(name="fusion.rrf", capture_input=False, capture_output=False)
def rrf(lists: dict[str, list[RetrievedChunk]], k: int = RRF_K) -> list[RetrievedChunk]:
    """`fuse` as a traced span."""
    out = fuse(lists, k)
    update_span(output=[{"chunk_id": c.chunk_id, "rrf": round(c.score, 5)} for c in out[:10]],
                metadata={"inputs": {n: len(v) for n, v in lists.items()}, "fused": len(out)})
    return out
