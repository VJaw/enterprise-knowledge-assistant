"""MCP tools (§B9.3): write-capable tools are never bound. The live test starts the real stdio servers."""
import shutil

import pytest
from langchain_core.tools import StructuredTool

from app.agent.nodes import mcp_artifact
from app.config import Settings
from app.tools.mcp import MCPTools, is_write_tool, read_only, server_config

SQLITE_TOOLS = ["read_query", "write_query", "create_table", "list_tables", "describe_table",
                "append_insight"]
FS_TOOLS = ["read_file", "read_text_file", "read_media_file", "read_multiple_files", "write_file",
            "edit_file", "create_directory", "list_directory", "list_directory_with_sizes", "directory_tree",
            "move_file", "search_files", "get_file_info", "list_allowed_directories"]


def fake(name: str) -> StructuredTool:
    return StructuredTool.from_function(func=lambda: "", name=name, description=name)


def test_write_tools_are_filtered_out():
    names = [f"sqlite_{n}" for n in SQLITE_TOOLS] + [f"filesystem_{n}" for n in FS_TOOLS]
    kept = [t.name for t in read_only([fake(n) for n in names])]
    assert kept == ["sqlite_read_query", "sqlite_list_tables", "sqlite_describe_table",
                    "filesystem_read_file", "filesystem_read_text_file", "filesystem_read_media_file",
                    "filesystem_read_multiple_files", "filesystem_list_directory",
                    "filesystem_list_directory_with_sizes", "filesystem_directory_tree",
                    "filesystem_search_files", "filesystem_get_file_info",
                    "filesystem_list_allowed_directories"]
    assert is_write_tool("sqlite_append_insight") and is_write_tool("DeleteThing")


def test_servers_use_the_read_only_db_and_data_raw():
    cfg = server_config(Settings(_env_file=None))
    assert cfg["sqlite"]["command"] == "uvx" and cfg["sqlite"]["args"][-1].endswith("data/acme_ro.db")
    assert cfg["sqlite"]["args"][:4] == ["--with", "mcp<2", "--from", "mcp-server-sqlite==2025.4.25"]
    assert cfg["filesystem"]["command"] == "npx" and cfg["filesystem"]["args"][-1].endswith("data/raw")


def test_mcp_reads_become_citable_sources():
    [sql] = mcp_artifact("sqlite_read_query", {"query": "SELECT  count(*) FROM tickets"},
                         "[{'count(*)': 300}]")
    assert sql.source_type == "sql" and sql.metadata["query"] == "SELECT count(*) FROM tickets"
    [doc] = mcp_artifact("filesystem_read_text_file", {"path": "sops/SOP-OPS-014_database-failover.md"},
                         "# x")
    assert doc.source_type == "document" and doc.doc_id == "SOP-OPS-014"
    assert mcp_artifact("filesystem_list_directory", {"path": "."}, "x") is None


@pytest.mark.skipif(not (shutil.which("uvx") and shutil.which("npx")), reason="uvx/npx not installed")
async def test_live_servers_bind_only_read_tools():
    mcp = MCPTools(server_config(Settings(_env_file=None)))
    try:
        tools = await mcp.start()
        names = [t.name for t in tools]
        assert "sqlite_read_query" in names and "filesystem_read_text_file" in names
        assert not any(is_write_tool(n) for n in names)
        assert set(mcp.dropped) == {"sqlite_write_query", "sqlite_create_table", "sqlite_append_insight",
                                    "filesystem_write_file", "filesystem_edit_file",
                                    "filesystem_create_directory", "filesystem_move_file"}
        read = next(t for t in tools if t.name == "sqlite_read_query")
        msg = await read.ainvoke({"name": read.name, "args": {"query": "SELECT count(*) AS n FROM teams"},
                                  "id": "1", "type": "tool_call"})
        assert "6" in str(msg.content)
    finally:
        await mcp.close()
