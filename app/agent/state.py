"""Agent state (§B9.2), checkpointed per session by the SQLite saver (`thread_id = session_id`).

`messages` is the conversation only (one HumanMessage and one AIMessage answer per turn), so it is what the
router sees as history and what a session holds. The rest is per-turn working state that `turn_input()`
(app/agent/graph.py) resets at the start of each turn. Fields added to §B9.2 (D-021): `filters`, `allow_web`,
`scratch` (the agent's tool-calling messages for this turn), `flags` and `answer`.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from app.core.types import GroundingResult, RetrievedChunk, ToolResult
from app.retrieval.router import QueryPlan


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # conversation (masked from Phase 8)
    plan: QueryPlan | None
    contexts: list[RetrievedChunk]  # re-ranked chunks + graph facts + tool results, each with a cite_id
    tool_results: list[ToolResult]
    steps: int  # agent LLM calls this turn, bounded by AGENT_MAX_STEPS
    model: str  # decided by the model router; may move to gpt-4o mid-loop or fall back to gpt-4o-mini
    draft_answer: str | None
    grounding: GroundingResult | None  # score, unsupported_claims[]
    retries: int
    pii_map: dict[str, str]  # placeholder -> original (never sent to the LLM; cleared before the checkpoint)
    filters: dict[str, str] | None  # request filters
    allow_web: bool
    scratch: list[AnyMessage]
    flags: list[str]
    answer: str | None


# Types the checkpointer may deserialize (LangGraph warns on, and will later block, unregistered types).
CHECKPOINT_TYPES = [("app.core.types", "RetrievedChunk"), ("app.core.types", "ToolResult"),
                    ("app.core.types", "GroundingResult"), ("app.retrieval.router", "QueryPlan")]
