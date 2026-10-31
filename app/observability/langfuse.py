"""Langfuse wiring (§B13): one client per process, a LangChain callback per request, @observe for spans.

The SDK is v5 (OpenTelemetry based): spans opened with `observe` / `start_as_current_observation` nest by
context, and the LangChain `CallbackHandler` attaches its generations to whatever span is current.
"""
from __future__ import annotations

from langfuse import Langfuse, get_client, observe, propagate_attributes
from langfuse.langchain import CallbackHandler
from opentelemetry import trace as otel_trace

from app.config import Settings

__all__ = ["init_langfuse", "llm_callbacks", "current_trace_id", "update_span", "get_client", "observe",
           "propagate_attributes"]


def _secret(v) -> str | None:
    return v.get_secret_value() if v else None


def init_langfuse(s: Settings) -> Langfuse:
    """Register the process-wide client that `get_client()` / `observe` / `CallbackHandler` use."""
    return Langfuse(public_key=_secret(s.langfuse_public_key), secret_key=_secret(s.langfuse_secret_key),
                    host=s.langfuse_host)


def llm_callbacks() -> list:
    """Fresh handler per call: it keeps per-run state."""
    return [CallbackHandler()]


def current_trace_id() -> str:
    return get_client().get_current_trace_id() or ""


def update_span(**kwargs) -> None:
    """Update the current span; a no-op (without the SDK's error log) when nothing is being traced."""
    if otel_trace.get_current_span().is_recording():
        get_client().update_current_span(**kwargs)
