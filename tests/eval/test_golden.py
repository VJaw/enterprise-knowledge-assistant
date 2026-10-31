"""Golden set checks (Phase 6): shape, category counts, and every reference context against its source.

A reference context is one of:
- a verbatim excerpt of a document in data/processed/docs.jsonl (compared after stripping Markdown emphasis
  and collapsing whitespace, since PDF text keeps soft line wraps);
- a graph fact `A REL B (graph: <csv>)`, checked against the CSV backbone in data/raw/tables;
- `SQL: <select> -> <result>`, re-run on the read-only SQLite copy (rows joined with "; ", columns
  with " | ").
"""
import csv
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

GOLDEN = Path("eval/datasets/golden.jsonl")
DOCS = Path("data/processed/docs.jsonl")
TABLES = Path("data/raw/tables")
RO_DB = Path("data/acme_ro.db")

EXPECTED_COUNTS = {"factual": 10, "multi_hop": 8, "structured": 5, "faq": 4, "unanswerable": 3}
DOMAINS = {"engineering", "operations", "compliance", "support"}
FACT = re.compile(r"^(.+?) (OWNS|DEPENDS_ON|MANAGES|GOVERNS|AFFECTS) (.+?) \(graph: (\S+\.csv)\)$")


def norm(s: str) -> str:
    return " ".join(s.replace("*", "").replace("`", "").split())


def table(name: str) -> list[dict]:
    with open(TABLES / name, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


@pytest.fixture(scope="module")
def corpus() -> str:
    return "\n".join(norm(json.loads(line)["text"]) for line in DOCS.read_text().splitlines())


@pytest.fixture(scope="module")
def graph_facts() -> set[tuple[str, str, str, str]]:
    systems = {r["system_id"]: r["name"] for r in table("systems.csv")}
    teams = {r["team_id"]: r for r in table("teams.csv")}
    people = {r["emp_id"]: r["name"] for r in table("employees.csv")}
    facts = {(teams[r["team_id"]]["name"], "OWNS", systems[r["system_id"]], "system_owners.csv")
             for r in table("system_owners.csv")}
    facts |= {(people[t["manager_emp_id"]], "MANAGES", t["name"], "teams.csv") for t in teams.values()}
    facts |= {(systems[r["system_id"]], "DEPENDS_ON", systems[r["depends_on_system_id"]],
               "system_dependencies.csv") for r in table("system_dependencies.csv")}
    facts |= {(r["doc_id"], "GOVERNS", systems[r["system_id"]], "policy_systems.csv")
              for r in table("policy_systems.csv")}
    facts |= {(r["incident_id"], "AFFECTS", systems[r["system_id"]], "incident_systems.csv")
              for r in table("incident_systems.csv")}
    return facts


def test_shape_and_counts(rows):
    assert len(rows) == 30
    assert Counter(r["query_type"] for r in rows) == EXPECTED_COUNTS
    for r in rows:
        assert set(r) == {"user_input", "reference", "reference_contexts", "query_type", "domain"}
        assert r["domain"] in DOMAINS
        assert r["user_input"].strip() and r["reference"].strip()
        assert bool(r["reference_contexts"]) == (r["query_type"] != "unanswerable")
    assert len({r["user_input"] for r in rows}) == 30
    assert {r["domain"] for r in rows} == DOMAINS


def test_document_contexts_are_verbatim(rows, corpus):
    missing = [(r["user_input"], c) for r in rows for c in r["reference_contexts"]
               if not c.startswith("SQL: ") and not FACT.match(c) and norm(c) not in corpus]
    assert not missing


def test_graph_contexts_match_csvs(rows, graph_facts):
    facts = [FACT.match(c).groups() for r in rows for c in r["reference_contexts"] if FACT.match(c)]
    assert facts
    assert [f for f in facts if f not in graph_facts] == []


def test_multi_hop_rows_need_the_graph(rows):
    """Each multi-hop row has at least one graph fact, and ownership/manager names never occur in the docs
    (D-009), so the answer can't come from text alone."""
    for r in rows:
        if r["query_type"] == "multi_hop":
            assert any(FACT.match(c) for c in r["reference_contexts"]), r["user_input"]


def test_multi_hop_fixtures_included(rows):
    fixtures = json.loads(Path("data/fixtures.json").read_text())["multi_hop_facts"]
    multi = [r for r in rows if r["query_type"] == "multi_hop"]
    for f in fixtures:
        row = next((r for r in multi if f["doc_id"] in r["user_input"]), None)
        assert row is not None, f["doc_id"]
        for name in (f["system"], f["owner_team"], f["manager"]):
            assert name in row["reference"]


def test_sql_contexts_match_database(rows):
    if not RO_DB.exists():
        pytest.skip("data/acme_ro.db not built (make ingest)")
    con = sqlite3.connect(f"file:{RO_DB}?mode=ro", uri=True)
    try:
        for r in rows:
            for c in r["reference_contexts"]:
                if not c.startswith("SQL: "):
                    continue
                sql, expected = c.removeprefix("SQL: ").rsplit(" -> ", 1)
                got = "; ".join(" | ".join(str(v) for v in row) for row in con.execute(sql).fetchall())
                assert got == expected, sql
                assert expected.split(" | ")[-1] in r["reference"]
    finally:
        con.close()
