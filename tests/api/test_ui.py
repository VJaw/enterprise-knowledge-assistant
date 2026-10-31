"""Gradio UI at /ui (D-030): it is mounted, its handler drives the real POST /chat in-process, and the details
panel renders what the API returned."""
import httpx

from app.main import app
from app.ui.gradio_app import build_ui, citation_rows, error_markdown, summary_markdown, tool_rows


def ask_fn(ui):
    return next(f.fn for f in ui.fns.values() if getattr(f.fn, "__name__", "") == "ask")


async def run_ask(message, session="ui-test", allow_web=True, doc_type="any"):
    steps = [s async for s in ask_fn(build_ui(app))(message, [], session, allow_web, doc_type)]
    return steps[0], steps[-1]


async def test_ui_is_mounted(state):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/ui/", follow_redirects=True)
    assert r.status_code == 200 and "gradio" in r.text.lower()


async def test_ask_shows_answer_and_details(state):
    working, (history, cleared, summary, cites, tools) = await run_ask(
        "What is the retention period for customer logs?")
    assert working[0][-1]["content"] == "_Thinking…_"
    assert history[-2] == {"role": "user", "content": "What is the retention period for customer logs?"}
    assert history[-1] == {"role": "assistant", "content": "Customer logs are retained for 13 months [2][1]."}
    assert cleared == ""
    assert "✅ answered" in summary and "`gpt-4o-mini`" in summary
    assert "| **Grounding score** | 1.00 |" in summary
    assert [row[0] for row in cites] == ["2", "1"] and cites[0][2].startswith("POL-COMP-001")
    assert tools == []


async def test_ask_passes_session_and_filters(state):
    await run_ask("logs?", session="ui-abc", doc_type="policy", allow_web=False)
    assert state.retriever.calls[-1][1] == {"doc_type": "policy"}


async def test_ask_shows_api_errors(state):
    _, (history, _, summary, cites, tools) = await run_ask("x" * 2001)
    assert "Error 422 `VALIDATION_ERROR`" in history[-1]["content"] and cites == [] and tools == []


def test_renderers():
    cites = citation_rows([
        {"id": "G1", "source_type": "graph", "doc_id": None, "title": "graph: system_owners.csv",
         "snippet": "Data-Platform OWNS Ledger DB"},
        {"id": "3", "source_type": "document", "doc_id": "SOP-OPS-014", "title": "Database Failover",
         "section": "3.2 Manual promotion", "page": None, "snippet": "x " * 300},
    ])
    assert cites[0][2] == "graph: system_owners.csv"
    assert cites[1][2] == "SOP-OPS-014: Database Failover" and cites[1][3] == "3.2 Manual promotion"
    assert cites[1][4].endswith("…") and len(cites[1][4]) == 241
    call = {"name": "sql_query", "status": "ok", "duration_ms": 12, "args": {"query": "SELECT 1"}}
    assert tool_rows([call]) == [[1, "sql_query", "ok", 12, '{"query": "SELECT 1"}']]
    blocked = summary_markdown({"guardrail_flags": ["prompt_injection", "GUARDRAIL_BLOCKED"],
                                "model_used": "guardrails", "grounding_score": None, "trace_id": "abc"},
                               trace_url="https://lf/trace/abc")
    assert "🛑 blocked" in blocked and "| **Grounding score** | n/a |" in blocked
    assert "[abc](https://lf/trace/abc)" in blocked
    assert "RATE_LIMITED" in error_markdown(429, {"error": {"code": "RATE_LIMITED", "message": "slow down"}})
