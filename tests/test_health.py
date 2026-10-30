"""/health with mocked service clients; no network access."""
from types import SimpleNamespace

import httpx
import pytest

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


def tavily_transport(status):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TAVILY_KEY}"
        return httpx.Response(status, json={"key": {}, "account": {}})
    return httpx.MockTransport(handler)


@pytest.fixture
def state():
    s = Settings(_env_file=None, tavily_api_key=TAVILY_KEY)
    app.state.settings = s
    app.state.openai = SimpleNamespace(models=FakeModels([s.model_strong, s.model_fast, "other"]))
    app.state.neo4j = FakeNeo4j()
    app.state.langfuse = FakeLangfuse()
    app.state.http = httpx.AsyncClient(transport=tavily_transport(200))
    return app.state


async def get_health():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get("/health")


async def test_all_ok(state):
    r = await get_health()
    assert r.status_code == 200
    assert r.json() == {"openai": "ok", "neo4j": "ok", "langfuse": "ok", "tavily": "ok"}


async def test_neo4j_down(state):
    state.neo4j = FakeNeo4j(fail=True)
    r = await get_health()
    assert r.status_code == 503
    assert r.json()["neo4j"] == "error: ConnectionError"
    assert r.json()["openai"] == "ok"


async def test_openai_missing_model(state):
    state.openai = SimpleNamespace(models=FakeModels(["gpt-4o-mini"]))
    r = await get_health()
    assert r.status_code == 503
    assert r.json()["openai"] == "error: RuntimeError"


async def test_langfuse_auth_fails(state):
    state.langfuse = FakeLangfuse(ok=False)
    assert (await get_health()).json()["langfuse"] == "error: RuntimeError"


async def test_tavily_bad_key_does_not_leak_secret(state):
    state.http = httpx.AsyncClient(transport=tavily_transport(401))
    r = await get_health()
    assert r.json()["tavily"] == "error: HTTP 401"
    assert TAVILY_KEY not in r.text
