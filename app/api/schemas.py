"""Request/response schemas for the API (§B11.2, §B11.3)."""
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=128,
                                   description="Conversation id; a new one is created if absent.")
    message: str = Field(min_length=1, max_length=2000)
    filters: dict[str, str] | None = Field(
        default=None, description="Metadata filters: doc_type, domain, sensitivity, doc_id or entities.",
        examples=[{"domain": "operations"}])
    allow_web: bool = Field(default=True, description="Let the agent call the web_search tool.")

    model_config = {"json_schema_extra": {"examples": [
        {"message": "What is the retention period for customer logs?"},
        {"session_id": "demo-1", "message": "Which team owns Ledger DB?", "filters": {"doc_type": "sop"}}]}}


class Citation(BaseModel):
    id: str = Field(description='"1" passage, "G1" graph fact, "SQL1" query result, "WEB1" web result')
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
    guardrail_flags: list[str] = Field(description='e.g. ["pii_masked_input", "regenerated_ungrounded"]; a '
                                                   'refusal carries "GUARDRAIL_BLOCKED"')
    grounding_score: float | None
    model_used: str = Field(description='"gpt-4o-mini", "gpt-4o", "cache" or "guardrails" (refusal)')
    cache_hit: bool
    latency_ms: int
    usage: dict = Field(description="prompt/completion tokens, est. cost USD, per-model breakdown, "
                                    "agent steps")
    trace_id: str = Field(description="Langfuse trace id")


class SessionMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(description="Masked text, as stored in the session (§B10.4)")


class SessionHistory(BaseModel):
    session_id: str
    turns: int
    messages: list[SessionMessage]


class SessionDeleted(BaseModel):
    session_id: str
    deleted: bool


class IngestResponse(BaseModel):
    documents: int
    chunks: int
    corpus_version: int
    cache_entries_dropped: int
    duration_ms: int


class ErrorDetail(BaseModel):
    code: Literal["VALIDATION_ERROR", "RATE_LIMITED", "UPSTREAM_TIMEOUT", "INTERNAL_ERROR", "UNAUTHORIZED",
                  "NOT_FOUND", "CONFLICT", "BAD_REQUEST", "METHOD_NOT_ALLOWED", "HTTP_ERROR"]
    message: str
    trace_id: str | None = None


class ErrorResponse(BaseModel):
    """§B11.3. A guardrail refusal is *not* an error: it is a 200 ChatResponse with GUARDRAIL_BLOCKED."""
    error: ErrorDetail
