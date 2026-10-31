"""`query_graph`: a named Cypher template + an entity -> `[Gn]` facts (§B9.3).

Templates only: no free-form Cypher reaches Neo4j, values are parameters, and every query runs in a read
transaction with the 3 s Neo4j timeout (`Neo4jClient.read`). The entity is linked through the same full-text
index and thresholds as graph retrieval (D-016); a document ID resolves to the systems it mentions.
"""
from __future__ import annotations

import re

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.core.types import RetrievedChunk
from app.graph import cypher_templates as T
from app.retrieval.context import format_items, label
from app.retrieval.graph import DOC_ID, MAX_FACTS, GraphRetriever, Seed, pick_seed
from app.tools.common import error, guarded

SYSTEM_TEMPLATES = ("owners", "dependencies", "blast_radius", "incidents", "policies")
TEMPLATES = (*SYSTEM_TEMPLATES, "team", "neighbourhood")
KEY_ID = re.compile(r"(?:SYS|TEAM|EMP|PRD)-\d+", re.I)


class GraphQueryArgs(BaseModel):
    template: str = Field(description=f"One of: {', '.join(TEMPLATES)}")
    entity: str = Field(description="A system name (e.g. 'Ledger DB'), a team name (e.g. 'Data-Platform') "
                                    "for 'team', a document ID (e.g. 'SOP-OPS-014') or a system/team id "
                                    "(e.g. 'SYS-002')")


DESCRIPTION = """Query Acme's knowledge graph with a fixed template. It is the authoritative source for who \
owns a system, team managers, dependencies, incidents per system and which policies govern a system. \
Templates:
- owners(system): the team that owns it and the team's manager
- dependencies(system): the systems it depends on, each with owner team and manager
- blast_radius(system): the systems impacted if it fails (1-2 hops upstream)
- incidents(system): incident reports that affected it
- policies(system): policies that govern it
- team(team): the team's manager, head count and the systems it owns
- neighbourhood(any entity): everything 1-2 hops away
A document ID resolves to the systems the document is about."""


def make_graph_query(graph: GraphRetriever) -> StructuredTool:
    async def facts_for(template: str, entity: str) -> list[tuple[str, str, str]] | str:
        doc = DOC_ID.fullmatch(entity.strip())
        if doc and template in SYSTEM_TEMPLATES:
            systems = await T.doc_systems(graph.graph, entity.strip().upper())
            if not systems:
                return f"document {entity!r} is not linked to any system"
            out = []
            for s in systems:
                out += (await graph.system_facts(s, {template}))[0]
            return out
        if doc and template == "neighbourhood":
            return (await graph.neighbourhood_facts(entity.strip().upper()))[0]
        if KEY_ID.fullmatch(entity.strip()):  # an id from a sql_query result, e.g. SYS-002
            rows = await T.by_key(graph.graph, entity.strip().upper())
            if not rows:
                return f"no graph entity has the id {entity!r}"
            seed = Seed(rows[0]["name"], rows[0]["label"])
        else:
            seed = pick_seed(entity, await T.link_entity(graph.graph, entity))
        if seed is None:
            return f"no graph entity matches {entity!r}"
        if template == "neighbourhood":
            return (await graph.neighbourhood_facts(seed.name))[0]
        if seed.label == "Team" and template in ("owners", "team"):
            return (await graph.team_facts(seed.name))[0]
        if template == "team":
            return f"{seed.name!r} is a {seed.label}, not a Team"
        if seed.label != "System":
            return f"{seed.name!r} is a {seed.label}; template {template!r} needs a System"
        return (await graph.system_facts(seed.name, {template}))[0]

    @guarded()
    async def query_graph(template: str, entity: str):
        if template not in TEMPLATES:
            return error(f"unknown template {template!r}; use one of: {', '.join(TEMPLATES)}")
        rows = await facts_for(template, entity)
        if isinstance(rows, str):
            return error(rows)
        if not rows:
            return f"No {template} facts for {entity!r}.", []
        facts = [RetrievedChunk(chunk_id=f"graph:{template}:{i}", doc_id="graph", text=text, score=1.0,
                                source_type="graph", metadata={"source": source, "template": tmpl})
                 for i, (text, source, tmpl) in enumerate(dict.fromkeys(rows), 1)][:MAX_FACTS]
        return format_items(label([], facts)[0]), facts

    return StructuredTool.from_function(coroutine=query_graph, name="query_graph", description=DESCRIPTION,
                                        args_schema=GraphQueryArgs, response_format="content_and_artifact")
