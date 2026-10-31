"""POST /chat over the real agent graph with a fake router, retriever, models and judge; no network."""
from langchain_core.messages import AIMessage

from app.api.chat import usage_summary
from app.retrieval.context import build_citations, cited_ids, format_context
from tests.api.conftest import post
from tests.fakes import CHUNKS, FACTS, artifact_tool, build_agent, tool_call


async def test_chat_returns_cited_answer(state):
    r = await post({"message": "What is the retention period for customer logs?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Customer logs are retained for 13 months [2][1]."
    assert [c["doc_id"] for c in body["citations"]] == ["POL-COMP-001", "FAQ-030"]  # order of first use
    pol = body["citations"][0]
    assert pol["id"] == "2" and pol["page"] == 1 and pol["source_type"] == "document"
    assert pol["snippet"] == "Logs are kept 13 months."
    assert body["model_used"] == "gpt-4o-mini" and body["cache_hit"] is False
    assert body["tool_calls"] == [] and body["guardrail_flags"] == [] and body["grounding_score"] == 1.0
    assert body["usage"]["agent_steps"] == 1 and set(body["usage"]) >= {"prompt_tokens", "est_cost_usd"}
    assert body["session_id"] and isinstance(body["trace_id"], str)
    assert state.retriever.calls == [("What is the retention period for customer logs?", None, 6)]


async def test_chat_reports_tool_calls(state):
    search = AIMessage(content="", tool_calls=[tool_call("search_knowledge_base", {"query": "x"})])
    state.script.replies = [search, "Logs: 13 months [2]."]
    state.agent = build_agent(state.script, router=state.router, retriever=state.retriever, judge=state.judge,
                              tools=[artifact_tool("search_knowledge_base")])
    body = (await post({"message": "logs?"})).json()
    [call] = body["tool_calls"]
    assert (call["name"], call["args"], call["status"]) == ("search_knowledge_base", {"query": "x"}, "ok")
    assert isinstance(call["duration_ms"], int) and body["usage"]["agent_steps"] == 2


async def test_chat_keeps_session_id(state):
    assert (await post({"message": "hi", "session_id": "abc"})).json()["session_id"] == "abc"


async def test_chat_validation_and_bad_filters(state):
    assert (await post({"message": ""})).status_code == 422
    assert (await post({"message": "x" * 2001})).status_code == 422
    r = await post({"message": "q", "filters": {"owner": "TEAM-01"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "owner" in r.json()["error"]["message"] and "trace_id" in r.json()["error"]


async def test_chat_graph_citations_and_flags(state):
    state.retriever.flags = ["graph_unavailable"]
    state.script.default = "Ledger DB is owned by Data-Platform, managed by Anthony Gonzalez [G1][2]."
    body = (await post({"message": "Who owns the Ledger DB?"})).json()
    assert [c["source_type"] for c in body["citations"]] == ["graph", "document"]
    g = body["citations"][0]
    assert g["id"] == "G1" and g["doc_id"] is None and g["title"] == "graph: system_owners.csv"
    assert g["snippet"].startswith("Data-Platform team OWNS Ledger DB")
    assert body["guardrail_flags"] == ["graph_unavailable"]


async def test_chat_passes_session_history_to_router(state):
    await post({"message": "Tell me about the Ledger DB", "session_id": "s9"})
    await post({"message": "who owns it?", "session_id": "s9"})
    assert state.router.calls[1] == ("who owns it?", [("Tell me about the Ledger DB",
                                                       "Customer logs are retained for 13 months [2][1].")])
    assert state.router.calls[0][1] == []


def test_usage_summary_prices_each_model():
    u = usage_summary({"gpt-4o-mini-2024-07-18": {"input_tokens": 1000, "output_tokens": 100},
                       "gpt-4o-2024-08-06": {"input_tokens": 1000, "output_tokens": 100}}, steps=3)
    assert u["prompt_tokens"] == 2000 and u["completion_tokens"] == 200 and u["agent_steps"] == 3
    assert u["est_cost_usd"] == round(0.00021 + 0.0035, 6)
    assert u["by_model"]["gpt-4o-2024-08-06"] == {"prompt_tokens": 1000, "completion_tokens": 100,
                                                  "est_cost_usd": 0.0035}


def test_citation_helpers_ignore_out_of_range_ids():
    assert cited_ids("a [1] b [3, 1] c [2] d [G2][G1, 2] [SQL1] [WEB2]") == \
        ["1", "3", "2", "G2", "G1", "SQL1", "WEB2"]
    assert [c.id for c in build_citations("x [1] y [7] z [G1] [G4]", CHUNKS, FACTS)] == ["1", "G1"]
    assert "p.1" in format_context(CHUNKS)
    assert format_context(CHUNKS, FACTS).endswith("(graph: system_owners.csv)")


def test_sql_citation_snippet_carries_the_query():
    from app.core.types import RetrievedChunk
    from app.retrieval.context import citation

    q = "SELECT COUNT(*) FROM tickets WHERE priority='SEV1'"
    chunk = RetrievedChunk(chunk_id="sql:x", doc_id="sql", text="COUNT(*)\n17\n(1 row)", score=1.0,
                           source_type="sql", metadata={"query": q})
    assert citation(chunk, "SQL1").snippet == f"SQL: {q}\nCOUNT(*)\n17\n(1 row)"
