"""The LangGraph agent (§B9.1) with scripted models and tools: step bound, routing, citations, grounding
retry, sessions."""
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.graph import open_checkpointer, run_config, turn_input
from app.agent.nodes import DISCLAIMER
from app.agent.prompts import IDK
from app.core.types import RetrievedChunk
from app.guardrails.actions import drop_unsupported
from app.retrieval.context import citations_for
from tests.fakes import (
    CHUNKS,
    FakeJudge,
    FakeRetriever,
    FakeRouter,
    Script,
    artifact_tool,
    build_agent,
    plan,
    tool_call,
)


async def ask(agent, message, session="s1", **kw):
    return await agent.ainvoke(turn_input(message, **kw), config=run_config(session))


def always_calls_tools(messages, tools):
    if "search_knowledge_base" not in tools:  # the synthesis call has no tools bound
        return "Logs are kept 13 months [2]."
    n = sum(isinstance(m, AIMessage) for m in messages)
    return AIMessage(content="", tool_calls=[tool_call("search_knowledge_base", {"query": f"q{n}"}, n)])


async def test_step_bound_with_always_tool_calling_llm():
    kb = artifact_tool("search_knowledge_base")
    script = Script(default=always_calls_tools)
    out = await ask(build_agent(script, tools=[kb], max_steps=5), "logs?")

    assert out["steps"] == 5  # five agent LLM calls, then the bound stops the loop
    assert len(out["tool_results"]) == 4 and len(kb.metadata["calls"]) == 4
    assert script.runs() == [*(f"agent.step_{i}" for i in range(1, 6)), "synthesize"]
    assert script.calls[-1]["tools"] == []  # synthesis never gets tools
    assert "agent_step_limit" in out["flags"]
    assert out["answer"] == "Logs are kept 13 months [2]."


async def test_direct_answer_needs_no_synthesis_call():
    judge = FakeJudge([("Logs are kept 13 months", True)])
    script = Script(["Customer logs are retained for 13 months [2][1]."])
    out = await ask(build_agent(script, judge=judge), "What is the retention period for customer logs?")

    assert script.runs() == ["agent.step_1"]
    assert out["model"] == "gpt-4o-mini" and out["steps"] == 1 and out["tool_results"] == []
    assert out["grounding"].score == 1.0 and out["grounding"].grounded
    assert [c.doc_id for c in citations_for(out["answer"], out["contexts"])] == ["POL-COMP-001", "FAQ-030"]
    human = script.calls[0]["messages"][1].content
    assert human.startswith("Context:\n[1] (FAQ-030") and "[G1] Data-Platform team OWNS Ledger DB" in human
    assert "Question: What is the retention period for customer logs?" in human


async def test_chit_chat_skips_retrieval_and_judge():
    retriever, judge = FakeRetriever(), FakeJudge()
    router = FakeRouter(lambda m: plan(m, intent="chit_chat"))
    script = Script(["Hello! Ask me about Acme's policies or systems."])
    out = await ask(build_agent(script, router=router, retriever=retriever, judge=judge), "hi there")

    assert retriever.calls == [] and judge.calls == []
    assert script.runs() == ["synthesize"] and script.calls[0]["tools"] == []
    assert out["grounding"].skipped == "chit_chat" and out["answer"].startswith("Hello")


async def test_tool_results_continue_the_citation_ids():
    new = RetrievedChunk(chunk_id="SOP-OPS-014::003", doc_id="SOP-OPS-014", text="Run ledgerctl promote.",
                         score=1.0, source_type="document", metadata={"title": "Database Failover"})
    fact = RetrievedChunk(chunk_id="graph:owners:1", doc_id="graph", text="Ops OWNS Ledger DB", score=1.0,
                          source_type="graph", metadata={"source": "system_owners.csv"})
    kb = artifact_tool("search_knowledge_base", [CHUNKS[1], new, fact])  # CHUNKS[1] is already [2]
    script = Script([AIMessage(content="", tool_calls=[tool_call("search_knowledge_base", {"query": "x"})]),
                     "Run ledgerctl promote [3]; Ops owns it [G2]; logs 13 months [2]."])
    out = await ask(build_agent(script, tools=[kb]), "how do I promote?")

    ids = [c.metadata["cite_id"] for c in out["contexts"]]
    assert ids == ["1", "2", "G1", "3", "G2"]  # the duplicate kept "2"
    tool_msg = next(m for m in script.calls[1]["messages"] if isinstance(m, ToolMessage))
    assert tool_msg.content.startswith("[2] (POL-COMP-001") and "[3] (SOP-OPS-014" in tool_msg.content
    assert "[G2] Ops OWNS Ledger DB  (graph: system_owners.csv)" in tool_msg.content
    cites = citations_for(out["answer"], out["contexts"])
    assert [(c.id, c.doc_id) for c in cites] == [("3", "SOP-OPS-014"), ("G2", None), ("2", "POL-COMP-001")]
    assert out["tool_results"][0].status == "ok" and out["tool_results"][0].args == {"query": "x"}


async def test_unknown_tool_and_bad_arguments_come_back_as_error_strings():
    kb = artifact_tool("search_knowledge_base")
    script = Script([AIMessage(content="", tool_calls=[tool_call("drop_database", {}, 1),
                                                       tool_call("search_knowledge_base", {"wrong": 1}, 2)]),
                     IDK])
    out = await ask(build_agent(script, tools=[kb]), "q")

    assert [(r.name, r.status) for r in out["tool_results"]] == [("drop_database", "error"),
                                                                 ("search_knowledge_base", "error")]
    msgs = [m for m in script.calls[1]["messages"] if isinstance(m, ToolMessage)]
    assert msgs[0].content == "error: unknown tool 'drop_database'" and msgs[1].content.startswith("error:")
    assert out["grounding"].skipped == "abstained"


async def test_grounding_failure_regenerates_once_then_succeeds():
    judge = FakeJudge([("Logs are kept 99 months", False)], [("Logs are kept 13 months", True)])
    script = Script(["Logs are kept 99 months [2].", "Logs are kept 13 months [2]."])
    out = await ask(build_agent(script, judge=judge), "logs?")

    assert script.runs() == ["agent.step_1", "synthesize"]
    assert "Logs are kept 99 months" in script.calls[1]["messages"][-1].content  # the feedback
    assert out["retries"] == 1 and out["flags"] == ["regenerated_ungrounded"]
    assert out["answer"] == "Logs are kept 13 months [2]." and out["grounding"].grounded


async def test_grounding_failure_twice_falls_back_to_a_partial_answer():
    bad = [("Logs are kept 13 months", True), ("Backups are kept 99 years", False)]
    script = Script(["Logs are kept 13 months [2]. Backups are kept 99 years [1].",
                     "Logs are kept 13 months [2]. Backups are kept 99 years [1]."])
    out = await ask(build_agent(script, judge=FakeJudge(bad)), "retention?")

    assert out["flags"] == ["regenerated_ungrounded", "ungrounded_fallback"]
    assert out["answer"] == f"Logs are kept 13 months [2].\n\n{DISCLAIMER}"
    assert out["grounding"].score == 0.5


def test_drop_unsupported_keeps_supported_sentences_and_layout():
    answer = "Steps:\n- Freeze writes [1].\n- Promote the replica with sudo rm -rf [2].\nDone [1]."
    assert drop_unsupported(answer, ["promote the replica with sudo rm -rf"]) == \
        "Steps:\n- Freeze writes [1].\nDone [1]."


async def test_follow_up_in_the_same_session_sees_history_and_resets_working_state():
    router = FakeRouter()
    kb = artifact_tool("search_knowledge_base")
    script = Script([AIMessage(content="", tool_calls=[tool_call("search_knowledge_base", {"query": "a"})]),
                     "Data-Platform owns Ledger DB [G1].", "Anthony Gonzalez manages it [G1]."])
    agent = build_agent(script, router=router, tools=[kb])
    await ask(agent, "Who owns Ledger DB?", session="s9")
    out = await ask(agent, "and who manages that team?", session="s9")

    assert router.calls[0] == ("Who owns Ledger DB?", [])
    assert router.calls[1] == ("and who manages that team?",
                               [("Who owns Ledger DB?", "Data-Platform owns Ledger DB [G1].")])
    assert out["tool_results"] == [] and out["steps"] == 1  # turn 1's tool call didn't carry over
    assert [type(m).__name__ for m in out["messages"]] == ["HumanMessage", "AIMessage"] * 2
    assert "Conversation so far:\nUser: Who owns Ledger DB?" in script.calls[-1]["messages"][1].content
    other = await ask(agent, "and who manages that team?", session="other")
    assert router.calls[-1][1] == [] and len(other["messages"]) == 2


class PrefetchingRetriever(FakeRetriever):
    """Records the prefetches classify starts and the ones retrieve hands over."""

    class Pre:
        def __init__(self, query, filters):
            self.query, self.filters, self.cancelled = query, filters, False

        def cancel(self):
            self.cancelled = True

    def __init__(self):
        super().__init__()
        self.started, self.received = [], []

    def prefetch(self, query, filters=None):
        self.started.append(self.Pre(query, filters))
        return self.started[-1]

    async def search(self, plan, filters, k, prefetched=None):
        self.received.append(prefetched)
        return await super().search(plan, filters, k)


async def test_first_turn_prefetches_retrieval_and_follow_ups_do_not():
    retriever = PrefetchingRetriever()
    script = Script(["Data-Platform owns Ledger DB [G1].", "Anthony Gonzalez manages it [G1]."])
    agent = build_agent(script, retriever=retriever)
    await ask(agent, "Who owns Ledger DB?", session="p1", filters={"domain": "engineering"})
    assert [(p.query, p.filters) for p in retriever.started] == [("Who owns Ledger DB?",
                                                                  {"domain": "engineering"})]
    assert retriever.received == retriever.started  # retrieve picked up the same prefetch
    await ask(agent, "and who manages that team?", session="p1")  # follow-up: needs the router's rewrite
    assert len(retriever.started) == 1 and retriever.received[-1] is None

    chat = PrefetchingRetriever()  # chit-chat never retrieves: the prefetch is cancelled
    router = FakeRouter(lambda m: plan(m, intent="chit_chat"))
    await ask(build_agent(Script(["Hello!"]), router=router, retriever=chat), "hi there", session="p2")
    assert chat.started[0].cancelled and chat.received == []


async def test_sqlite_checkpointer_keeps_the_session_across_restarts(tmp_path):
    db = tmp_path / "sessions.db"
    async with open_checkpointer(db) as saver:
        await ask(build_agent(Script(["First answer [1]."]), checkpointer=saver), "first?", session="s")
    router = FakeRouter()
    async with open_checkpointer(db) as saver:  # a new process would open the same file
        out = await ask(build_agent(Script(["Second [1]."]), router=router, checkpointer=saver), "second?",
                        session="s")
    assert router.calls == [("second?", [("first?", "First answer [1].")])]
    assert out["pii_map"] == {} and len(out["messages"]) == 4


async def test_allow_web_false_unbinds_web_search():
    tools = [artifact_tool("search_knowledge_base"), artifact_tool("web_search")]
    script = Script(["x [1].", "y [1]."])
    agent = build_agent(script, tools=tools)
    await ask(agent, "q", session="a")
    await ask(agent, "q", session="b", allow_web=False)
    assert script.calls[0]["tools"] == ["search_knowledge_base", "web_search"]
    assert script.calls[1]["tools"] == ["search_knowledge_base"]


async def test_complex_questions_route_to_the_strong_model_and_long_loops_upgrade():
    kb = artifact_tool("search_knowledge_base")
    router = FakeRouter(lambda m: plan(m, intent="multi_hop", complexity="complex"))
    script = Script(["A [1]."])
    out = await ask(build_agent(script, router=router, tools=[kb]), "multi-hop?")
    assert out["model"] == "gpt-4o" and script.calls[0]["model"] == "gpt-4o"

    calls = [AIMessage(content="", tool_calls=[tool_call("search_knowledge_base", {"query": str(i)}, i)])
             for i in range(2)]
    script = Script([*calls, "B [1]."])
    out = await ask(build_agent(script, tools=[kb]), "simple?", session="s2")
    assert [c["model"] for c in script.calls] == ["gpt-4o-mini", "gpt-4o-mini", "gpt-4o"]  # step 3 upgrades
    assert out["model"] == "gpt-4o"


async def test_request_filters_reach_retrieval_and_bad_keys_raise():
    retriever = FakeRetriever()
    agent = build_agent(Script(["x [1]."]), retriever=retriever)
    await ask(agent, "q", filters={"domain": "operations"})
    assert retriever.calls == [("q", {"domain": "operations"}, 6)]
    try:
        await ask(agent, "q", session="s2", filters={"owner": "x"})
    except ValueError as e:
        assert "owner" in str(e)
    else:
        raise AssertionError("bad filter keys must raise")
