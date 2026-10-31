"""MCP tools (§B9.3): the sqlite and filesystem servers over stdio via `langchain-mcp-adapters`.

The sessions are opened once at startup and kept for the app's lifetime (a session per call would start a new
`uvx`/`npx` process every time). Tool names get a server prefix (`sqlite_read_query`, `filesystem_read_file`),
and every tool whose name contains a write verb is dropped before binding. `append` is on the list as well as
the spec's write/create/edit/move/delete, because mcp-server-sqlite's `append_insight` writes to the server's
memo (D-021). The sqlite server reads the read-only copy, and the filesystem server is rooted at `data/raw`.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from app.config import Settings

log = logging.getLogger("uvicorn.error")

WRITE_WORDS = ("write", "create", "edit", "move", "delete", "append")
START_TIMEOUT_S = 90.0  # the first start downloads the server packages


def is_write_tool(name: str) -> bool:
    return any(w in name.lower() for w in WRITE_WORDS)


def read_only(tools: list[BaseTool]) -> list[BaseTool]:
    return [t for t in tools if not is_write_tool(t.name)]


def ro_db_path(sqlite_path: str | Path) -> Path:
    p = Path(sqlite_path)
    return p.with_name(f"{p.stem}_ro{p.suffix}")


def server_config(s: Settings) -> dict:
    return {
        # `mcp<2`: mcp-server-sqlite uses `Server.list_resources`, which the mcp 2.x SDK removed (D-021)
        "sqlite": {"command": "uvx",
                   "args": ["--with", "mcp<2", "--from", s.mcp_sqlite_package, "mcp-server-sqlite",
                            "--db-path", str(ro_db_path(s.sqlite_path).resolve())],
                   "transport": "stdio"},
        "filesystem": {"command": "npx",
                       "args": ["-y", s.mcp_filesystem_package, str(Path(s.raw_dir).resolve())],
                       "transport": "stdio"},
    }


class MCPTools:
    """Owns the MCP sessions. `start()` returns the read-only tools; a server that fails to start is skipped
    (the native SQL and document tools still work, §D4)."""

    def __init__(self, config: dict):
        self.client = MultiServerMCPClient(config)
        self.servers = list(config)
        self.stack = AsyncExitStack()
        self.tools: list[BaseTool] = []
        self.dropped: list[str] = []

    async def _open(self, name: str) -> list[BaseTool]:
        session = await self.stack.enter_async_context(self.client.session(name))
        return await load_mcp_tools(session, server_name=name, tool_name_prefix=True)

    async def start(self) -> list[BaseTool]:
        for name in self.servers:
            try:
                # asyncio.timeout, not wait_for: the session's task group must be entered and exited in the
                # same task (the lifespan's), and wait_for would run _open in a task of its own.
                async with asyncio.timeout(START_TIMEOUT_S):
                    tools = await self._open(name)
            except Exception as e:  # noqa: BLE001
                log.warning("MCP server %r failed to start (%s): continuing without it", name,
                            type(e).__name__)
                continue
            self.dropped += [t.name for t in tools if is_write_tool(t.name)]
            self.tools += read_only(tools)
        log.warning("MCP tools bound: %s", ", ".join(t.name for t in self.tools) or "(none)")
        log.warning("MCP write tools dropped: %s", ", ".join(self.dropped) or "(none)")
        return self.tools

    async def close(self) -> None:
        try:
            await self.stack.aclose()
        except Exception as e:  # noqa: BLE001 - stdio servers may already be gone at shutdown
            log.warning("MCP shutdown: %s", type(e).__name__)
