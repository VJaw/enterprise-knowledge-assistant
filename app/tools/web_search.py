"""`web_search`: Tavily for external / current information (§B9.3). Results are `[WEBn]` items labelled
external, never mixed with internal citations, and treated as untrusted data. Tavily errors are retried twice
(§B12.3); if it stays down the tool returns an error string and the agent continues without it."""
from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from tavily import AsyncTavilyClient
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.types import RetrievedChunk
from app.retrieval.context import format_items, label
from app.tools.common import error, guarded, truncate

MAX_RESULTS = 5
RESULT_CHARS = 1500
TAVILY_TIMEOUT_S = 8.0


class WebSearchArgs(BaseModel):
    query: str = Field(description="A web search query about public, external or current information")


DESCRIPTION = """Search the public web (Tavily) for external or current information that Acme's internal \
sources can't have: public CVEs, vendor releases, standards, news. Never use it for Acme-internal facts. \
Returns the top 5 results, labelled as external."""


def make_web_search(api_key: str | None, client: AsyncTavilyClient | None = None) -> StructuredTool:
    tavily = client or (AsyncTavilyClient(api_key=api_key) if api_key else None)

    @guarded()
    async def web_search(query: str):
        if tavily is None:
            return error("web search is not configured (TAVILY_API_KEY missing)")
        async for attempt in AsyncRetrying(stop=stop_after_attempt(3), wait=wait_exponential(min=0.5, max=2),
                                           retry=retry_if_exception_type(Exception), reraise=True):
            with attempt:
                res = await tavily.search(query, max_results=MAX_RESULTS, timeout=TAVILY_TIMEOUT_S)
        items = [RetrievedChunk(chunk_id=f"web:{r['url']}", doc_id="web", text=truncate(r.get("content", ""),
                                                                                        RESULT_CHARS),
                                score=float(r.get("score") or 0.0), source_type="web",
                                metadata={"title": r.get("title"), "url": r["url"]})
                 for r in res.get("results", [])[:MAX_RESULTS] if r.get("url")]
        if not items:
            return "No web results.", []
        return format_items(label([], items)[0]), items

    return StructuredTool.from_function(coroutine=web_search, name="web_search", description=DESCRIPTION,
                                        args_schema=WebSearchArgs, response_format="content_and_artifact")
