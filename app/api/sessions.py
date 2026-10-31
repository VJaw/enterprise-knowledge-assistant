"""GET / DELETE /sessions/{session_id} (§B11.1): the conversation held by the LangGraph checkpointer.

Messages are what the checkpoint stores: the masked question and the final masked answer (or refusal) of
each turn (§B10.4), so PII the user typed never comes back from here.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import run_config
from app.agent.nodes import text_of
from app.api.errors import ApiError
from app.api.schemas import ErrorResponse, SessionDeleted, SessionHistory, SessionMessage

router = APIRouter(tags=["sessions"])


async def _messages(request: Request, session_id: str) -> list:
    snapshot = await request.app.state.agent.aget_state(run_config(session_id))
    return (snapshot.values or {}).get("messages") or []


@router.get("/sessions/{session_id}", response_model=SessionHistory,
            responses={404: {"model": ErrorResponse, "description": "NOT_FOUND: no such session"}})
async def get_session(session_id: str, request: Request) -> SessionHistory:
    """The session's conversation, oldest first (masked)."""
    messages = [SessionMessage(role="user" if isinstance(m, HumanMessage) else "assistant",
                               content=text_of(m.content))
                for m in await _messages(request, session_id) if isinstance(m, HumanMessage | AIMessage)]
    if not messages:
        raise ApiError("NOT_FOUND", 404, f"Session {session_id!r} not found.")
    return SessionHistory(session_id=session_id, turns=sum(m.role == "user" for m in messages),
                          messages=messages)


@router.delete("/sessions/{session_id}", response_model=SessionDeleted)
async def delete_session(session_id: str, request: Request) -> SessionDeleted:
    """Clear a session: every checkpoint of the thread is deleted. Idempotent (`deleted: false` if absent)."""
    existed = bool(await _messages(request, session_id))
    await request.app.state.agent.checkpointer.adelete_thread(session_id)
    return SessionDeleted(session_id=session_id, deleted=existed)
