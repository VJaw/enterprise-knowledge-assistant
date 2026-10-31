"""FastAPI entrypoint: app factory, lifespan-managed clients, routers."""
import asyncio
import logging
import time
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from qdrant_client import QdrantClient

from app.agent.graph import build_graph, open_checkpointer
from app.agent.nodes import AgentNodes
from app.api import chat, health
from app.config import get_settings
from app.graph.neo4j_client import Neo4jClient
from app.guardrails.rails import build_guardrails
from app.llm.router import ModelRouter
from app.observability.langfuse import init_langfuse, llm_callbacks
from app.retrieval.bm25 import BM25Retriever
from app.retrieval.dense import COLLECTION, DenseRetriever
from app.retrieval.embeddings import get_embedder
from app.retrieval.graph import GraphRetriever
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.rerank import CrossEncoderReranker, get_cross_encoder
from app.retrieval.router import Router, load_filter_pairs, load_known_entities
from app.tools import build_tools
from app.tools.mcp import MCPTools, server_config

log = logging.getLogger("uvicorn.error")


def _secret(v) -> str | None:
    return v.get_secret_value() if v else None


def ensure_index(client: QdrantClient) -> None:
    """§B4.3: indexes are baked into the image; rebuild from data/processed if the collection is missing."""
    if client.collection_exists(COLLECTION):
        return
    from ingestion.run import index_documents, load_processed

    log.warning("Qdrant collection %r missing: building it from data/processed/docs.jsonl", COLLECTION)
    stats = index_documents(load_processed(), client)
    log.warning("built %s chunks", stats["qdrant_points"])


async def ensure_graph(graph: Neo4jClient) -> bool:
    """§B4.3: verify Aura (3 retries with backoff); load the committed graph CSVs if it is empty.

    Returns False when Aura is unreachable: the app still starts and serves dense (+ BM25) only (§B12.3).
    """
    if not await graph.connect():
        log.warning("Neo4j Aura unreachable: graph retrieval disabled (graph_unavailable)")
        return False
    try:
        if await graph.node_count() == 0:
            from ingestion.load_graph import load_all

            log.warning("Neo4j graph is empty: loading it from data/processed/graph + data/raw/tables")
            nodes, rels = await asyncio.to_thread(load_all)
            log.warning("graph loaded: %s nodes, %s relationships", nodes, rels)
    except Exception as e:  # noqa: BLE001
        log.warning("Neo4j graph check/load failed (%s): graph retrieval disabled", type(e).__name__)
        return False
    return True


async def prefill_guard_memo() -> None:
    """Scan every indexed passage once (injection strip + PII analysis) so retrieved passages hit the memo."""
    from app.guardrails.actions import prefill_corpus

    t0 = time.perf_counter()
    n = await asyncio.to_thread(prefill_corpus)
    log.warning("guardrails: pre-scanned %s passages in %.1f s", n, time.perf_counter() - t0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    app.state.settings = s
    app.state.openai = AsyncOpenAI(api_key=_secret(s.openai_api_key), timeout=30)
    app.state.graph = Neo4jClient(s.neo4j_uri, s.neo4j_username, _secret(s.neo4j_password))
    app.state.neo4j = app.state.graph.driver  # /health checks the driver directly
    app.state.langfuse = init_langfuse(s)
    app.state.http = httpx.AsyncClient(timeout=10)

    await asyncio.to_thread(get_embedder)  # load weights once, before the first request
    if s.use_rerank:  # one forward pass too: the first predict() is ~2x slower than steady state
        await asyncio.to_thread(lambda: get_cross_encoder().predict([("warm up", "warm up")]))
    app.state.qdrant = QdrantClient(path=s.qdrant_path)
    await asyncio.to_thread(ensure_index, app.state.qdrant)
    app.state.graph_available = await ensure_graph(app.state.graph)
    dense = DenseRetriever(app.state.qdrant)
    graph_retriever = GraphRetriever(app.state.graph, dense)
    app.state.retriever = HybridRetriever(
        dense, BM25Retriever(), graph_retriever, CrossEncoderReranker(),
        use_bm25=s.use_bm25, use_graph=s.use_graph, use_rerank=s.use_rerank,
        graph_available=lambda: app.state.graph_available)
    app.state.llm = ChatOpenAI(model=s.model_fast, temperature=0, api_key=_secret(s.openai_api_key),
                               timeout=30, max_retries=2)
    app.state.router = Router(app.state.llm, load_known_entities(), load_filter_pairs())

    # §B4.3 / §B10: NeMo LLMRails + the guard models (Presidio/spaCy, LLM Guard), warmed up once. The
    # self-check model is a separate client so its generations are traced (callback bound at construction).
    app.state.guardrails = None
    if s.use_guardrails:
        guard_llm = ChatOpenAI(model=s.model_fast, temperature=0, api_key=_secret(s.openai_api_key),
                               timeout=30, max_retries=2, callbacks=llm_callbacks(), name="nemo.self_check")
        app.state.guardrails = await build_guardrails(guard_llm, judge=app.state.llm)
        # In the background: startup doesn't wait for it.
        app.state.guard_prefill = asyncio.create_task(prefill_guard_memo())

    # §B4.3: MCP sessions and the compiled agent graph. The exit stack closes them in reverse order.
    stack = AsyncExitStack()
    tools = build_tools(s, app.state.retriever, graph_retriever)
    app.state.mcp = None
    if s.use_mcp:
        app.state.mcp = MCPTools(server_config(s))
        stack.push_async_callback(app.state.mcp.close)
        tools += await app.state.mcp.start()
    checkpointer = await stack.enter_async_context(open_checkpointer(s.sessions_db))
    app.state.models = ModelRouter.from_settings(s)
    app.state.tools = tools
    app.state.agent = build_graph(
        AgentNodes(router=app.state.router, retriever=app.state.retriever, models=app.state.models,
                   tools=tools, judge=app.state.llm, max_steps=s.agent_max_steps, guard=app.state.guardrails),
        checkpointer)
    log.warning("agent tools: %s", ", ".join(t.name for t in tools))
    try:
        yield
    finally:
        await stack.aclose()
        await app.state.http.aclose()
        await app.state.graph.close()
        await app.state.openai.close()
        app.state.qdrant.close()
        app.state.langfuse.shutdown()


app = FastAPI(
    title="Enterprise Knowledge Assistant",
    version="0.1.0",
    description="Hybrid vector + graph RAG with a bounded LangGraph agent. See capstonespec.md.",
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(chat.router)
