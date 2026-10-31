"""Semantic cache (§B12.1) in the Qdrant `semantic_cache` collection.

- Key: the bge-small embedding of the masked, normalised, history-resolved question. A first-turn question is
  its own resolved form; a follow-up is written under the router's `rewritten_query`.
- Hit: cosine >= CACHE_SIM_THRESHOLD, same `filters_hash`, same `corpus_version`, younger than
  CACHE_TTL_SECONDS, and the same specifics: identical tokens with digits (SEV1, INC-2025-012, $5,000) and the
  same dependency direction ("what depends on X" vs "what X depends on"). bge-small scores those pairs
  0.92-0.96, above some true paraphrases, so no threshold alone separates them (D-026,
  scripts/cache_threshold.py).
- Write: only grounded answers (score >= 0.9) with no PII placeholder in the question or answer, no
  `web_search` call, intent != chit_chat, not blocked, and no confidential source (§B17).
- Invalidation: `corpus_version` (data/indexes/corpus_version) goes up on every index build, `/ingest`
  included.
- Multi-turn: a lookup is skipped when the session has history and the message refers back to it, because
  resolving it would need the router (an LLM call) in front of the cache.

A hit replays the stored masked answer and its cited sources; the pipeline still runs the output rails on it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    Range,
    VectorParams,
)

from app.core.types import GroundingResult, RetrievedChunk
from app.observability.langfuse import get_client
from app.retrieval.embeddings import DIM, embed_query

COLLECTION = "semantic_cache"
VERSION_FILE = Path("data/indexes/corpus_version")
GROUNDED_MIN = 0.9

# Words that only make sense against the conversation so far ("who manages that team?", "and its owner?").
FOLLOW_UP = re.compile(r"\b(it|its|they|them|their|theirs|that|this|those|these|he|she|him|her|his|hers"
                       r"|same|above|previous|earlier|former|latter)\b|^(and|also|what about|how about)\b",
                       re.I)


def read_corpus_version(path: Path = VERSION_FILE) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 1


def bump_corpus_version(path: Path = VERSION_FILE) -> int:
    """Called by every index build (ingestion.index.build_indexes): cached answers never outlive a corpus."""
    v = read_corpus_version(path) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{v}\n")
    return v


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return " ".join(text.split()).rstrip(" ?.!")


def filters_hash(filters: dict | None) -> str:
    if not filters:
        return "none"
    return hashlib.sha1(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:16]


TOKEN = re.compile(r"[a-z0-9$]+(?:[-.,/][a-z0-9]+)*")
DEPENDENCY = re.compile(r"\b(?:depends?|depended|depending|relies|rely|relied|relying)\s+on\b"
                        r"(?:\s+the)?\s*(\w*)")


def specifics(key: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """What must match exactly between two cache keys: tokens with digits, and what each "depends on"
    points at ("" when the phrase ends the question)."""
    ids = frozenset(t.strip(".,") for t in TOKEN.findall(key) if any(ch.isdigit() for ch in t))
    return ids, tuple(sorted(DEPENDENCY.findall(key)))


def depends_on_history(message: str, has_history: bool) -> bool:
    return has_history and bool(FOLLOW_UP.search(message))


class CachedAnswer(BaseModel):
    """`response_json` in the payload: what a hit needs to rebuild the response."""
    answer: str  # the final masked answer, after the output rails
    contexts: list[RetrievedChunk] = Field(default_factory=list)  # the cited sources, with their cite ids
    grounding: GroundingResult
    model: str  # the model that wrote it
    intent: str | None = None


@dataclass
class CacheHit:
    entry: CachedAnswer
    similarity: float
    age_s: float


def write_refusal(*, answer: str, masked_message: str, grounding: GroundingResult | None, intent: str | None,
                  blocked: bool, tools: list[str], contexts: list[RetrievedChunk]) -> str | None:
    """Why an answer must not be cached (§B12.1, §B17), or None when it may be."""
    from app.guardrails.pii import PLACEHOLDER

    if blocked:
        return "blocked"
    if intent == "chit_chat":
        return "chit_chat"
    if grounding is None or grounding.score is None or grounding.score < GROUNDED_MIN:
        return "not_grounded"
    if PLACEHOLDER.search(masked_message) or PLACEHOLDER.search(answer):
        return "pii_placeholder"
    if "web_search" in tools:
        return "web_search"
    if any(c.metadata.get("sensitivity") == "confidential" for c in contexts):
        return "confidential_source"
    return None


class SemanticCache:
    def __init__(self, client: QdrantClient, *, threshold: float = 0.92, ttl_s: int = 86400,
                 version_path: Path = VERSION_FILE):
        self.client = client
        self.threshold, self.ttl_s = threshold, ttl_s
        self.version_path = version_path
        self.corpus_version = read_corpus_version(version_path)
        if not client.collection_exists(COLLECTION):
            client.create_collection(COLLECTION,
                                     vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))

    def reload_version(self) -> int:
        """After a re-index: pick up the new version and drop entries of older ones."""
        self.corpus_version = read_corpus_version(self.version_path)
        current = FieldCondition(key="corpus_version", match=MatchValue(value=self.corpus_version))
        stale = Filter(must_not=[current])
        self.client.delete(COLLECTION, points_selector=FilterSelector(filter=stale))
        return self.corpus_version

    def count(self) -> int:
        return self.client.count(COLLECTION, exact=True).count

    def _search(self, text: str, fhash: str, now: float) -> tuple[CacheHit | None, float | None]:
        vec = embed_query(text)
        cond = Filter(must=[
            FieldCondition(key="filters_hash", match=MatchValue(value=fhash)),
            FieldCondition(key="corpus_version", match=MatchValue(value=self.corpus_version)),
            FieldCondition(key="created_at", range=Range(gte=now - self.ttl_s)),
        ])
        res = self.client.query_points(COLLECTION, query=vec, query_filter=cond, limit=3, with_payload=True)
        if not res.points:
            return None, None
        want = specifics(text)
        for p in res.points:
            if p.score < self.threshold:
                break
            if specifics(p.payload["query"]) != want:  # e.g. SEV1 vs SEV2: close in vector space only
                continue
            entry = CachedAnswer.model_validate_json(p.payload["response_json"])
            return CacheHit(entry=entry, similarity=p.score, age_s=now - p.payload["created_at"]), p.score
        return None, res.points[0].score

    async def lookup(self, text: str, filters: dict | None) -> CacheHit | None:
        key = normalise(text)
        with get_client().start_as_current_observation(name="cache.lookup", as_type="span",
                                                       input={"query": key}) as span:
            hit, best = await asyncio.to_thread(self._search, key, filters_hash(filters), time.time())
            span.update(output={"hit": hit is not None, "similarity": round(best, 4) if best else None},
                        metadata={"threshold": self.threshold, "corpus_version": self.corpus_version,
                                  "age_s": round(hit.age_s) if hit else None})
        return hit

    def _upsert(self, key: str, fhash: str, entry: CachedAnswer) -> None:
        pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.corpus_version}|{fhash}|{key}"))
        payload = {"response_json": entry.model_dump_json(), "created_at": time.time(), "model": entry.model,
                   "corpus_version": self.corpus_version, "filters_hash": fhash, "query": key}
        self.client.upsert(COLLECTION, points=[PointStruct(id=pid, vector=embed_query(key), payload=payload)])

    async def write(self, text: str, filters: dict | None, entry: CachedAnswer) -> None:
        key = normalise(text)
        with get_client().start_as_current_observation(name="cache.write", as_type="span",
                                                       input={"query": key}) as span:
            await asyncio.to_thread(self._upsert, key, filters_hash(filters), entry)
            span.update(output={"written": True, "sources": len(entry.contexts)})
