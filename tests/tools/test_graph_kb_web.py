"""`query_graph`, `search_knowledge_base` and `web_search` with fake back ends."""
import pytest

from app.core.types import RetrievedChunk
from app.graph import cypher_templates as T
from app.retrieval.graph import GraphRetriever
from app.retrieval.hybrid import HybridResult
from app.tools.graph_query import make_graph_query
from app.tools.kb_search import make_kb_search
from app.tools.web_search import make_web_search


async def call(tool, **args):
    msg = await tool.ainvoke({"name": tool.name, "args": args, "id": "1", "type": "tool_call"})
    return msg.content, msg.artifact


# ---------------------------------------------------------------- query_graph

class FakeNeo4j:
    LINKS = {"Ledger DB": ("Ledger DB", "System"), "Data-Platform": ("Data-Platform", "Team"),
             "Anthony Gonzalez": ("Anthony Gonzalez", "Person")}

    def __init__(self):
        self.queries = []
        self.by_key = {}

    async def read(self, cypher, **params):
        self.queries.append(cypher)
        if cypher == T.LINK_ENTITY:
            hit = self.LINKS.get(params["query"].replace("\\", ""))
            return [{"name": hit[0], "label": hit[1], "score": 3.2}] if hit else []
        if cypher == T.OWNERS:
            return [{"system": params["name"], "team": "Data-Platform", "manager": "Anthony Gonzalez",
                     "source": "system_owners.csv"}]
        if cypher == T.TEAM:
            return [{"team": "Data-Platform", "manager": "Anthony Gonzalez", "members": 7,
                     "systems": ["Ledger DB", "Data Warehouse"], "source": "teams.csv, employees.csv"}]
        if cypher == T.BY_KEY:
            hit = self.by_key.get(params["id"])
            return [{"name": hit[0], "label": hit[1]}] if hit else []
        if cypher == T.DOC_SYSTEMS:
            return [{"system": "Ledger DB"}] if params["doc_id"] == "SOP-OPS-014" else []
        return []


@pytest.fixture
def graph_tool():
    neo = FakeNeo4j()
    return make_graph_query(GraphRetriever(neo, dense=None)), neo


async def test_unknown_template_is_an_error(graph_tool):
    tool, neo = graph_tool
    content, artifact = await call(tool, template="free_cypher", entity="MATCH (n) DETACH DELETE n")
    assert content.startswith("error: unknown template 'free_cypher'; use one of: owners, dependencies")
    assert artifact == [] and neo.queries == []  # nothing reached Neo4j


async def test_owners_of_a_system(graph_tool):
    content, [fact] = await call(graph_tool[0], template="owners", entity="Ledger DB")
    assert content == ("[G1] Data-Platform team OWNS Ledger DB; manager: Anthony Gonzalez  "
                       "(graph: system_owners.csv)")
    assert fact.source_type == "graph" and fact.metadata["template"] == "owners"


async def test_team_template_and_owners_of_a_team(graph_tool):
    for template in ("team", "owners"):
        content, _ = await call(graph_tool[0], template=template, entity="Data-Platform")
        assert "Anthony Gonzalez MANAGES Data-Platform team; Data-Platform has 7 members and OWNS Data " \
               "Warehouse, Ledger DB" in content


async def test_document_ids_resolve_to_their_systems(graph_tool):
    content, _ = await call(graph_tool[0], template="owners", entity="SOP-OPS-014")
    assert "OWNS Ledger DB" in content
    content, _ = await call(graph_tool[0], template="owners", entity="SOP-OPS-999")
    assert content == "error: document 'SOP-OPS-999' is not linked to any system"


async def test_entity_mismatches_are_errors(graph_tool):
    assert (await call(graph_tool[0], template="owners", entity="Nonexistent"))[0] == \
        "error: no graph entity matches 'Nonexistent'"
    assert (await call(graph_tool[0], template="dependencies", entity="Anthony Gonzalez"))[0] == \
        "error: 'Anthony Gonzalez' is a Person; template 'dependencies' needs a System"


# ---------------------------------------------------------------- search_knowledge_base

def chunk(i: int) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=f"DOC-{i}::000", doc_id=f"DOC-{i}", text=f"text {i}", score=1.0,
                          source_type="document", metadata={"title": f"Doc {i}"})


class FakeHybrid:
    def __init__(self, n):
        self.n, self.calls = n, []

    async def search(self, plan, filters, k):
        self.calls.append((plan, filters, k))
        return HybridResult(chunks=[chunk(i) for i in range(self.n)])


async def test_kb_search_returns_at_most_six_chunks():
    hybrid = FakeHybrid(10)
    content, artifact = await call(make_kb_search(hybrid), query="Ledger DB failover", doc_type="sop")
    assert len(artifact) <= 6 and content.startswith('[1] (DOC-0 "Doc 0")')
    plan, filters, k = hybrid.calls[0]
    assert plan.rewritten_query == "Ledger DB failover" and plan.filters == {}  # no router filters to relax
    assert filters == {"doc_type": "sop"} and k == 6


async def test_kb_search_without_hits():
    assert await call(make_kb_search(FakeHybrid(0)), query="nothing") == ("No passages found.", [])


# ---------------------------------------------------------------- web_search

class FakeTavily:
    def __init__(self, fail=0):
        self.fail, self.calls = fail, []

    async def search(self, query, **kw):
        self.calls.append((query, kw))
        if len(self.calls) <= self.fail:
            raise ConnectionError("tavily down")
        return {"results": [{"title": f"CVE {i}", "url": f"https://example.org/{i}", "content": f"c{i}",
                             "score": 0.9} for i in range(7)]}


async def test_web_search_labels_results_as_external():
    tavily = FakeTavily()
    content, artifact = await call(make_web_search("key", client=tavily), query="latest OpenSSL CVE")
    assert len(artifact) == 5 and tavily.calls[0][1]["max_results"] == 5
    assert content.startswith('[WEB1] (external web result: "CVE 0", https://example.org/0)\nc0')
    assert all(a.source_type == "web" and a.metadata["url"] for a in artifact)


async def test_web_search_retries_then_reports_an_error():
    tavily = FakeTavily(fail=1)
    _, artifact = await call(make_web_search("key", client=tavily), query="q")
    assert len(tavily.calls) == 2 and len(artifact) == 5
    content, artifact = await call(make_web_search("key", client=FakeTavily(fail=9)), query="q")
    assert content == "error: ConnectionError: tavily down" and artifact == []


async def test_web_search_without_a_key():
    assert (await call(make_web_search(None), query="q"))[0] == \
        "error: web search is not configured (TAVILY_API_KEY missing)"


async def test_sql_ids_resolve_to_graph_nodes(graph_tool):
    tool, neo = graph_tool
    neo.by_key = {"SYS-002": ("Ledger DB", "System")}
    content, _ = await call(tool, template="owners", entity="SYS-002")
    assert "Data-Platform team OWNS SYS-002" not in content and "OWNS Ledger DB" in content
    assert (await call(tool, template="owners", entity="SYS-999"))[0] == \
        "error: no graph entity has the id 'SYS-999'"
