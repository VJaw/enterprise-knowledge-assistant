"""`search_knowledge_base`: re-query hybrid retrieval with a refined query or sub-question (§B9.3)."""
from __future__ import annotations

from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.retrieval.context import format_context
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.rerank import TOP_K
from app.retrieval.router import QueryPlan
from app.tools.common import guarded


class KBSearchArgs(BaseModel):
    query: str = Field(description="A focused search query or sub-question, naming the system, document ID "
                                   "or procedure, e.g. 'Ledger DB failover manual promotion'")
    doc_type: Literal["policy", "manual", "sop", "incident", "faq"] | None = Field(
        None, description="Restrict to one source type")
    domain: Literal["compliance", "engineering", "operations", "support"] | None = Field(
        None, description="Restrict to one domain")


DESCRIPTION = """Search Acme's internal documents (policies, engineering manuals, SOPs, incident reports, \
support FAQ) with hybrid retrieval and re-ranking. Returns at most 6 passages. Use it to fetch the passages \
for a sub-question or for an entity you identified with another tool."""


def make_kb_search(retriever: HybridRetriever) -> StructuredTool:
    @guarded()
    async def search_knowledge_base(query: str, doc_type: str | None = None, domain: str | None = None):
        # The tool's filters are request filters: D-020's relax-and-retry never drops them.
        filters = {k: v for k, v in {"doc_type": doc_type, "domain": domain}.items() if v} or None
        res = await retriever.search(QueryPlan.fallback(query), filters, k=TOP_K)
        chunks = res.chunks[:TOP_K]
        if not chunks:
            return "No passages found.", []
        return format_context(chunks), chunks

    return StructuredTool.from_function(coroutine=search_knowledge_base, name="search_knowledge_base",
                                        description=DESCRIPTION, args_schema=KBSearchArgs,
                                        response_format="content_and_artifact")
