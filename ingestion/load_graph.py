"""Load the knowledge graph into Neo4j Aura (§B7.1, §B7.2) from committed CSVs only (no LLM calls).

1. constraints + the `entity_names` full-text index;
2. the deterministic backbone from data/raw/tables/*.csv and incident front-matter (authoritative);
3. extracted edges from data/processed/graph/*.csv (written by `ingestion.extract_graph`);
4. MENTIONS and RELATED_TO edges, and `entities[]` written back into the Qdrant chunk payloads.

Every write is `UNWIND $rows AS r MERGE ...` in batches of 500, so re-running creates no duplicates.
Usage: python -m ingestion.load_graph [--reset] [--skip-qdrant]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from neo4j import Driver, GraphDatabase

from app.config import get_settings

RAW_TABLES = Path("data/raw/tables")
GRAPH_DIR = Path("data/processed/graph")
DOCS_JSONL = Path("data/processed/docs.jsonl")
BATCH = 500

# label -> key property. Names key the extracted endpoints, so they are unique too.
KEYS = {"Person": "emp_id", "Team": "team_id", "System": "system_id", "Product": "product_id",
        "Component": "name", "Document": "doc_id", "Policy": "doc_id", "Incident": "incident_id"}
NAME_KEYS = {"Person": "name", "Team": "name", "System": "name", "Product": "name", "Component": "name",
             "Document": "doc_id", "Policy": "doc_id", "Incident": "incident_id"}
MENTION_LABELS = ("System", "Person")  # §B7.1: Document -MENTIONS-> System | Person

SCHEMA = [
    *(f"CREATE CONSTRAINT {label.lower()}_{key} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
      for label, key in KEYS.items()),
    *(f"CREATE CONSTRAINT {label.lower()}_name IF NOT EXISTS FOR (n:{label}) REQUIRE n.name IS UNIQUE"
      for label in ("Team", "System", "Product")),
    "CREATE FULLTEXT INDEX entity_names IF NOT EXISTS "
    "FOR (n:Person|Team|System|Component|Product) ON EACH [n.name]",
]


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write(driver: Driver, cypher: str, rows: list[dict]) -> int:
    for i in range(0, len(rows), BATCH):
        driver.execute_query(cypher, rows=rows[i:i + BATCH])
    return len(rows)


# ---------------------------------------------------------------- 1-2: schema + backbone
def load_backbone(driver: Driver, tables: Path = RAW_TABLES, docs_jsonl: Path = DOCS_JSONL) -> dict:
    for stmt in SCHEMA:
        driver.execute_query(stmt)
    n = {}
    n["Team"] = write(driver, """UNWIND $rows AS r MERGE (t:Team {team_id: r.team_id})
        SET t.name = r.name, t.domain = r.domain""", read_csv(tables / "teams.csv"))
    employees = read_csv(tables / "employees.csv")  # no email/phone in the graph (§B7.1)
    n["Person"] = write(driver, """UNWIND $rows AS r MERGE (p:Person {emp_id: r.emp_id})
        SET p.name = r.name, p.role = r.role
        WITH p, r MATCH (t:Team {team_id: r.team_id})
        MERGE (p)-[e:MEMBER_OF]->(t) SET e.source = 'employees.csv'""",
                        [{k: r[k] for k in ("emp_id", "name", "role", "team_id")} for r in employees])
    write(driver, """UNWIND $rows AS r
        MATCH (p:Person {emp_id: r.manager_emp_id}), (t:Team {team_id: r.team_id})
        MERGE (p)-[e:MANAGES]->(t) SET e.source = 'teams.csv'""", read_csv(tables / "teams.csv"))
    systems = [{**r, "tier": int(r["tier"])} for r in read_csv(tables / "systems.csv")]
    n["System"] = write(driver, """UNWIND $rows AS r MERGE (s:System {system_id: r.system_id})
        SET s.name = r.name, s.tier = r.tier, s.status = r.status""", systems)
    write(driver, """UNWIND $rows AS r
        MATCH (t:Team {team_id: r.team_id}), (s:System {system_id: r.system_id})
        MERGE (t)-[e:OWNS]->(s) SET e.source = 'system_owners.csv'""", read_csv(tables / "system_owners.csv"))
    write(driver, """UNWIND $rows AS r
        MATCH (a:System {system_id: r.system_id}), (b:System {system_id: r.depends_on_system_id})
        MERGE (a)-[e:DEPENDS_ON]->(b) SET e.source = 'system_dependencies.csv'""",
          read_csv(tables / "system_dependencies.csv"))
    n["Product"] = write(driver, """UNWIND $rows AS r MERGE (p:Product {product_id: r.product_id})
        SET p.name = r.name, p.category = r.category
        WITH p, r MATCH (s:System {system_id: r.system_id})
        MERGE (p)-[e:RUNS_ON]->(s) SET e.source = 'products.csv'""", read_csv(tables / "products.csv"))

    docs = read_csv(tables / "documents_meta.csv")
    n["Document"] = write(driver, """UNWIND $rows AS r MERGE (d:Document {doc_id: r.doc_id})
        SET d.title = r.title, d.doc_type = r.doc_type, d.domain = r.domain
        WITH d, r MATCH (t:Team {team_id: r.owner_team_id})
        MERGE (d)-[e:AUTHORED_BY]->(t) SET e.source = 'documents_meta.csv'""", docs)
    write(driver, "UNWIND $rows AS r MATCH (d:Document {doc_id: r.doc_id}) SET d:Policy",
          [r for r in docs if r["doc_type"] == "policy"])
    write(driver, """UNWIND $rows AS r
        MATCH (d:Document {doc_id: r.doc_id}), (s:System {system_id: r.system_id})
        MERGE (d)-[e:GOVERNS]->(s) SET e.source = 'policy_systems.csv'""",
          read_csv(tables / "policy_systems.csv"))

    incidents = []
    with open(docs_jsonl) as f:
        for line in f:
            d = json.loads(line)
            if d["metadata"]["doc_type"] == "incident":
                a = d["attributes"]
                incidents.append({"doc_id": d["metadata"]["doc_id"], "title": d["metadata"]["title"],
                                  "severity": a["severity"], "date": a["incident_date"],
                                  "resolved_by": a.get("resolved_by")})
    n["Incident"] = write(driver, """UNWIND $rows AS r MATCH (d:Document {doc_id: r.doc_id})
        SET d:Incident, d.incident_id = r.doc_id, d.severity = r.severity, d.date = date(r.date)
        WITH d, r MATCH (p:Person {emp_id: r.resolved_by})
        MERGE (d)-[e:RESOLVED_BY]->(p) SET e.source = 'incident front-matter'""", incidents)
    write(driver, """UNWIND $rows AS r
        MATCH (i:Incident {incident_id: r.incident_id}), (s:System {system_id: r.system_id})
        MERGE (i)-[e:AFFECTS]->(s) SET e.source = 'incident_systems.csv'""",
          read_csv(tables / "incident_systems.csv"))
    return n


# ---------------------------------------------------------------- 3-4: extracted + mentions
def match_clause(var: str, label: str, param: str) -> str:
    return f"MATCH ({var}:{label} {{{NAME_KEYS[label]}: r.{param}}})"


def load_extracted(driver: Driver, graph_dir: Path = GRAPH_DIR) -> dict:
    n = {}
    n["Component"] = write(driver, """UNWIND $rows AS r MERGE (c:Component {name: r.name})
        SET c.type = r.type, c.source_doc_id = r.source_doc_id, c.chunk_id = r.chunk_id""",
                           read_csv(graph_dir / "components.csv"))

    # one edge per (src, rel, dst); provenance = first citing chunk + all citing chunks
    edges: dict[tuple, dict] = {}
    for r in read_csv(graph_dir / "extracted_relations.csv"):
        if r["status"] == "conflict":
            continue
        key = (r["src_label"], r["src"], r["rel"], r["dst_label"], r["dst"], r["status"])
        e = edges.setdefault(key, {"src": r["src"], "dst": r["dst"], "source_doc_id": r["source_doc_id"],
                                   "chunk_id": r["chunk_id"], "chunk_ids": [], "evidence": r["evidence"]})
        e["chunk_ids"].append(r["chunk_id"])
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for (sl, _, rel, dl, _, status), e in edges.items():
        groups[(sl, rel, dl, status)].append(e)
    for (sl, rel, dl, status), rows in sorted(groups.items()):
        head = f"UNWIND $rows AS r {match_clause('a', sl, 'src')} {match_clause('b', dl, 'dst')}"
        if status == "new":
            cypher = f"""{head} MERGE (a)-[e:{rel}]->(b)
                SET e.source = 'llm_extraction', e.source_doc_id = r.source_doc_id, e.chunk_id = r.chunk_id,
                    e.chunk_ids = r.chunk_ids, e.evidence = r.evidence"""
        else:  # agrees with the backbone: attach the chunks that state it, never create an edge
            cypher = f"{head} MATCH (a)-[e:{rel}]->(b) SET e.chunk_ids = r.chunk_ids"
        n[f"{rel}:{status}"] = n.get(f"{rel}:{status}", 0) + write(driver, cypher, rows)

    mentions: dict[tuple, dict] = {}
    doc_ids = {r["doc_id"] for r in read_csv(RAW_TABLES / "documents_meta.csv")}
    for r in read_csv(graph_dir / "mentions.csv"):
        if r["label"] in MENTION_LABELS and r["doc_id"] in doc_ids:
            m = mentions.setdefault((r["doc_id"], r["label"], r["name"]),
                                    {"doc_id": r["doc_id"], "name": r["name"], "chunk_id": r["chunk_id"],
                                     "chunk_ids": []})
            m["chunk_ids"].append(r["chunk_id"])
    for label in MENTION_LABELS:
        rows = [m for (_, lab, _), m in mentions.items() if lab == label]
        n[f"MENTIONS:{label}"] = write(driver, f"""UNWIND $rows AS r
            MATCH (d:Document {{doc_id: r.doc_id}}) {match_clause('x', label, 'name')}
            MERGE (d)-[e:MENTIONS]->(x)
            SET e.source_doc_id = r.doc_id, e.chunk_id = r.chunk_id, e.chunk_ids = r.chunk_ids""", rows)

    related: dict[tuple, dict] = {}
    for r in read_csv(graph_dir / "related_docs.csv"):
        e = related.setdefault((r["src_doc_id"], r["dst_doc_id"]),
                               {"src": r["src_doc_id"], "dst": r["dst_doc_id"], "chunk_id": r["chunk_id"],
                                "chunk_ids": []})
        e["chunk_ids"].append(r["chunk_id"])
    n["RELATED_TO"] = write(driver, """UNWIND $rows AS r
        MATCH (a:Document {doc_id: r.src}), (b:Document {doc_id: r.dst})
        MERGE (a)-[e:RELATED_TO]->(b)
        SET e.source = 'doc_id reference', e.source_doc_id = r.src, e.chunk_id = r.chunk_id,
            e.chunk_ids = r.chunk_ids""", list(related.values()))
    return n


def write_back_entities(qdrant_path: str, graph_dir: Path = GRAPH_DIR) -> int:
    """§B8.3 bridging: set `entities[]` on every chunk in Qdrant (and in data/indexes/chunks.jsonl)."""
    from qdrant_client import QdrantClient

    from ingestion.index import DOCUMENTS, INDEX_DIR, load_chunk_entities, point_id

    entities = load_chunk_entities(graph_dir / "mentions.csv")
    client = QdrantClient(path=qdrant_path)
    try:
        if not client.collection_exists(DOCUMENTS):
            print("  qdrant: no `documents` collection yet (run `make ingest`); skipped")
            return 0
        chunk_ids, offset = [], None
        while True:
            points, offset = client.scroll(DOCUMENTS, limit=256, offset=offset, with_payload=["chunk_id"])
            chunk_ids += [p.payload["chunk_id"] for p in points]
            if offset is None:
                break
        by_value: dict[tuple, list[str]] = defaultdict(list)
        for cid in chunk_ids:
            by_value[tuple(entities.get(cid, []))].append(point_id(cid))
        for ents, ids in by_value.items():
            client.set_payload(DOCUMENTS, payload={"entities": list(ents)}, points=ids)
    finally:
        client.close()

    jsonl = INDEX_DIR / "chunks.jsonl"
    if jsonl.exists():
        lines = []
        for line in jsonl.read_text().splitlines():
            c = json.loads(line)
            c["entities"] = entities.get(c["chunk_id"], [])
            lines.append(json.dumps(c))
        jsonl.write_text("\n".join(lines) + "\n")
    return sum(1 for cid in chunk_ids if entities.get(cid))


def counts(driver: Driver) -> tuple[int, int, dict, dict]:
    nodes = driver.execute_query("MATCH (n) RETURN count(n) AS c").records[0]["c"]
    rels = driver.execute_query("MATCH ()-[r]->() RETURN count(r) AS c").records[0]["c"]
    by_label = {r["l"]: r["c"] for r in driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY l").records}
    by_type = {r["t"]: r["c"] for r in driver.execute_query(
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY t").records}
    return nodes, rels, by_label, by_type


def open_driver() -> Driver:
    s = get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_username, s.neo4j_password.get_secret_value()
                                                   if s.neo4j_password else None))


def load_all(reset: bool = False) -> tuple[int, int]:
    """Steps 1-3 (Neo4j only). Used by `main` and by the app lifespan when the graph is empty (§B4.3)."""
    with open_driver() as driver:
        driver.verify_connectivity()
        if reset:
            driver.execute_query("MATCH (n) DETACH DELETE n")
        load_backbone(driver)
        load_extracted(driver)
        nodes, rels, _, _ = counts(driver)
    return nodes, rels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load the knowledge graph into Neo4j Aura from CSVs.")
    ap.add_argument("--reset", action="store_true", help="delete every node first")
    ap.add_argument("--skip-qdrant", action="store_true", help="don't write entities[] into Qdrant")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    with open_driver() as driver:
        driver.verify_connectivity()
        if args.reset:
            driver.execute_query("MATCH (n) DETACH DELETE n")
        backbone = load_backbone(driver)
        print("backbone: " + ", ".join(f"{k}={v}" for k, v in backbone.items()))
        extracted = load_extracted(driver)
        print("extracted: " + ", ".join(f"{k}={v}" for k, v in extracted.items()))
        nodes, rels, by_label, by_type = counts(driver)
    print(f"graph: {nodes} nodes, {rels} relationships")
    print("  labels: " + ", ".join(f"{k}={v}" for k, v in by_label.items()))
    print("  types:  " + ", ".join(f"{k}={v}" for k, v in by_type.items()))

    if not args.skip_qdrant:
        try:
            n = write_back_entities(get_settings().qdrant_path)
            print(f"qdrant: entities[] written ({n} chunks with at least one entity)")
        except RuntimeError as e:  # embedded Qdrant folder lock
            if "already accessed" not in str(e):
                raise
            print("qdrant: data/qdrant is locked by another process (stop the API) - entities[] not written")
            return 1
    print(f"done in {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
