"""Parameterised Cypher templates for graph retrieval (§B7.3, §B8.3).

Values are always query parameters. The only interpolated piece is the traversal depth of a
variable-length pattern (Cypher can't parameterise it), which is checked against `DEPTHS` first.
Each row carries `source` provenance so facts can be cited as `[Gn] ... (graph: <source>)`.
"""
from __future__ import annotations

from app.graph.neo4j_client import Neo4jClient

DEPTHS = (1, 2)
NEIGHBOURHOOD_LIMIT = 25

OWNERS = """
MATCH (s:System {name: $name})<-[o:OWNS]-(t:Team)
OPTIONAL MATCH (m:Person)-[:MANAGES]->(t)
RETURN s.name AS system, t.name AS team, m.name AS manager, o.source AS source
"""

DEPENDENCIES = """
MATCH p = (s:System {name: $name})-[:DEPENDS_ON*1..%d]->(d:System)
WITH d, min(length(p)) AS hops
OPTIONAL MATCH (t:Team)-[:OWNS]->(d)
OPTIONAL MATCH (m:Person)-[:MANAGES]->(t)
RETURN d.name AS system, d.tier AS tier, hops, t.name AS owner_team, m.name AS manager,
       'system_dependencies.csv' AS source
ORDER BY hops, d.tier, d.name
"""

# Document seed -> the systems it mentions (bridges "the system in SOP-OPS-014" to the System templates)
DOC_SYSTEMS = """
MATCH (d:Document {doc_id: $doc_id})-[:MENTIONS]->(s:System)
RETURN s.name AS system
ORDER BY s.name
"""

# Team seed -> manager, head count and owned systems (the agent's `query_graph(team, ...)`, D-021)
TEAM = """
MATCH (t:Team {name: $name})
OPTIONAL MATCH (m:Person)-[:MANAGES]->(t)
OPTIONAL MATCH (p:Person)-[:MEMBER_OF]->(t)
OPTIONAL MATCH (t)-[:OWNS]->(s:System)
RETURN t.name AS team, m.name AS manager, count(DISTINCT p) AS members, collect(DISTINCT s.name) AS systems,
       'teams.csv, employees.csv, system_owners.csv' AS source
"""

BLAST_RADIUS = """
MATCH p = (down:System {name: $name})<-[:DEPENDS_ON*1..%d]-(up:System)
WITH up, min(length(p)) AS hops
RETURN up.name AS system, up.tier AS tier, hops, 'system_dependencies.csv' AS source
ORDER BY up.tier, hops, up.name
"""

INCIDENTS_FOR = """
MATCH (i:Incident)-[a:AFFECTS]->(s:System {name: $name})
RETURN i.incident_id AS incident_id, i.title AS title, i.severity AS severity, toString(i.date) AS date,
       a.source AS source
ORDER BY i.date DESC
"""

POLICIES_FOR = """
MATCH (p:Policy)-[g:GOVERNS]->(s:System {name: $name})
RETURN p.doc_id AS doc_id, p.title AS title, g.source AS source
ORDER BY p.doc_id
"""

NEIGHBOURHOOD = """
MATCH (seed) WHERE seed.name = $name OR seed.doc_id = $name
MATCH p = (seed)-[*1..%d]-(n)
WHERE n <> seed
WITH n, min(length(p)) AS hops, head(collect(p)) AS path
RETURN coalesce(n.name, n.doc_id) AS name, labels(n)[0] AS label, hops,
       [r IN relationships(path) | type(r)] AS rels,
       [r IN relationships(path) | coalesce(startNode(r).name, startNode(r).doc_id)] AS rel_from,
       [x IN nodes(path) | coalesce(x.name, x.doc_id)] AS via
ORDER BY hops, label, name
LIMIT $limit
"""

# A key from the SQL tables (SYS-002, TEAM-01, EMP-12345, PRD-...) -> the node's name and label
BY_KEY = """
MATCH (n) WHERE n.system_id = $id OR n.team_id = $id OR n.emp_id = $id OR n.product_id = $id
RETURN n.name AS name, labels(n)[0] AS label
LIMIT 1
"""

# §B8.1 entity linking via the `entity_names` full-text index
LINK_ENTITY = """
CALL db.index.fulltext.queryNodes('entity_names', $query) YIELD node, score
RETURN node.name AS name, labels(node)[0] AS label, score
ORDER BY score DESC
LIMIT $limit
"""


def _depth(depth: int) -> int:
    if depth not in DEPTHS:
        raise ValueError(f"depth must be one of {DEPTHS}")
    return depth


async def owners(client: Neo4jClient, name: str) -> list[dict]:
    return await client.read(OWNERS, name=name)


async def dependencies(client: Neo4jClient, name: str, depth: int = 1) -> list[dict]:
    return await client.read(DEPENDENCIES % _depth(depth), name=name)


async def blast_radius(client: Neo4jClient, name: str, depth: int = 2) -> list[dict]:
    return await client.read(BLAST_RADIUS % _depth(depth), name=name)


async def incidents_for(client: Neo4jClient, name: str) -> list[dict]:
    return await client.read(INCIDENTS_FOR, name=name)


async def policies_for(client: Neo4jClient, name: str) -> list[dict]:
    return await client.read(POLICIES_FOR, name=name)


async def neighbourhood(client: Neo4jClient, name: str, depth: int = 2,
                        limit: int = NEIGHBOURHOOD_LIMIT) -> list[dict]:
    return await client.read(NEIGHBOURHOOD % _depth(depth), name=name, limit=limit)


async def team(client: Neo4jClient, name: str) -> list[dict]:
    return await client.read(TEAM, name=name)


async def by_key(client: Neo4jClient, key: str) -> list[dict]:
    return await client.read(BY_KEY, id=key)


async def doc_systems(client: Neo4jClient, doc_id: str) -> list[str]:
    return [r["system"] for r in await client.read(DOC_SYSTEMS, doc_id=doc_id)]


async def link_entity(client: Neo4jClient, text: str, limit: int = 5) -> list[dict]:
    """Lucene query over entity names; special characters are escaped so user text can't break it."""
    escaped = "".join(f"\\{ch}" if ch in '+-&|!(){}[]^"~*?:\\/' else ch for ch in text)
    return await client.read(LINK_ENTITY, query=escaped, limit=limit)
