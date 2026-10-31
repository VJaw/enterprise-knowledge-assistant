"""FastAPI entrypoint: app factory, lifespan-managed clients, routers."""
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from neo4j import AsyncGraphDatabase
from openai import AsyncOpenAI
from qdrant_client import QdrantClient

from app.api import chat, health
from app.config import get_settings
from app.observability.langfuse import init_langfuse
from app.retrieval.dense import COLLECTION, DenseRetriever
from app.retrieval.embeddings import get_embedder

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    app.state.settings = s
    app.state.openai = AsyncOpenAI(api_key=_secret(s.openai_api_key), timeout=30)
    app.state.neo4j = AsyncGraphDatabase.driver(
        s.neo4j_uri, auth=(s.neo4j_username, _secret(s.neo4j_password))
    )
    app.state.langfuse = init_langfuse(s)
    app.state.http = httpx.AsyncClient(timeout=10)

    await asyncio.to_thread(get_embedder)  # load weights once, before the first request
    app.state.qdrant = QdrantClient(path=s.qdrant_path)
    await asyncio.to_thread(ensure_index, app.state.qdrant)
    app.state.retriever = DenseRetriever(app.state.qdrant)
    app.state.llm = ChatOpenAI(model=s.model_fast, temperature=0, api_key=_secret(s.openai_api_key),
                               timeout=30, max_retries=2)
    try:
        yield
    finally:
        await app.state.http.aclose()
        await app.state.neo4j.close()
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
