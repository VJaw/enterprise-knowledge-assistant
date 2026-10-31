"""GraphRetriever with a fake Neo4j client, HybridRetriever flags/degradation, and the router; no network."""
import asyncio

import pytest
from langchain_core.messages import HumanMessage

from app.core.types import RetrievedChunk
from app.retrieval import graph as G
from app.retrieval.graph import GraphResult, GraphRetriever, GraphUnavailable, anchor_filter, cues, pick_seed
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.router import (
    QueryPlan,
    Router,
    _RouterOutput,
    explicit_filters,
    grounded_entities,
    load_filter_pairs,
)

LINKS = {
    "Payment Gateway": [{"name": "Payment Gateway", "label": "System", "score": 3.39},
                        {"name": "API Gateway", "label": "System", "score": 1.7}],
    "Kafka": [{"name": "Kafka topic", "label": "Component", "score": 1.7},
              {"name": "Kafka Cluster", "label": "System", "score": 1.7}],
    "customer logs": [{"name": "Customer-Support", "label": "Team", "score": 1.7}],
    "Priya Rao": [{"name": "Priya Rao", "label": "Person", "score": 3.86}],
    "Data-Platform": [{"name": "Data-Platform", "label": "Team", "score": 3.5}],
}


def chunk(cid, source_type="document", **meta):
    return RetrievedChunk(chunk_id=cid, doc_id=cid.split("::")[0], text=f"text {cid}", score=0.5,
                          source_type=source_type, metadata={"title": cid, **meta})


class FakeNeo4j:
    def __init__(self, delay: float = 0.0, fail: bool = False):
        self.delay, self.fail = delay, fail
        self.queries = []

    async def read(self, cypher, **params):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("aura paused")
        self.queries.append((cypher, params))
        if "entity_names" in cypher:
            surface = params["query"].replace("\\", "")
            return LINKS.get(surface, [])
        if "DEPENDS_ON*1..1]->" in cypher:
            return [{"system": "Ledger DB", "tier": "1", "hops": 1, "owner_team": "Data-Platform",
                     "manager": "Anthony Gonzalez", "source": "system_dependencies.csv"},
                    {"system": "Secrets Vault", "tier": "1", "hops": 1, "owner_team": "Identity-Access",
                     "manager": "Christopher Davis", "source": "system_dependencies.csv"}]
        if "AFFECTS" in cypher:
            return [{"incident_id": "INC-2025-031", "title": "Payment latency spike", "severity": "SEV1",
                     "date": "2025-08-01", "source": "incident_systems.csv"}]
        if "<-[o:OWNS]-" in cypher:
            return [{"system": params["name"], "team": "Payments-Core", "manager": "Priya Rao",
                     "source": "system_owners.csv"}]
        if "(t:Team {name: $name})" in cypher:
            return [{"team": "Data-Platform", "manager": "Anthony Gonzalez", "members": 7,
                     "systems": ["Ledger DB", "Data Warehouse"], "source": "teams.csv, employees.csv"}]
        if "MENTIONS]->(s:System)" in cypher:
            return [{"system": "Ledger DB"}]
        if "seed.doc_id" in cypher:
            return [{"name": "Payments-Core", "label": "Team", "hops": 1, "rels": ["MEMBER_OF"],
                     "rel_from": ["Priya Rao"], "via": ["Priya Rao", "Payments-Core"]},
                    {"name": "INC-2025-031", "label": "Document", "hops": 2,
                     "rels": ["MEMBER_OF", "RESOLVED_BY"],
                     "via": ["Priya Rao", "Payments-Core", "INC-2025-031"]}]
        return []


class FakeDense:
    def __init__(self, chunks=None):
        self.chunks = chunks or [chunk("MAN-ENG-001::003")]
        self.filters = []

    def search(self, query, flt, k):
        self.filters.append(flt)
        return self.chunks[:k]

    async def retrieve(self, query, filters=None, k=5):
        return self.chunks[:k]


def test_pick_seed_thresholds_and_tie_break():
    assert pick_seed("Payment Gateway", LINKS["Payment Gateway"]).name == "Payment Gateway"
    assert pick_seed("Kafka", LINKS["Kafka"]).name == "Kafka Cluster"  # tie at 1.7 -> System wins
    weak = [{"name": "Ledger DB", "label": "System", "score": 1.7}]
    assert pick_seed("ledger database", weak).name == "Ledger DB"
    assert pick_seed("customer logs", LINKS["customer logs"]) is None  # weak hit, name doesn't match
    assert pick_seed("anything", []) is None


def test_path_text_follows_edge_direction():
    row = {"via": ["Ledger DB", "Billing Service", "INC-2025-012"], "rels": ["DEPENDS_ON", "AFFECTS"],
           "rel_from": ["Billing Service", "INC-2025-012"]}
    assert G.path_text(row) == "Ledger DB <-[DEPENDS_ON]- Billing Service <-[AFFECTS]- INC-2025-012"


def test_cues_pick_templates():
    assert cues("Which teams own the systems Payment Gateway depends on?") == {"dependencies"}
    assert cues("Who owns the Ledger DB?") == {"owners"}
    assert cues("Which incidents affected the Ledger DB?") == {"incidents"}
    assert cues("Which policies govern the Secrets Vault?") == {"policies"}
    assert "blast_radius" in cues("What is impacted if Auth Service goes down?")
    assert cues("Tell me about the Ledger DB") == set()


def test_anchor_filter_must_and_should():
    flt = anchor_filter({"doc_type": "sop"}, ["Ledger DB"], ["INC-2025-031"])
    assert [c.key for c in flt.must] == ["doc_type"]
    assert {c.key for c in flt.should} == {"entities", "doc_id"}
    assert anchor_filter(None, ["Ledger DB"], []).must is None


async def test_multi_hop_dependencies_give_owner_facts_and_bridge():
    dense = FakeDense()
    res = await GraphRetriever(FakeNeo4j(), dense).retrieve(
        "Which teams own the systems Payment Gateway depends on?", ["Payment Gateway"])
    assert [f.text for f in res.facts] == [
        "Payment Gateway DEPENDS_ON Ledger DB (tier 1); Data-Platform team OWNS Ledger DB; "
        "manager: Anthony Gonzalez",
        "Payment Gateway DEPENDS_ON Secrets Vault (tier 1); Identity-Access team OWNS Secrets Vault; "
        "manager: Christopher Davis"]
    assert all(f.source_type == "graph" and f.metadata["template"] == "dependencies" for f in res.facts)
    assert res.chunks and res.templates == ["dependencies"]
    should = dense.filters[0].should[0]
    assert should.key == "entities"
    assert set(should.match.any) == {"Payment Gateway", "Ledger DB", "Secrets Vault"}


async def test_incidents_bridge_doc_ids_and_doc_seed_owners():
    dense = FakeDense()
    res = await GraphRetriever(FakeNeo4j(), dense).retrieve("Which incidents affected the Payment Gateway?",
                                                            ["Payment Gateway"])
    assert res.facts[0].text.startswith("INC-2025-031 (SEV1, 2025-08-01) AFFECTS Payment Gateway")
    assert {c.key: c.match.any for c in dense.filters[0].should}["doc_id"] == ["INC-2025-031"]

    res = await GraphRetriever(FakeNeo4j(), FakeDense()).retrieve(
        "Who manages the team that owns the system in sop-ops-014?", [])
    assert [s.name for s in res.seeds] == ["SOP-OPS-014"]
    assert res.facts[0].text == "Payments-Core team OWNS Ledger DB; manager: Priya Rao"


async def test_team_seed_with_a_manager_cue_uses_the_team_template():
    # Follow-up "and who manages that team?" rewritten to name the team: the neighbourhood's first 10 paths
    # don't reach the MANAGES edge, the team template does (D-021).
    res = await GraphRetriever(FakeNeo4j(), FakeDense()).retrieve("Who manages the Data-Platform team?",
                                                                  ["Data-Platform"])
    assert [f.text for f in res.facts] == [
        "Anthony Gonzalez MANAGES Data-Platform team; Data-Platform has 7 members and OWNS Data Warehouse, "
        "Ledger DB"]
    assert res.facts[0].metadata["template"] == "team"


async def test_generic_neighbourhood_and_no_seed():
    res = await GraphRetriever(FakeNeo4j(), FakeDense()).retrieve("Tell me about Priya Rao", ["Priya Rao"])
    assert res.facts[0].text == "Priya Rao -[MEMBER_OF]-> Payments-Core"
    assert res.templates == ["neighbourhood"]
    dense = FakeDense()
    empty = await GraphRetriever(FakeNeo4j(), dense).retrieve("customer log retention", ["customer logs"])
    assert empty.facts == [] and empty.chunks == [] and dense.filters == []


async def test_facts_capped_at_ten(monkeypatch):
    many = [{"incident_id": f"INC-2025-{i:03d}", "title": "t", "severity": "SEV2", "date": "2025-01-01",
             "source": "incident_systems.csv"} for i in range(15)]

    async def incidents(client, name):
        return many
    monkeypatch.setattr(G.T, "incidents_for", incidents)
    res = await GraphRetriever(FakeNeo4j(), FakeDense()).retrieve("incidents on Payment Gateway",
                                                                  ["Payment Gateway"])
    assert len(res.facts) == G.MAX_FACTS


async def test_graph_errors_and_timeouts_raise_unavailable():
    with pytest.raises(GraphUnavailable):
        await GraphRetriever(FakeNeo4j(fail=True), FakeDense()).retrieve("Who owns X?", ["Payment Gateway"])
    with pytest.raises(GraphUnavailable):
        slow = GraphRetriever(FakeNeo4j(delay=0.2), FakeDense(), budget_s=0.05)
        await slow.retrieve("q", ["Payment Gateway"])


class FakeBranch:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = 0

    async def retrieve(self, query, filters=None, k=20):
        self.calls += 1
        return self.chunks


class FakeGraph:
    def __init__(self, result=None, error=None):
        self.result, self.error = result or GraphResult(), error
        self.calls = 0

    async def retrieve(self, query, entities, filters=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class FakeReranker:
    def __init__(self):
        self.scored = []

    async def ascore(self, query, chunks):
        self.scored.append([c.chunk_id for c in chunks])
        return {c.chunk_id: 1.0 - "ABCD".index(c.chunk_id[0]) * 0.1 for c in chunks}

    def select(self, chunks, scores, top_k=6):
        return sorted(chunks, key=lambda c: scores[c.chunk_id], reverse=True)[:top_k]


PLAN = QueryPlan(intent="multi_hop", complexity="complex", entities=["Payment Gateway"], filters={},
                 rewritten_query="Which teams own the systems Payment Gateway depends on?", sub_questions=[])


def hybrid(graph=None, available=True, **flags):
    dense = FakeBranch([chunk("A::0"), chunk("B::0")])
    bm25 = FakeBranch([chunk("B::0"), chunk("C::0")])
    graph = graph or FakeGraph(GraphResult(facts=[chunk("graph:1", source_type="graph")],
                                           chunks=[chunk("D::0")]))
    return HybridRetriever(dense, bm25, graph, FakeReranker(), graph_available=lambda: available, **flags)


async def test_hybrid_fuses_reranks_and_returns_facts():
    h = hybrid()
    res = await h.search(PLAN)
    assert [c.chunk_id for c in res.chunks] == ["A::0", "B::0", "C::0", "D::0"]
    assert [f.chunk_id for f in res.facts] == ["graph:1"] and res.flags == []
    # dense + BM25 candidates scored first; only the graph newcomer afterwards
    assert h.reranker.scored == [["B::0", "A::0", "C::0"], ["D::0"]]
    assert {"dense", "bm25", "graph", "total", "rerank_wait"} <= set(res.timings_ms)


async def test_hybrid_degrades_when_graph_unavailable():
    res = await hybrid(available=False).search(PLAN)
    assert res.flags == ["graph_unavailable"] and res.facts == [] and len(res.chunks) == 3
    res = await hybrid(graph=FakeGraph(error=GraphUnavailable("ServiceUnavailable"))).search(PLAN)
    assert res.flags == ["graph_unavailable"] and [c.chunk_id for c in res.chunks] == ["A::0", "B::0", "C::0"]


async def test_hybrid_feature_flags():
    h = hybrid(use_bm25=False, use_graph=False, use_rerank=False)
    res = await h.search(PLAN, k=6)
    assert [c.chunk_id for c in res.chunks] == ["A::0", "B::0"] and res.flags == []
    assert h.bm25.calls == 0 and h.graph.calls == 0 and h.reranker.scored == []


async def test_hybrid_filters_merge_and_validate():
    h = hybrid()
    plan = PLAN.model_copy(update={"filters": {"doc_type": "manual", "domain": "engineering"}})
    calls = []

    async def spy(query, filters=None, k=20):
        calls.append(filters)
        return []
    h.dense.retrieve = spy
    await h.search(plan, {"doc_type": "sop"})
    assert calls == [{"doc_type": "sop", "domain": "engineering"}]  # the request's filter wins
    with pytest.raises(ValueError):
        await h.search(PLAN, {"owner": "TEAM-01"})


async def test_hybrid_relaxes_router_filters_that_match_nothing():
    h = hybrid(use_graph=False)
    calls = []

    def empty_when_filtered(branch):
        async def retrieve(query, filters=None, k=20):
            calls.append(filters)
            return [] if filters and "doc_type" in filters else branch.chunks
        return retrieve
    h.dense.retrieve, h.bm25.retrieve = empty_when_filtered(h.dense), empty_when_filtered(h.bm25)
    plan = PLAN.model_copy(update={"filters": {"doc_type": "policy"}})

    res = await h.search(plan, {"domain": "operations"})
    assert res.flags == ["router_filters_relaxed"] and [c.chunk_id for c in res.chunks] == ["A::0", "B::0",
                                                                                              "C::0"]
    # first with the merged filters, then again with the request's filters only
    assert calls == [{"doc_type": "policy", "domain": "operations"}] * 2 + [{"domain": "operations"}] * 2

    calls.clear()  # request filters alone are never relaxed: an empty result stands
    res = await h.search(PLAN, {"doc_type": "policy"})
    assert res.chunks == [] and res.flags == [] and len(calls) == 2


class FakeStructuredLLM:
    def __init__(self, out=None, error=None):
        self.out, self.error = out, error
        self.messages = None

    def with_structured_output(self, schema):
        assert schema is _RouterOutput
        return self

    async def ainvoke(self, messages, config=None):
        self.messages = messages
        if self.error:
            raise self.error
        return self.out


async def test_router_plan_history_and_filters():
    out = _RouterOutput(intent="multi_hop", complexity="complex", entities=["Ledger DB"], doc_type="sop",
                        domain=None, rewritten_query="Who owns the Ledger DB?", sub_questions=["a", "b"])
    llm = FakeStructuredLLM(out)
    router = Router(llm, ["Ledger DB", "Payments-Core"])
    plan = await router.plan("who owns it, per the SOP?",
                             [("Tell me about the Ledger DB", "It stores transactions [1].")])
    assert plan.filters == {"doc_type": "sop"} and plan.rewritten_query == "Who owns the Ledger DB?"
    assert plan.intent == "multi_hop" and plan.entities == ["Ledger DB"]
    system, human = llm.messages
    assert "Ledger DB, Payments-Core" in system.content
    assert isinstance(human, HumanMessage) and "User: Tell me about the Ledger DB" in human.content
    assert human.content.endswith("Question: who owns it, per the SOP?")


async def test_router_drops_filters_the_user_did_not_ask_for():
    # Golden row 8: the model returned {policy, operations}, which matches no document.
    out = _RouterOutput(intent="factual", complexity="simple", entities=[], doc_type="policy",
                        domain="operations", rewritten_query="Who approves refunds over $5,000?",
                        sub_questions=[])
    plan = await Router(FakeStructuredLLM(out)).plan("Who has to approve a refund of more than $5,000?")
    assert plan.filters == {}


@pytest.mark.parametrize(("message", "doc_type", "domain", "expected"), [
    ("How quickly must an emergency secret rotation happen?", "policy", "operations", {}),
    ("What caused the spike in INC-2025-031?", "incident", "operations", {"doc_type": "incident"}),
    ("What does the access control policy say about MFA?", "policy", None, {"doc_type": "policy"}),
    ("Per POL-COMP-001, how long are logs kept?", "policy", "compliance", {"doc_type": "policy"}),
    ("Which support SOPs cover refunds?", "sop", "support", {"doc_type": "sop", "domain": "support"}),
    # no policy has the operations domain, so the domain goes
    ("Which operations policies cover backups?", "policy", "operations", {"doc_type": "policy"}),
    ("What does the FAQ say about refunds?", "faq", "support", {"doc_type": "faq"}),
])
def test_explicit_filters(message, doc_type, domain, expected):
    assert explicit_filters(message, doc_type, domain, load_filter_pairs()) == expected


def test_load_filter_pairs():
    pairs = load_filter_pairs()
    assert ("policy", "compliance") in pairs and ("faq", "support") in pairs
    assert ("policy", "operations") not in pairs and ("incident", "support") not in pairs


async def test_router_falls_back_on_error():
    plan = await Router(FakeStructuredLLM(error=TimeoutError())).plan("What is RTO?")
    assert plan == QueryPlan.fallback("What is RTO?") and plan.intent == "factual"


@pytest.mark.parametrize(("entities", "text", "kept"), [
    # §B9.4: the model added teams the question never names (D-021)
    (["SRE-Operations", "Support Desk"],
     "Which team owns the system that caused the most SEV-1 tickets last quarter, and what does their "
     "failover SOP say about manual promotion?", []),
    (["Kafka Cluster"], "Which incidents involved Kafka?", ["Kafka Cluster"]),
    (["Ledger DB"], "what is the ledger database RTO?", ["Ledger DB"]),
    (["Fraud Detection Engine"], "chargebacks and the fraud engine", ["Fraud Detection Engine"]),
    (["Data-Platform"], "User: who owns Ledger DB?\nAssistant: The Data-Platform team [G1].\nand who manages "
                        "that team?", ["Data-Platform"]),
    (["SOP-OPS-014", "Payments-Core"], "steps in SOP-OPS-014", ["SOP-OPS-014"]),
])
def test_grounded_entities(entities, text, kept):
    assert grounded_entities(entities, text) == kept


async def test_router_drops_entities_the_conversation_never_mentions():
    out = _RouterOutput(intent="multi_hop", complexity="complex", entities=["Ledger DB", "SRE-Operations"],
                        doc_type=None, domain=None, rewritten_query="Who owns the Ledger DB?",
                        sub_questions=[])
    plan = await Router(FakeStructuredLLM(out)).plan("who owns it?",
                                                     [("Tell me about the Ledger DB", "OK [1].")])
    assert plan.entities == ["Ledger DB"]
