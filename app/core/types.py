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


class ToolResult(BaseModel):
    """One executed tool call, as reported in `ChatResponse.tool_calls` (§B11.2)."""
    name: str
    args: dict
    duration_ms: int
    status: Literal["ok", "error", "blocked"]
    output: str = ""  # truncated, for the trace


class GroundingResult(BaseModel):
    score: float | None  # supported claims / claims; None when the check was skipped
    grounded: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    skipped: str | None = None  # why the judge didn't run (chit_chat, abstained, judge_error)


class GuardResult(BaseModel):
    allowed: bool
    text: str
    flags: list[str]
    pii_map: dict[str, str] = Field(default_factory=dict)
