"""Live Neo4j Aura fixtures, plus expected answers derived from the generated CSVs (the backbone source)."""
import csv
from pathlib import Path

import pytest

from app.config import get_settings
from app.graph.neo4j_client import Neo4jClient

TABLES = Path("data/raw/tables")


def rows(name: str) -> list[dict]:
    with open(TABLES / name, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
async def graph():
    s = get_settings()
    if not s.neo4j_uri:
        pytest.skip("NEO4J_URI not configured")
    client = Neo4jClient(s.neo4j_uri, s.neo4j_username,
                         s.neo4j_password.get_secret_value() if s.neo4j_password else None)
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture(scope="session")
def data():
    systems = {r["system_id"]: r for r in rows("systems.csv")}
    teams = {r["team_id"]: r for r in rows("teams.csv")}
    people = {r["emp_id"]: r["name"] for r in rows("employees.csv")}
    by_name = {r["name"]: sid for sid, r in systems.items()}
    deps = [(r["system_id"], r["depends_on_system_id"]) for r in rows("system_dependencies.csv")]
    owner = {r["system_id"]: r["team_id"] for r in rows("system_owners.csv")}
    return {"systems": systems, "teams": teams, "people": people, "by_name": by_name, "deps": deps,
            "owner": owner, "incident_systems": rows("incident_systems.csv"),
            "policy_systems": rows("policy_systems.csv"), "documents_meta": rows("documents_meta.csv")}
