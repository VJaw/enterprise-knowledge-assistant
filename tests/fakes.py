"""Fakes for the agent graph: a scripted chat model, router, retriever and grounding judge. No network."""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from app.agent.graph import build_graph
from app.agent.nodes import AgentNodes
from app.core.types import RetrievedChunk
from app.llm.router import ModelRouter
from app.retrieval.dense import FilterError
from app.retrieval.hybrid import HybridResult
from app.retrieval.router import QueryPlan

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
FACTS = [RetrievedChunk(chunk_id="graph:1", doc_id="graph",
                        text="Data-Platform team OWNS Ledger DB; manager: Anthony Gonzalez", score=1.0,
                        source_type="graph", metadata={"source": "system_owners.csv", "template": "owners"})]


def plan(message: str = "q", intent: str = "factual", complexity: str = "simple") -> QueryPlan:
    return QueryPlan(intent=intent, complexity=complexity, entities=[], filters={}, rewritten_query=message,
                     sub_questions=[])


def tool_call(name: str, args: dict, i: int = 0) -> dict:
    return {"name": name, "args": args, "id": f"call_{name}_{i}", "type": "tool_call"}


class Script:
    """Replies shared by every fake model: each is an AIMessage, a string, or fn(messages, tool_names)."""

    def __init__(self, replies=(), default="Done [1]."):
        self.replies = list(replies)
        self.default = default
        self.calls: list[dict] = []

    def next(self, model: str, messages, tools, config) -> AIMessage:
        names = [t.name for t in tools]
        self.calls.append({"model": model, "messages": list(messages), "tools": names,
                           "run_name": (config or {}).get("run_name")})
        reply = self.replies.pop(0) if self.replies else self.default
        if callable(reply):
            reply = reply(messages, names)
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(content=reply) if isinstance(reply, str) else reply

    def runs(self) -> list[str]:
        return [c["run_name"] for c in self.calls]


class FakeChat:
    def __init__(self, model: str, script: Script):
        self.model, self.script, self.tools = model, script, []

    def bind_tools(self, tools):
        bound = FakeChat(self.model, self.script)
        bound.tools = list(tools)
        return bound

    async def ainvoke(self, messages, config=None):
        return self.script.next(self.model, messages, self.tools, config)


def fake_models(script: Script) -> ModelRouter:
    return ModelRouter("gpt-4o-mini", "gpt-4o", factory=lambda m: FakeChat(m, script))


class FakeRouter:
    def __init__(self, make_plan=None):
        self.calls = []
        self.make_plan = make_plan or (lambda message: plan(message))

    async def plan(self, message, history=None):
        self.calls.append((message, list(history or [])))
        return self.make_plan(message)


class FakeRetriever:
    def __init__(self, chunks=CHUNKS, facts=FACTS, flags=None):
        self.calls = []
        self.chunks, self.facts, self.flags = chunks, facts, flags or []

    async def search(self, plan, filters, k):
        self.calls.append((plan.rewritten_query, filters, k))
        if filters and "owner" in filters:
            raise FilterError("unsupported filter keys: ['owner']")
        return HybridResult(chunks=self.chunks, facts=self.facts, flags=list(self.flags))


class FakeJudge:
    """`with_structured_output(Judgement)`; each verdict is a list of (claim, supported) pairs."""

    def __init__(self, *verdicts):
        self.verdicts = list(verdicts) or [[("claim", True)]]
        self.calls = []

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        v = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        return self.schema(claims=[{"claim": c, "supported": s} for c, s in v])


class EchoArgs(BaseModel):
    query: str


def artifact_tool(name: str, items: list[RetrievedChunk] | None = None,
                  content: str = "ok") -> StructuredTool:
    """A tool returning (content, artifact) like the real ones; records its calls in `.calls`."""
    calls = []

    async def run(query: str):
        calls.append(query)
        return content, list(items or [])

    t = StructuredTool.from_function(coroutine=run, name=name, description=f"fake {name}",
                                     args_schema=EchoArgs, response_format="content_and_artifact")
    t.metadata = {"calls": calls}
    return t


def build_agent(script: Script, *, router=None, retriever=None, tools=(), judge=None, max_steps=5,
                checkpointer=None, guard=None):
    nodes = AgentNodes(router=router or FakeRouter(), retriever=retriever or FakeRetriever(),
                       models=fake_models(script), tools=list(tools), judge=judge or FakeJudge(),
                       max_steps=max_steps, guard=guard)
    return build_graph(nodes, checkpointer or MemorySaver())
