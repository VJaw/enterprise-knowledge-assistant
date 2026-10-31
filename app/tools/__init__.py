"""Agent tools (§B9.3). `build_tools` returns the native tools; MCP tools come from `app.tools.mcp.MCPTools`.
"""
from __future__ import annotations

from langchain_core.tools import BaseTool

from app.config import Settings
from app.retrieval.graph import GraphRetriever
from app.retrieval.hybrid import HybridRetriever


def build_tools(s: Settings, retriever: HybridRetriever, graph: GraphRetriever) -> list[BaseTool]:
    from app.tools.graph_query import make_graph_query
    from app.tools.kb_search import make_kb_search
    from app.tools.mcp import ro_db_path
    from app.tools.python_exec import make_python_exec
    from app.tools.sql_query import make_sql_query
    from app.tools.web_search import make_web_search

    key = s.tavily_api_key.get_secret_value() if s.tavily_api_key else None
    return [make_kb_search(retriever), make_graph_query(graph), make_sql_query(ro_db_path(s.sqlite_path)),
            make_python_exec(), make_web_search(key)]
