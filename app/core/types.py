"""Shared interfaces used across retrieval, agent and guardrails."""
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    score: float
    source_type: Literal["document", "graph", "sql", "web"]
    metadata: dict = Field(default_factory=dict)


class Retriever(Protocol):
    async def retrieve(self, query: str, filters: dict | None, k: int) -> list[RetrievedChunk]: ...


class Reranker(Protocol):
    def rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]: ...


class GuardResult(BaseModel):
    allowed: bool
    text: str
    flags: list[str]
    pii_map: dict[str, str] = Field(default_factory=dict)
