"""Query router (§B8.1): gpt-4o-mini structured output -> `QueryPlan`, with history-aware rewriting.

OpenAI strict structured output can't express a free-form `dict[str, str]`, so the model fills `_RouterOutput`
(filters as two enumerated optional fields) and the result is converted to the §B8.1 `QueryPlan`.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.observability.langfuse import llm_callbacks

log = logging.getLogger(__name__)

TABLES = Path("data/raw/tables")
HISTORY_TURNS = 3  # user/assistant exchanges shown to the rewriter

Intent = Literal["factual", "multi_hop", "analytical", "structured_data", "external", "chit_chat"]


class QueryPlan(BaseModel):
    intent: Intent
    complexity: Literal["simple", "complex"]
    entities: list[str]  # surface forms, e.g. ["Payment Gateway"]
    filters: dict[str, str]  # e.g. {"doc_type": "sop", "domain": "operations"}
    rewritten_query: str  # self-contained, history-resolved
    sub_questions: list[str]  # for multi_hop decomposition

    @classmethod
    def fallback(cls, message: str) -> QueryPlan:
        """Used when the router call fails: retrieval still runs on the raw message."""
        return cls(intent="factual", complexity="simple", entities=[], filters={}, rewritten_query=message,
                   sub_questions=[])


class _RouterOutput(BaseModel):
    intent: Intent
    complexity: Literal["simple", "complex"]
    entities: list[str] = Field(description="Named systems, teams, people, products, components or document "
                                            "IDs, using the canonical names listed when they match")
    doc_type: Literal["policy", "manual", "sop", "incident", "faq"] | None = Field(
        description="Only when the user explicitly restricts the source type")
    domain: Literal["compliance", "engineering", "operations", "support"] | None = Field(
        description="Only when the user explicitly restricts the domain")
    rewritten_query: str
    sub_questions: list[str]


PROMPT = """You plan retrieval for the Acme Corp knowledge assistant (policies, engineering manuals, SOPs, \
incident reports, support FAQ, plus a graph of systems, owners, dependencies, incidents and policies).

Return:
- intent: factual (one fact or procedure) | multi_hop (needs chaining facts, e.g. owners of dependencies) | \
analytical (compare, summarise, trends) | structured_data (counts/aggregates over tickets, transactions, \
employees) | external (needs the public web) | chit_chat (greetings, thanks).
- complexity: simple or complex.
- entities: every named system, team, person, product, component or document ID (e.g. SOP-OPS-014, \
INC-2025-031) in the question. Use the canonical spelling from this list when one matches:
{known}
- doc_type / domain: null unless the user explicitly restricts the source ("according to the SOP", \
"in incident reports").
- rewritten_query: the question rewritten to be self-contained, resolving pronouns and references from the \
conversation. Keep IDs, commands and names verbatim. Don't answer it.
- sub_questions: for multi_hop only, the 2-3 simpler questions to chain; otherwise []."""


def load_known_entities(tables: Path = TABLES) -> list[str]:
    """Canonical system, team and product names, given to the router so it emits linkable surface forms."""
    names: list[str] = []
    for table in ("systems.csv", "teams.csv", "products.csv"):
        path = tables / table
        if path.exists():
            with open(path, newline="") as f:
                names += [r["name"] for r in csv.DictReader(f)]
    return names


def format_history(history: list[tuple[str, str]]) -> str:
    turns = history[-HISTORY_TURNS:]
    return "\n".join(f"User: {u}\nAssistant: {a}" for u, a in turns)


class Router:
    def __init__(self, llm, known_entities: list[str] | None = None):
        self.llm = llm.with_structured_output(_RouterOutput)
        self.system = PROMPT.format(known=", ".join(known_entities or []) or "(none)")

    async def plan(self, message: str, history: list[tuple[str, str]] | None = None) -> QueryPlan:
        human = f"Question: {message}"
        if history:
            human = f"Conversation so far:\n{format_history(history)}\n\n{human}"
        try:
            out: _RouterOutput = await self.llm.ainvoke(
                [SystemMessage(self.system), HumanMessage(human)],
                config={"callbacks": llm_callbacks(), "run_name": "router"})
        except Exception as e:  # noqa: BLE001 - routing must never fail the request
            log.warning("router failed (%s): falling back to the raw message", type(e).__name__)
            return QueryPlan.fallback(message)
        filters = {k: v for k, v in (("doc_type", out.doc_type), ("domain", out.domain)) if v}
        return QueryPlan(intent=out.intent, complexity=out.complexity, entities=out.entities, filters=filters,
                         rewritten_query=out.rewritten_query.strip() or message,
                         sub_questions=out.sub_questions)
