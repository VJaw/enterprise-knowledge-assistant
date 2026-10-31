"""BM25 (§B6.3): the tokeniser shared with the indexer, and `BM25Retriever` over the pickled index.

Lower-cased, stop words removed, and ID-like tokens (`SOP-OPS-014`, `INC-2025-031`, `EMP-22280`) kept whole
so exact ID lookups match.
"""
from __future__ import annotations

import asyncio
import json
import pickle
import re
from pathlib import Path

import numpy as np

from app.core.types import RetrievedChunk
from app.observability.langfuse import observe, update_span
from app.retrieval.dense import build_filter

INDEX_DIR = Path("data/indexes")

ID_TOKEN = re.compile(r"\b[a-z]+(?:-[a-z]+)*-\d[a-z0-9]*(?:-[a-z0-9]+)*\b")
WORD = re.compile(r"[a-z0-9]+")

STOP_WORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being below between
both but by can could did do does doing down during each few for from further had has have having he her
here hers him his how i if in into is it its itself just me more most my no nor not now of off on once only
or other our ours out over own same she should so some such than that the their theirs them then there these
they this those through to too under until up very was we were what when where which while who whom why will
with would you your yours
""".split())


def tokenize(text: str) -> list[str]:
    text = text.lower()
    ids = ID_TOKEN.findall(text)
    rest = ID_TOKEN.sub(" ", text)
    words = [w for w in WORD.findall(rest) if len(w) > 1 and w not in STOP_WORDS]
    return ids + words


def flat_payload(row: dict) -> dict:
    """A chunks.jsonl row as the same flat metadata dict the Qdrant payload carries (minus chunk_id/text)."""
    return {**row["metadata"], "chunk_index": row["chunk_index"], "heading_path": row["heading_path"],
            "entities": row.get("entities", [])}


def matches(meta: dict, filters: dict | None) -> bool:
    """In-memory equivalent of `dense.build_filter`: scalar = equality, list = any-of; list fields match on
    any element (as Qdrant does for `entities`)."""
    for key, want in (filters or {}).items():
        have = meta.get(key)
        have = set(have) if isinstance(have, list) else {have}
        if not have & (set(want) if isinstance(want, list | tuple) else {want}):
            return False
    return True


class BM25Retriever:
    """Implements `app.core.types.Retriever` over `data/indexes/{bm25.pkl,chunks.jsonl}`."""

    def __init__(self, index_dir: Path = INDEX_DIR):
        with open(index_dir / "bm25.pkl", "rb") as f:
            idx = pickle.load(f)  # noqa: S301 - our own build artefact
        self.bm25 = idx["bm25"]
        self.chunk_ids: list[str] = idx["chunk_ids"]
        rows = {}
        with open(index_dir / "chunks.jsonl") as f:
            for line in f:
                r = json.loads(line)
                rows[r["chunk_id"]] = r
        self.rows = [rows[cid] for cid in self.chunk_ids]
        self.meta = [flat_payload(r) for r in self.rows]

    def _search(self, query: str, filters: dict | None, k: int) -> list[RetrievedChunk]:
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        out = []
        for i in np.argsort(-scores):
            if scores[i] <= 0 or len(out) == k:
                break
            if matches(self.meta[i], filters):
                r = self.rows[i]
                out.append(RetrievedChunk(chunk_id=r["chunk_id"], doc_id=r["doc_id"], text=r["text"],
                                          score=float(scores[i]), source_type="document",
                                          metadata=self.meta[i]))
        return out

    @observe(name="bm25", as_type="retriever", capture_output=False)
    async def retrieve(self, query: str, filters: dict | None = None, k: int = 20) -> list[RetrievedChunk]:
        build_filter(filters)  # same key validation (ValueError -> 422) as the dense path
        chunks = await asyncio.to_thread(self._search, query, filters, k)
        update_span(output=[{"chunk_id": c.chunk_id, "score": round(c.score, 3)} for c in chunks],
                    metadata={"k": k, "filters": filters, "tokens": tokenize(query)})
        return chunks
