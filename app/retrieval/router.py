"""Query router (§B8.1): gpt-4o-mini structured output -> `QueryPlan`, with history-aware rewriting.

OpenAI strict structured output can't express a free-form `dict[str, str]`, so the model fills `_RouterOutput`
(filters as two enumerated optional fields) and the result is converted to the §B8.1 `QueryPlan`.

The model sets filters the user never asked for despite the prompt rule (12 of 30 golden questions, some of
them impossible pairs such as policy + operations that match nothing), so a filter is kept only when the
user's own message names that source type or domain, and a domain that no document of that type has is
dropped (D-020).
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

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
- sub_questions: for multi_hop only, the 2-4 simpler questions to chain, covering every part of the \
question (including any part about what a document says); otherwise []."""


def load_known_entities(tables: Path = TABLES) -> list[str]:
    """Canonical system, team and product names, given to the router so it emits linkable surface forms."""
    names: list[str] = []
    for table in ("systems.csv", "teams.csv", "products.csv"):
        path = tables / table
        if path.exists():
            with open(path, newline="") as f:
                names += [r["name"] for r in csv.DictReader(f)]
    return names


# What the user has to write for a router filter to be kept (D-020).
DOC_TYPE_CUES = {
    "policy": r"\bpolic(?:y|ies)\b|\bPOL-",
    "sop": r"\bSOPs?\b|\bstandard operating procedures?\b",
    "manual": r"\bmanuals?\b|\brunbooks?\b|\bguides?\b|\bMAN-",
    "incident": r"\bincidents?\b|\bpostmortems?\b|\bINC-",
    "faq": r"\bFAQs?\b|\bfrequently asked\b",
}
DOMAIN_CUES = {d: rf"\b{d}\b" for d in ("compliance", "engineering", "operations", "support")}


def load_filter_pairs(tables: Path = TABLES) -> set[tuple[str, str]] | None:
    """(doc_type, domain) pairs that at least one document has; None when the metadata table is missing."""
    path = tables / "documents_meta.csv"
    if not path.exists():
        return None
    with open(path, newline="") as f:
        pairs = {(r["doc_type"], r["domain"]) for r in csv.DictReader(f)}
    return pairs | {("faq", "support")}  # FAQ entries come from faq.json, not documents_meta


def explicit_filters(message: str, doc_type: str | None, domain: str | None,
                     pairs: set[tuple[str, str]] | None = None) -> dict[str, str]:
    """Keep only the router filters the user's message asks for, and never an impossible combination."""
    out: dict[str, str] = {}
    if doc_type and re.search(DOC_TYPE_CUES[doc_type], message, re.I):
        out["doc_type"] = doc_type
    if domain and re.search(DOMAIN_CUES[domain], message, re.I):
        out["domain"] = domain
    if pairs is not None and len(out) == 2 and (out["doc_type"], out["domain"]) not in pairs:
        del out["domain"]  # the source type is the more specific cue
    return out


ENTITY_MATCH = 85
GENERIC_WORDS = {"service", "system", "team", "cluster", "engine", "gateway", "portal", "platform", "data"}


def grounded_entities(entities: list[str], text: str) -> list[str]:
    """Keep the router's entities that the conversation actually mentions (D-021). The model also returns
    names it associates with the topic (a team for "failover SOP"), which become wrong graph seeds. An entity
    is kept when it fuzzily occurs in the text (`ledger database` -> Ledger DB) or its distinctive first word
    does (`Kafka` -> Kafka Cluster)."""
    low = text.lower()
    words = set(re.findall(r"[\w-]+", low))
    out = []
    for e in entities:
        first = re.split(r"[\s-]+", e.lower())[0]
        if (fuzz.partial_ratio(e.lower(), low) >= ENTITY_MATCH
                or (len(first) >= 4 and first not in GENERIC_WORDS and first in words)):
            out.append(e)
    return out


def format_history(history: list[tuple[str, str]]) -> str:
    turns = history[-HISTORY_TURNS:]
    return "\n".join(f"User: {u}\nAssistant: {a}" for u, a in turns)


class Router:
    def __init__(self, llm, known_entities: list[str] | None = None,
                 filter_pairs: set[tuple[str, str]] | None = None):
        self.llm = llm.with_structured_output(_RouterOutput)
        self.system = PROMPT.format(known=", ".join(known_entities or []) or "(none)")
        self.filter_pairs = filter_pairs

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
        # Checked against the user's message, not rewritten_query: the rewrite can add words of its own.
        filters = explicit_filters(message, out.doc_type, out.domain, self.filter_pairs)
        # Entities may come from the history too: a follow-up's "that team" resolves to a name said earlier.
        entities = grounded_entities(out.entities, f"{format_history(history or [])}\n{message}")
        # A dropped entity that made it into the rewrite or a sub-question would steer the agent to the wrong
        # node ("... and who manages the SRE-Operations team?" for an SOP-OPS-009 question, Phase 10): use the
        # user's message instead, and drop those sub-questions.
        dropped = [e.lower() for e in out.entities if e not in entities]
        rewritten = out.rewritten_query.strip()
        if not rewritten or any(e in rewritten.lower() for e in dropped):
            rewritten = message
        subs = [q for q in out.sub_questions if not any(e in q.lower() for e in dropped)]
        return QueryPlan(intent=out.intent, complexity=out.complexity, entities=entities, filters=filters,
                         rewritten_query=rewritten, sub_questions=subs)
