"""Shared setup for the API tests: `app.state` is process-wide, so every test starts from a clean slate (no
cache, no guardrails, fresh rate-limit counters and metrics). OpenAI and Tavily are never reached: the agent
runs on the fakes in tests/fakes.py."""
import httpx
import pytest
from qdrant_client import QdrantClient

from app.api.chat import limiter
from app.cache.semantic_cache import SemanticCache
from app.config import Settings
from app.main import app
from app.observability import metrics
from tests.fakes import FakeJudge, FakeRetriever, FakeRouter, Script, build_agent

ANSWER = "Customer logs are retained for 13 months [2][1]."


@pytest.fixture(autouse=True)
def clean_state():
    for name in ("cache", "guardrails", "agent", "qdrant", "retriever"):
        if hasattr(app.state, name):
            delattr(app.state, name)
    limiter.reset()
    metrics.reset()
    yield
    limiter.reset()


@pytest.fixture
def state():
    s = app.state
    s.settings = Settings(_env_file=None)
    s.router, s.retriever, s.script = FakeRouter(), FakeRetriever(), Script(default=ANSWER)
    s.judge = FakeJudge([("Customer logs are retained for 13 months", True)])
    s.agent = build_agent(s.script, router=s.router, retriever=s.retriever, judge=s.judge)
    return s


@pytest.fixture
def cache(state, tmp_path):
    """A real SemanticCache on in-memory Qdrant with the real bge-small embedder."""
    state.qdrant = QdrantClient(":memory:")
    state.cache = SemanticCache(state.qdrant, version_path=tmp_path / "corpus_version")
    return state.cache


def client(**kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, **kw), base_url="http://t")


async def post(body, **kw):
    async with client() as c:
        return await c.post("/chat", json=body, **kw)
