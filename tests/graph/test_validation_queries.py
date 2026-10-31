"""§B7.3 validation queries against the live Aura graph (run `make graph` first).

Expected rows are computed from the generated CSVs, and the headline names are also pinned so a
regenerated corpus that silently changes them fails loudly.
"""
import json

from app.graph import cypher_templates as T

Q1 = 'MATCH (t:Team)-[:OWNS]->(s:System {name:"Payment Gateway"}) RETURN t.name'
Q2 = """MATCH (s:System {name:"Payment Gateway"})-[:DEPENDS_ON]->(dep:System)<-[:OWNS]-(t:Team)
OPTIONAL MATCH (m:Person)-[:MANAGES]->(t)
RETURN dep.name, t.name, m.name"""
Q3 = """MATCH (down:System {name:"Auth Service"})<-[:DEPENDS_ON*1..2]-(up:System)
RETURN DISTINCT up.name, up.tier ORDER BY up.tier"""
Q4 = """MATCH (i:Incident {severity:"SEV1"})-[:AFFECTS]->(s:System)<-[:GOVERNS]-(p:Policy)
WHERE i.date >= date("2025-01-01")
RETURN p.title, collect(DISTINCT s.name)"""


async def test_q1_owner_of_payment_gateway(graph):
    rows = await graph.read(Q1)
    assert [r["t.name"] for r in rows] == ["Payments-Core"]


async def test_q2_owners_of_payment_gateway_dependencies(graph, data):
    rows = await graph.read(Q2)
    got = {(r["dep.name"], r["t.name"], r["m.name"]) for r in rows}
    pg = data["by_name"]["Payment Gateway"]
    expected = set()
    for src, dst in data["deps"]:
        if src == pg:
            team = data["teams"][data["owner"][dst]]
            expected.add((data["systems"][dst]["name"], team["name"], data["people"][team["manager_emp_id"]]))
    assert got == expected
    assert ("Ledger DB", "Data-Platform", "Anthony Gonzalez") in got  # A4 multi-hop fixture


async def test_q3_blast_radius_of_auth_service(graph, data):
    rows = await graph.read(Q3)
    auth = data["by_name"]["Auth Service"]
    hop1 = {s for s, d in data["deps"] if d == auth}
    hop2 = {s for s, d in data["deps"] if d in hop1}
    expected = {data["systems"][s]["name"] for s in (hop1 | hop2) - {auth}}
    assert {r["up.name"] for r in rows} == expected
    assert "Payment Gateway" in expected
    tiers = [r["up.tier"] for r in rows]
    assert tiers == sorted(tiers) and all(isinstance(t, int) for t in tiers)


async def test_q4_policies_governing_sev1_systems(graph, data):
    rows = await graph.read(Q4)
    got = {r["p.title"]: set(r["collect(DISTINCT s.name)"]) for r in rows}
    with open("data/processed/docs.jsonl") as f:
        sev1 = {d["metadata"]["doc_id"] for d in map(json.loads, f)
                if d["metadata"]["doc_type"] == "incident" and d["attributes"]["severity"] == "SEV1"}
    assert sev1 == {"INC-2025-012", "INC-2025-017", "INC-2025-031"}
    sev1_systems = {r["system_id"] for r in data["incident_systems"] if r["incident_id"] in sev1}
    titles = {r["doc_id"]: r["title"] for r in data["documents_meta"]}
    expected: dict[str, set] = {}
    for r in data["policy_systems"]:
        if r["system_id"] in sev1_systems:
            expected.setdefault(titles[r["doc_id"]], set()).add(data["systems"][r["system_id"]]["name"])
    assert got == expected and got


# ---------------------------------------------------------------- templates
async def test_template_owners_with_manager(graph):
    rows = await T.owners(graph, "Ledger DB")
    assert rows == [{"system": "Ledger DB", "team": "Data-Platform", "manager": "Anthony Gonzalez",
                     "source": "system_owners.csv"}]


async def test_template_dependencies_and_blast_radius(graph, data):
    deps = await T.dependencies(graph, "Payment Gateway", depth=1)
    assert {r["system"] for r in deps} == {"Ledger DB", "Auth Service", "Fraud Detection Engine",
                                           "Kafka Cluster", "API Gateway", "Secrets Vault",
                                           "Notification Service"}
    assert all(r["hops"] == 1 and r["owner_team"] for r in deps)
    radius = await T.blast_radius(graph, "Auth Service", depth=1)
    auth = data["by_name"]["Auth Service"]
    assert {r["system"] for r in radius} == {data["systems"][s]["name"] for s, d in data["deps"] if d == auth}


async def test_template_incidents_and_policies(graph):
    incidents = await T.incidents_for(graph, "Ledger DB")
    assert {r["incident_id"] for r in incidents} >= {"INC-2025-012", "INC-2025-031"}
    assert [r["date"] for r in incidents] == sorted((r["date"] for r in incidents), reverse=True)
    policies = await T.policies_for(graph, "Ledger DB")
    assert policies and all(r["doc_id"].startswith("POL-COMP-") for r in policies)


async def test_template_neighbourhood_reaches_owner_from_sop(graph):
    # SOP-OPS-014 -MENTIONS-> Ledger DB <-OWNS- Data-Platform: the multi-hop fixture in two hops
    rows = await T.neighbourhood(graph, "SOP-OPS-014", depth=2, limit=200)
    assert any(r["name"] == "Data-Platform" and r["hops"] == 2 for r in rows)
    chain = await graph.read("""
        MATCH (:Document {doc_id: 'SOP-OPS-014'})-[:MENTIONS]->(:System {name: 'Ledger DB'})
              <-[:OWNS]-(t:Team)<-[:MANAGES]-(m:Person)
        RETURN t.name AS team, m.name AS manager""")
    assert chain == [{"team": "Data-Platform", "manager": "Anthony Gonzalez"}]
    assert len(await T.neighbourhood(graph, "SOP-OPS-014")) <= T.NEIGHBOURHOOD_LIMIT


async def test_template_link_entity_fulltext(graph):
    rows = await T.link_entity(graph, "payment gateway")
    assert rows[0]["name"] == "Payment Gateway" and rows[0]["label"] == "System"
    assert await T.link_entity(graph, 'Kafka" OR (') is not None  # Lucene syntax is escaped


async def test_bad_depth_rejected(graph):
    import pytest

    with pytest.raises(ValueError):
        await T.dependencies(graph, "Payment Gateway", depth=5)


# ---------------------------------------------------------------- integrity
async def test_no_duplicate_nodes_or_edges(graph):
    dup_edges = await graph.read("""MATCH (a)-[r]->(b) WITH a, type(r) AS t, b, count(*) AS c WHERE c > 1
                                    RETURN count(*) AS n""")
    assert dup_edges[0]["n"] == 0
    for label, key in [("System", "name"), ("Team", "name"), ("Person", "emp_id"), ("Component", "name"),
                       ("Document", "doc_id")]:
        rows = await graph.read(f"MATCH (n:{label}) WITH n.{key} AS k, count(*) AS c WHERE c > 1 "
                                "RETURN count(*) AS n")
        assert rows[0]["n"] == 0, label


async def test_extracted_edges_have_provenance(graph):
    rows = await graph.read("""MATCH ()-[r]->() WHERE r.source = 'llm_extraction' OR type(r) = 'MENTIONS'
                               RETURN count(*) AS n,
                                      sum(CASE WHEN r.source_doc_id IS NULL OR r.chunk_id IS NULL
                                          THEN 1 ELSE 0 END) AS missing""")
    assert rows[0]["n"] > 0 and rows[0]["missing"] == 0


async def test_no_contact_details_in_graph(graph):
    rows = await graph.read("MATCH (n) WHERE n.email IS NOT NULL OR n.phone IS NOT NULL RETURN count(n) AS n")
    assert rows[0]["n"] == 0
