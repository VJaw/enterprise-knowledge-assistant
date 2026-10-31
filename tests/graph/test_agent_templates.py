"""Live Aura: the templates the agent's `query_graph` adds (team, id lookup) against the CSV backbone."""
from app.graph import cypher_templates as T


async def test_team_template(graph, data):
    [row] = await T.team(graph, "Data-Platform")
    team_id = next(t for t, r in data["teams"].items() if r["name"] == "Data-Platform")
    assert row["manager"] == data["people"][data["teams"][team_id]["manager_emp_id"]] == "Anthony Gonzalez"
    assert row["members"] == 7
    owned = {data["systems"][s]["name"] for s, t in data["owner"].items() if t == team_id}
    assert set(row["systems"]) == owned


async def test_lookup_by_key(graph):
    assert await T.by_key(graph, "SYS-002") == [{"name": "Ledger DB", "label": "System"}]
    assert (await T.by_key(graph, "TEAM-02"))[0]["label"] == "Team"
    assert await T.by_key(graph, "SYS-999") == []
