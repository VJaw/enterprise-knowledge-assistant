"""FastAPI entrypoint: app factory, lifespan-managed clients, routers."""
import asyncio
import logging
import time
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from qdrant_client import QdrantClient

from app.agent.graph import build_graph, open_checkpointer
from app.agent.nodes import AgentNodes
from app.api import admin, chat, errors, health, sessions
from app.cache.semantic_cache import SemanticCache
from app.config import get_settings
from app.graph.neo4j_client import Neo4jClient
from app.guardrails.rails import build_guardrails
from app.llm.router import ModelRouter
from app.observability.langfuse import init_langfuse, llm_callbacks
from app.retrieval.bm25 import BM25Retriever
from app.retrieval.dense import COLLECTION, DenseRetriever
from app.retrieval.embeddings import embed_query
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


async def graph_watchdog(app: FastAPI, every_s: float) -> None:
    """§B12.3: probe Aura every `every_s`. Reconnect (and load the graph if it is empty) when it was marked
    unavailable; mark it unavailable when a probe of a working connection fails. Retrieval reads the flag."""
    graph: Neo4jClient = app.state.graph
    while True:
        await asyncio.sleep(every_s)
        try:
            if not app.state.graph_available:
                if await ensure_graph(graph):
                    app.state.graph_available = True
                    log.warning("Neo4j Aura reachable again: graph retrieval re-enabled")
            else:
                await asyncio.wait_for(graph.driver.verify_connectivity(), timeout=5)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            if app.state.graph_available:
                log.warning("Neo4j Aura probe failed (%s): graph retrieval disabled", type(e).__name__)
            app.state.graph_available = False


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

    # Model warm-up: load the weights and run one forward pass each, before the first request.
    await asyncio.to_thread(lambda: embed_query("warm up"))
    if s.use_rerank:  # one forward pass too: the first predict() is ~2x slower than steady state
        await asyncio.to_thread(lambda: get_cross_encoder().predict([("warm up", "warm up")]))
    app.state.qdrant = QdrantClient(path=s.qdrant_path)
    await asyncio.to_thread(ensure_index, app.state.qdrant)
    app.state.graph_available = await ensure_graph(app.state.graph)
    app.state.cache = SemanticCache(app.state.qdrant, threshold=s.cache_sim_threshold,
                                    ttl_s=s.cache_ttl_seconds) if s.use_cache else None
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
    watchdog = asyncio.create_task(graph_watchdog(app, s.graph_retry_seconds)) if s.use_graph else None
    try:
        yield
    finally:
        if watchdog is not None:
            watchdog.cancel()
        await stack.aclose()
        await app.state.http.aclose()
        await app.state.graph.close()
        await app.state.openai.close()
        app.state.qdrant.close()
        app.state.langfuse.shutdown()


DESCRIPTION = """Acme Corp's enterprise knowledge assistant: hybrid vector + graph RAG (Qdrant, BM25, Neo4j
Aura, cross-encoder re-ranking) with a bounded LangGraph agent (knowledge base, graph, SQL, Python, web and
MCP tools), NeMo Guardrails, a Qdrant semantic cache and Langfuse tracing. See capstonespec.md.

* Answers cite their sources: `[n]` passages, `[Gn]` graph facts, `[SQLn]` query results, `[WEBn]` web
  results.
* Guardrail refusals are normal 200 responses with `GUARDRAIL_BLOCKED` in `guardrail_flags`.
* Errors use one shape: `{"error": {"code", "message", "trace_id"}}` (`VALIDATION_ERROR` 422,
  `RATE_LIMITED` 429, `UPSTREAM_TIMEOUT` 504, `INTERNAL_ERROR` 500, plus `UNAUTHORIZED` 401 /
  `NOT_FOUND` 404).
* Every chat response carries a `trace_id` for its Langfuse trace.
"""

app = FastAPI(
    title="Enterprise Knowledge Assistant",
    version="1.1.0",
    description=DESCRIPTION,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "chat", "description": "Questions and answers (§B11.1)"},
        {"name": "sessions", "description": "Conversation history held by the agent's checkpointer"},
        {"name": "admin", "description": "Re-indexing; needs the `X-Admin-Token` header"},
        {"name": "ops", "description": "Health and Prometheus metrics"},
    ],
)
def openapi() -> dict:
    """FastAPI documents its own 422 body (`HTTPValidationError`); ours is the §B11.3 `ErrorResponse`."""
    if app.openapi_schema is None:
        spec = get_openapi(title=app.title, version=app.version, description=app.description,
                           routes=app.routes, tags=app.openapi_tags)
        for op in (op for path in spec["paths"].values() for op in path.values()):
            if "422" in op.get("responses", {}):
                op["responses"]["422"] = {"description": "VALIDATION_ERROR", "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}}}}
        for name in ("HTTPValidationError", "ValidationError"):
            spec["components"]["schemas"].pop(name, None)
        app.openapi_schema = spec
    return app.openapi_schema


app.openapi = openapi
app.state.limiter = chat.limiter  # slowapi's convention; /chat counts hits per session in its storage
errors.install(app)
app.include_router(chat.router)
app.include_router(sessions.router)
app.include_router(admin.router)
app.include_router(health.router)

if get_settings().use_ui:
    from app.ui.gradio_app import mount  # imported only when on: gradio is a heavy import

    app = mount(app)
