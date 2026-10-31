"""POST /chat skeleton with a fake retriever and LLM; no network."""
import httpx
import pytest
from langchain_core.messages import AIMessage

from app.api.chat import build_citations, cited_ids, format_context
from app.config import Settings
from app.core.types import RetrievedChunk
from app.main import app

CHUNKS = [
    RetrievedChunk(chunk_id="FAQ-030::000", doc_id="FAQ-030", text="Q: Logs?\nA: 13 months.", score=0.9,
                   source_type="document",
                   metadata={"title": "Log retention FAQ", "doc_type": "faq", "domain": "support",
                             "section": "Data", "page": None, "version": "1.0"}),
    RetrievedChunk(chunk_id="POL-COMP-001::000", doc_id="POL-COMP-001", text="Logs are kept 13 months.",
                   score=0.8, source_type="document",
                   metadata={"title": "Data Retention Policy", "doc_type": "policy", "domain": "compliance",
                             "section": "3. Policy Statements > 3.1 Data Retention Periods", "page": 1,
                             "version": "3.0"}),
]


class FakeRetriever:
    def __init__(self):
        self.calls = []

    async def retrieve(self, query, filters, k):
        self.calls.append((query, filters, k))
        if filters and "owner" in filters:
            raise ValueError("unsupported filter keys: ['owner']")
        return CHUNKS


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer
        self.messages = None

    async def ainvoke(self, messages, config=None):
        self.messages = messages
        return AIMessage(content=self.answer,
                         usage_metadata={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100})


@pytest.fixture
def state():
    app.state.settings = Settings(_env_file=None)
    app.state.retriever = FakeRetriever()
    app.state.llm = FakeLLM("Customer logs are retained for 13 months [2][1].")
    return app.state


async def post(body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.post("/chat", json=body)


async def test_chat_returns_cited_answer(state):
    r = await post({"message": "What is the retention period for customer logs?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("Customer logs are retained for 13 months")
    assert [c["doc_id"] for c in body["citations"]] == ["POL-COMP-001", "FAQ-030"]  # order of first use
    pol = body["citations"][0]
    assert pol["id"] == "2" and pol["page"] == 1 and pol["source_type"] == "document"
    assert pol["snippet"] == "Logs are kept 13 months."
    assert body["model_used"] == "gpt-4o-mini" and body["cache_hit"] is False
    assert body["tool_calls"] == [] and body["guardrail_flags"] == [] and body["grounding_score"] is None
    assert body["usage"] == {"prompt_tokens": 1000, "completion_tokens": 100, "est_cost_usd": 0.00021}
    assert body["session_id"] and isinstance(body["trace_id"], str)
    assert state.retriever.calls == [("What is the retention period for customer logs?", None, 5)]


async def test_chat_prompt_contains_numbered_context(state):
    await post({"message": "logs?", "session_id": "s1"})
    system, human = state.llm.messages
    assert "[n]" not in system.content and "I don't know" in system.content
    assert human.content.startswith("Context:\n[1] (FAQ-030")
    assert '[2] (POL-COMP-001 "Data Retention Policy", § "3. Policy Statements' in human.content


async def test_chat_keeps_session_id(state):
    assert (await post({"message": "hi", "session_id": "abc"})).json()["session_id"] == "abc"


async def test_chat_validation_and_bad_filters(state):
    assert (await post({"message": ""})).status_code == 422
    assert (await post({"message": "x" * 2001})).status_code == 422
    assert (await post({"message": "q", "filters": {"owner": "TEAM-01"}})).status_code == 422


def test_citation_helpers_ignore_out_of_range_ids():
    assert cited_ids("a [1] b [3, 1] c [2]") == [1, 3, 2]
    assert [c.id for c in build_citations("x [1] y [7]", CHUNKS)] == ["1"]
    assert "p.1" in format_context(CHUNKS)
