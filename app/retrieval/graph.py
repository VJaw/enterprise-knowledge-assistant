"""Graph retrieval (§B8.2, §B8.3): entity linking -> Cypher template by intent -> `[Gn]` facts + bridging.

1. Link: the router's entities (plus any doc IDs in the query) go to the `entity_names` full-text index. A hit
   becomes a seed when its Lucene score is near-exact (>= 2.5), or when it is a weaker hit (>= 1.0) whose name
   fuzzily contains the surface form (`ledger database` -> Ledger DB, `Kafka` -> Kafka Cluster). Ties go to
   System. Doc IDs (`SOP-OPS-014`) become Document seeds directly.
2. Traverse: the question's wording picks the §B8.3 templates (ownership, dependency, blast radius, incident,
   governance); with no cue a seed gets the generic 1-2 hop neighbourhood.
3. Output: facts as `RetrievedChunk(source_type="graph")` (they bypass the re-ranker, max 10), and bridging:
   the linked names / doc IDs become a Qdrant filter for a graph-anchored dense fetch (k = 10).

Any Neo4j error or the 3 s budget (§B12.3) raises `GraphUnavailable`; the hybrid retriever then degrades.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from qdrant_client.models import FieldCondition, Filter, MatchAny
from rapidfuzz import fuzz

from app.core.types import RetrievedChunk
from app.graph import cypher_templates as T
from app.graph.neo4j_client import Neo4jClient
from app.observability.langfuse import observe, update_span
from app.retrieval.dense import DenseRetriever, build_filter

DOC_ID = re.compile(r"\b(?:[A-Z]+-[A-Z]+-\d{3}|INC-\d{4}-\d{3}|FAQ-\d{3})\b", re.I)
LINK_MIN_SCORE = 2.5  # Lucene score of a (near-)exact name match; partial matches score ~1.4-1.9
LINK_FUZZY_SCORE = 1.0
LINK_FUZZY_RATIO = 85
LABEL_PRIORITY = {"System": 0, "Team": 1, "Person": 2, "Product": 3, "Component": 4}
MAX_FACTS = 10  # §B8.4
ANCHOR_K = 10  # §B8.2
BUDGET_S = 3.0  # §B12.3 Neo4j timeout, applied to the whole graph branch

CUES = {
    "owners": re.compile(r"\b(owns?|owned|owners?|ownership|teams?|manag\w*|contact|responsible|who)\b",
                         re.I),
    "dependencies": re.compile(r"\b(depend\w*|upstream|relies|rely|dependencies)\b", re.I),
    "blast_radius": re.compile(r"\b(blast radius|impact\w*|downstream|break\w*|fails?|failure of|goes down|"
                               r"is down|outage of)\b", re.I),
    "incidents": re.compile(r"\b(incidents?|outages?|sev ?\d|postmortems?|rca)\b", re.I),
    "policies": re.compile(r"\b(polic\w*|govern\w*|complian\w*|regulat\w*)\b", re.I),
}


class GraphUnavailable(Exception):
    pass


@dataclass
class Seed:
    name: str  # node name, or doc_id for Document seeds
    label: str


@dataclass
class GraphResult:
    facts: list[RetrievedChunk] = field(default_factory=list)
    chunks: list[RetrievedChunk] = field(default_factory=list)  # graph-anchored document chunks
    seeds: list[Seed] = field(default_factory=list)
    templates: list[str] = field(default_factory=list)


def pick_seed(surface: str, hits: list[dict]) -> Seed | None:
    ok = [h for h in hits if h["score"] >= LINK_MIN_SCORE or (
        h["score"] >= LINK_FUZZY_SCORE
        and fuzz.partial_ratio(surface.lower(), h["name"].lower()) >= LINK_FUZZY_RATIO)]
    if not ok:
        return None
    best = max(ok, key=lambda h: (round(h["score"], 2), -LABEL_PRIORITY.get(h["label"], 9)))
    return Seed(best["name"], best["label"])


def cues(question: str) -> set[str]:
    found = {name for name, rx in CUES.items() if rx.search(question)}
    if "dependencies" in found:
        found.discard("owners")  # dependency rows already carry owner team + manager
    return found


def anchor_filter(filters: dict | None, names: list[str], doc_ids: list[str]) -> Filter:
    """User filters must hold; at least one of (entities in names, doc_id in doc_ids) should (§B8.2)."""
    base = build_filter(filters)
    should = []
    if names:
        should.append(FieldCondition(key="entities", match=MatchAny(any=names)))
    if doc_ids:
        should.append(FieldCondition(key="doc_id", match=MatchAny(any=doc_ids)))
    return Filter(must=base.must if base else None, should=should)


def path_text(row: dict) -> str:
    """`Billing Service -[DEPENDS_ON]-> Ledger DB <-[AFFECTS]- INC-2025-012`: arrows follow edge direction."""
    via, starts = row["via"], row.get("rel_from") or []
    parts = [via[0]]
    for i, (rel, node) in enumerate(zip(row["rels"], via[1:], strict=False)):
        forward = i >= len(starts) or starts[i] == via[i]
        parts += [f"-[{rel}]->" if forward else f"<-[{rel}]-", node]
    return " ".join(str(p) for p in parts)


class GraphRetriever:
    def __init__(self, graph: Neo4jClient, dense: DenseRetriever, budget_s: float = BUDGET_S):
        self.graph = graph
        self.dense = dense
        self.budget_s = budget_s

    async def link(self, entities: list[str], query: str) -> list[Seed]:
        doc_ids = {m.upper() for text in [query, *entities] for m in DOC_ID.findall(text)}
        names = [e for e in entities if e.strip() and not DOC_ID.fullmatch(e.strip())]
        hits = await asyncio.gather(*(T.link_entity(self.graph, e) for e in names))
        seeds = [Seed(d, "Document") for d in sorted(doc_ids)]
        for surface, h in zip(names, hits, strict=True):
            s = pick_seed(surface, h)
            if s and all(s.name != x.name for x in seeds):
                seeds.append(s)
        return seeds

    async def _system_facts(self, seed: str, wanted: set[str]) -> tuple[list, list[str], list[str]]:
        """Facts for one System seed (templates run concurrently), plus bridged system names and doc ids."""
        queries = {
            "owners": lambda: T.owners(self.graph, seed),
            "dependencies": lambda: T.dependencies(self.graph, seed, depth=1),
            "blast_radius": lambda: T.blast_radius(self.graph, seed, depth=2),
            "incidents": lambda: T.incidents_for(self.graph, seed),
            "policies": lambda: T.policies_for(self.graph, seed),
        }
        run = [n for n in queries if n in wanted]
        results = dict(zip(run, await asyncio.gather(*(queries[n]() for n in run)), strict=True))
        facts, names, docs = [], [], []
        for r in results.get("owners", []):
            facts.append((f"{r['team']} team OWNS {r['system']}; manager: {r['manager']}", r["source"],
                          "owners"))
        for r in results.get("dependencies", []):
            facts.append((f"{seed} DEPENDS_ON {r['system']} (tier {r['tier']}); {r['owner_team']} team OWNS "
                           f"{r['system']}; manager: {r['manager']}",
                           "system_dependencies.csv, system_owners.csv", "dependencies"))
            names.append(r["system"])
        for r in results.get("blast_radius", []):
            hops = f"{r['hops']} hop{'s' if r['hops'] > 1 else ''}"
            facts.append((f"{r['system']} (tier {r['tier']}) depends on {seed} ({hops}) and is impacted "
                           "if it fails", r["source"], "blast_radius"))
            names.append(r["system"])
        for r in results.get("incidents", []):
            facts.append((f"{r['incident_id']} ({r['severity']}, {r['date']}) AFFECTS {seed}: {r['title']}",
                           r["source"], "incidents_for"))
            docs.append(r["incident_id"])
        for r in results.get("policies", []):
            facts.append((f'{r["doc_id"]} "{r["title"]}" GOVERNS {seed}', r["source"], "policies_for"))
            docs.append(r["doc_id"])
        return facts, names, docs

    async def _neighbourhood_facts(self, seed: str) -> tuple[list, list[str], list[str]]:
        rows = await T.neighbourhood(self.graph, seed, depth=2)
        docs = [r["name"] for r in rows if r["name"] and DOC_ID.fullmatch(str(r["name"]))]
        return [(path_text(r), "neo4j neighbourhood", "neighbourhood") for r in rows], [], docs

    async def _traverse(self, seeds: list[Seed], question: str
                        ) -> tuple[list, list[str], list[str], list[str]]:
        wanted = cues(question)
        jobs, names, docs = [], [], []
        for s in seeds:
            if s.label == "Document":
                docs.append(s.name)
                systems = []
                if wanted & {"owners", "dependencies"}:
                    systems = await T.doc_systems(self.graph, s.name)
                names += systems
                jobs += ([self._system_facts(x, wanted) for x in systems]
                         or [self._neighbourhood_facts(s.name)])
            elif s.label == "System" and wanted:
                names.append(s.name)
                jobs.append(self._system_facts(s.name, wanted))
            else:
                names.append(s.name)
                jobs.append(self._neighbourhood_facts(s.name))
        facts = []
        for f, n, d in await asyncio.gather(*jobs):
            facts += f
            names += n
            docs += d
        templates = sorted(wanted) if wanted else ["neighbourhood"]
        return facts, list(dict.fromkeys(names)), list(dict.fromkeys(docs)), templates

    async def _run(self, query: str, entities: list[str], filters: dict | None) -> GraphResult:
        seeds = await self.link(entities, query)
        if not seeds:
            return GraphResult()
        raw, names, docs, templates = await self._traverse(seeds, query)
        seen, facts = set(), []
        for text, source, template in raw:
            if text not in seen and len(facts) < MAX_FACTS:
                seen.add(text)
                facts.append(RetrievedChunk(chunk_id=f"graph:{len(facts) + 1}", doc_id="graph", text=text,
                                            score=1.0, source_type="graph",
                                            metadata={"source": source, "template": template}))
        chunks = []
        if names or docs:
            flt = anchor_filter(filters, names, docs)
            chunks = await asyncio.to_thread(self.dense.search, query, flt, ANCHOR_K)
        return GraphResult(facts=facts, chunks=chunks, seeds=seeds, templates=templates)

    @observe(name="neo4j.traverse", as_type="retriever", capture_output=False)
    async def retrieve(self, query: str, entities: list[str], filters: dict | None = None) -> GraphResult:
        try:
            res = await asyncio.wait_for(self._run(query, entities, filters), timeout=self.budget_s)
        except ValueError:
            raise  # bad filter keys -> 422, not a graph outage
        except Exception as e:  # noqa: BLE001 - Aura down/paused/slow: degrade, don't fail (§B12.3)
            update_span(level="WARNING", status_message=f"graph_unavailable: {type(e).__name__}")
            raise GraphUnavailable(type(e).__name__) from e
        update_span(output=[f.text for f in res.facts],
                    metadata={"seeds": [f"{s.label}:{s.name}" for s in res.seeds], "templates": res.templates,
                              "facts": len(res.facts), "anchored_chunks": len(res.chunks)})
        return res
