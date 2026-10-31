"""Request/response schemas for the chat API (§B11.2)."""
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str | None = None  # created if absent
    message: str = Field(min_length=1, max_length=2000)
    filters: dict[str, str] | None = None  # e.g. {"domain": "operations"}
    allow_web: bool = True


class Citation(BaseModel):
    id: str  # "1", "G1", "SQL", "WEB1"
    source_type: Literal["document", "graph", "sql", "web"]
    doc_id: str | None = None
    title: str | None = None
    section: str | None = None
    page: int | None = None
    url: str | None = None
    snippet: str


class ToolCall(BaseModel):
    name: str
    args: dict
    duration_ms: int
    status: Literal["ok", "error", "blocked"]


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    citations: list[Citation]
    tool_calls: list[ToolCall]
    guardrail_flags: list[str]  # e.g. ["pii_masked_input", "regenerated_ungrounded"]
    grounding_score: float | None
    model_used: str  # "gpt-4o-mini" | "gpt-4o" | "cache"
    cache_hit: bool
    latency_ms: int
    usage: dict  # prompt/completion tokens, est. cost USD
    trace_id: str  # Langfuse trace id
