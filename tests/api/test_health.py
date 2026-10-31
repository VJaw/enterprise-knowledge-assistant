"""/health with mocked service clients; no network access."""
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from app.api import health
from app.config import Settings
from app.main import app

TAVILY_KEY = "tvly-test-secret-123"


class FakeModels:
    def __init__(self, ids):
        self.ids = ids

    async def list(self):
        for i in self.ids:
            yield SimpleNamespace(id=i)


class FakeNeo4j:
    def __init__(self, fail=False):
        self.fail = fail

    async def verify_connectivity(self):
        if self.fail:
            raise ConnectionError("unreachable")


class FakeLangfuse:
    def __init__(self, ok=True):
        self.ok = ok

    def auth_check(self):
        return self.ok


class FakeQdrant:
    def __init__(self, n=278):
        self.n = n

    def count(self, collection, exact=True):
        return SimpleNamespace(count=self.n)


def tavily_transport(status):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TAVILY_KEY}"
        return httpx.Response(status, json={"key": {}, "account": {}})
    return httpx.MockTransport(handler)


async def models_ok(state):
    return None


@pytest.fixture
def state(tmp_path, monkeypatch):
    db = tmp_path / "acme.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE tickets (id INTEGER)")
    s = Settings(_env_file=None, tavily_api_key=TAVILY_KEY, sqlite_path=str(db))
    app.state.settings = s
    app.state.openai = SimpleNamespace(models=FakeModels([s.model_strong, s.model_fast, "other"]))
    app.state.neo4j = FakeNeo4j()
    app.state.graph_available = True
    app.state.langfuse = FakeLangfuse()
    app.state.http = httpx.AsyncClient(transport=tavily_transport(200))
    app.state.qdrant = FakeQdrant()
    app.state.mcp = SimpleNamespace(tools=["sqlite_read_query"])
    monkeypatch.setitem(health.CRITICAL, "models", models_ok)  # the real check needs the loaded weights
    return app.state


async def get_health():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get("/health")


async def test_all_ok(state):
    r = await get_health()
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "openai": "ok", "qdrant": "ok", "sqlite": "ok", "models": "ok",
                        "graph": "ok", "langfuse": "ok", "tavily": "ok", "mcp": "ok"}


async def test_graph_down_is_degraded_not_down(state):
    state.neo4j = FakeNeo4j(fail=True)
    r = await get_health()
    assert r.status_code == 200 and r.json()["status"] == "degraded" and r.json()["graph"] == "degraded"
    state.neo4j = FakeNeo4j()
    state.graph_available = False  # the startup check / watchdog marked Aura unavailable
    assert (await get_health()).json()["graph"] == "degraded"


async def test_graph_disabled(state):
    state.settings.use_graph = False
    state.neo4j = FakeNeo4j(fail=True)
    body = (await get_health()).json()
    assert body["status"] == "ok" and body["graph"] == "disabled"


async def test_openai_missing_model_is_down(state):
    state.openai = SimpleNamespace(models=FakeModels(["gpt-4o-mini"]))
    r = await get_health()
    assert r.status_code == 503 and r.json()["status"] == "down"
    assert r.json()["openai"] == "error: RuntimeError"


async def test_empty_index_or_missing_sqlite_is_down(state, tmp_path):
    state.qdrant = FakeQdrant(0)
    assert (await get_health()).status_code == 503
    state.qdrant = FakeQdrant()
    state.settings.sqlite_path = str(tmp_path / "missing.db")
    r = await get_health()
    assert r.status_code == 503 and r.json()["sqlite"] == "error: OperationalError"


async def test_real_models_check_reports_what_is_missing(state, monkeypatch):
    monkeypatch.setitem(health.CRITICAL, "models", health.check_models)
    state.settings.use_rerank = False
    state.guardrails = None
    body = (await get_health()).json()
    assert body["models"] in ("ok", "error: RuntimeError")  # depends on whether another test loaded bge-small
    if body["models"] != "ok":
        assert body["status"] == "down"


async def test_langfuse_and_mcp_failures_degrade(state):
    state.langfuse = FakeLangfuse(ok=False)
    state.mcp = SimpleNamespace(tools=[])
    r = await get_health()
    assert r.status_code == 200 and r.json()["status"] == "degraded"
    assert r.json()["langfuse"] == "error: RuntimeError" and r.json()["mcp"] == "error: RuntimeError"


async def test_tavily_bad_key_does_not_leak_secret(state):
    state.http = httpx.AsyncClient(transport=tavily_transport(401))
    r = await get_health()
    assert r.json()["tavily"] == "error: HTTP 401" and r.json()["status"] == "degraded"
    assert TAVILY_KEY not in r.text
