"""Shared tool plumbing (§B9.3): a timeout per tool, errors returned as strings, citable output as artifacts.

Every tool returns `(content, artifact)` (LangChain `response_format="content_and_artifact"`). The artifact is
the list of `RetrievedChunk`s the agent can cite; the agent's tool node gives them their turn-wide citation
ids and rewrites the content with those ids. The content written here is what a caller outside the agent sees.
"""
from __future__ import annotations

import asyncio
import functools
import re

TOOL_TIMEOUT_S = 10.0  # §B12.3
MAX_OUTPUT_CHARS = 12_000

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
PHONE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}"
                   r"(?:\s*(?:x|ext\.?)\s*\d+)?(?!\w)")


def mask_contacts(text: str) -> str:
    """Email addresses and phone numbers -> placeholders. Phase 8's Presidio rail does the full PII job."""
    return PHONE.sub("<PHONE>", EMAIL.sub("<EMAIL>", text))


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    return text if len(text) <= limit else f"{text[:limit]}\n... (truncated, {len(text) - limit} more chars)"


def error(msg: str) -> tuple[str, list]:
    return f"error: {msg}", []


def guarded(timeout: float = TOOL_TIMEOUT_S):
    """Wrap an async tool body: enforce the timeout and turn any exception into an error string, so a failing
    tool never fails the request; the agent reads the error and carries on."""
    def wrap(fn):
        @functools.wraps(fn)
        async def run(*args, **kwargs):
            try:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
            except TimeoutError:
                return error(f"timed out after {timeout:g} s")
            except Exception as e:  # noqa: BLE001
                return error(f"{type(e).__name__}: {e}")
        return run
    return wrap
