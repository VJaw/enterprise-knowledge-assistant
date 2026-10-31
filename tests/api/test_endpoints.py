"""Sessions, /ingest, the §B11.3 error model, the rate limit and /metrics."""
import asyncio

from app.api import admin, chat
from app.cache.semantic_cache import bump_corpus_version
from app.main import app
from tests.api.conftest import ANSWER, client, post
from tests.api.test_cache import Q, entry

# ---------------------------------------------------------------------------------------------- sessions


async def test_session_history_and_delete(state):
    await post({"message": Q, "session_id": "s1"})
    await post({"message": "and who owns it?", "session_id": "s1"})
    async with client() as c:
        body = (await c.get("/sessions/s1")).json()
        assert body["session_id"] == "s1" and body["turns"] == 2
        assert [m["role"] for m in body["messages"]] == ["user", "assistant"] * 2
        assert body["messages"][0]["content"] == Q and body["messages"][1]["content"] == ANSWER

        assert (await c.delete("/sessions/s1")).json() == {"session_id": "s1", "deleted": True}
        r = await c.get("/sessions/s1")
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
        assert (await c.delete("/sessions/s1")).json()["deleted"] is False  # idempotent


# ---------------------------------------------------------------------------------------------- /ingest


async def test_ingest_requires_the_admin_token(state):
    async with client() as c:
        for headers in ({}, {"X-Admin-Token": "wrong"}):
            r = await c.post("/ingest", headers=headers)
            assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_ingest_reindexes_and_invalidates_the_cache(state, cache, monkeypatch, tmp_path):
    def fake_reindex(client):
        assert client is state.qdrant
        version = bump_corpus_version(tmp_path / "corpus_version")
        return {"documents": 94, "chunks": 278, "corpus_version": version}

    monkeypatch.setattr(admin, "reindex", fake_reindex)
    await cache.write(Q, None, entry())
    async with client() as c:
        r = await c.post("/ingest", headers={"X-Admin-Token": "change-me"})
    body = r.json()
    assert r.status_code == 200
    assert (body["documents"], body["chunks"], body["corpus_version"]) == (94, 278, 2)
    assert body["cache_entries_dropped"] == 1 and cache.count() == 0
    assert (await post({"message": Q})).json()["cache_hit"] is False


# ---------------------------------------------------------------------------------------------- errors


async def test_validation_error_shape(state):
    r = await post({"message": ""})
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "VALIDATION_ERROR" and "message" in err["message"]
    async with client() as c:
        r = await c.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


async def test_rate_limit_per_session_then_per_ip(state):
    state.settings.rate_limit = "2/minute"
    for _ in range(2):
        assert (await post({"message": "hi", "session_id": "busy"})).status_code == 200
    r = await post({"message": "hi", "session_id": "busy"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
    assert int(r.headers["Retry-After"]) >= 1
    assert (await post({"message": "hi", "session_id": "other"})).status_code == 200
    # Without a session id, requests count against the client IP.
    assert [(await post({"message": "hi"})).status_code for _ in range(3)] == [200, 200, 429]


class BrokenAgent:
    def __init__(self, exc=None, delay=0.0):
        self.exc, self.delay = exc, delay

    async def ainvoke(self, *a, **kw):
        await asyncio.sleep(self.delay)
        raise self.exc


async def test_internal_error_and_upstream_timeout(state, monkeypatch):
    state.agent = BrokenAgent(RuntimeError("boom"))
    r = await post({"message": Q})
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "boom" not in r.text and "trace_id" in r.json()["error"]

    monkeypatch.setattr(chat, "REQUEST_BUDGET_S", 0.05)
    state.agent = BrokenAgent(RuntimeError("never reached"), delay=1.0)
    r = await post({"message": Q})
    assert r.status_code == 504 and r.json()["error"]["code"] == "UPSTREAM_TIMEOUT"


async def test_unhandled_error_outside_chat_is_structured(state, monkeypatch):
    async def explode(*a, **kw):
        raise KeyError("x")

    monkeypatch.setattr(state.agent, "aget_state", explode)
    async with client() as c:
        r = await c.get("/sessions/any")
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------------------------- /metrics


async def test_metrics_count_requests_cache_and_flags(state, cache):
    state.retriever.flags = ["graph_unavailable"]
    await post({"message": Q})
    await post({"message": Q})
    await post({"message": ""})
    async with client() as c:
        text = (await c.get("/metrics")).text
    assert 'eka_chat_requests_total{cache="miss",model="gpt-4o-mini"} 1' in text
    assert 'eka_chat_requests_total{cache="hit",model="cache"} 1' in text
    assert 'eka_guardrail_triggers_total{flag="graph_unavailable"} 1' in text
    assert 'eka_http_requests_total{method="POST",route="/chat",status="200"} 2' in text
    assert 'eka_errors_total{code="VALIDATION_ERROR"} 1' in text
    assert 'eka_chat_latency_seconds_bucket{cache="hit",le="+Inf"} 1' in text


def test_openapi_lists_every_endpoint():
    spec = app.openapi()
    assert {"/chat", "/sessions/{session_id}", "/ingest", "/health", "/metrics"} <= set(spec["paths"])
    assert set(spec["paths"]["/sessions/{session_id}"]) == {"get", "delete"}
    chat_responses = spec["paths"]["/chat"]["post"]["responses"]
    assert {"200", "422", "429", "500", "504"} <= set(chat_responses)
    assert any(p["name"] == "x-admin-token" for p in spec["paths"]["/ingest"]["post"]["parameters"])
    assert "ErrorResponse" in spec["components"]["schemas"]
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    ref = spec["paths"]["/sessions/{session_id}"]["get"]["responses"]["422"]["content"]["application/json"]
    assert ref["schema"]["$ref"].endswith("/ErrorResponse")
