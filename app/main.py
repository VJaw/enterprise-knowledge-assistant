"""FastAPI entrypoint: app factory, lifespan-managed clients, routers."""
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from langfuse import Langfuse
from neo4j import AsyncGraphDatabase
from openai import AsyncOpenAI

from app.api import health
from app.config import get_settings


def _secret(v) -> str | None:
    return v.get_secret_value() if v else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    app.state.settings = s
    app.state.openai = AsyncOpenAI(api_key=_secret(s.openai_api_key), timeout=30)
    app.state.neo4j = AsyncGraphDatabase.driver(
        s.neo4j_uri, auth=(s.neo4j_username, _secret(s.neo4j_password))
    )
    app.state.langfuse = Langfuse(
        public_key=_secret(s.langfuse_public_key),
        secret_key=_secret(s.langfuse_secret_key),
        host=s.langfuse_host,
    )
    app.state.http = httpx.AsyncClient(timeout=10)
    try:
        yield
    finally:
        await app.state.http.aclose()
        await app.state.neo4j.close()
        await app.state.openai.close()
        app.state.langfuse.shutdown()


app = FastAPI(
    title="Enterprise Knowledge Assistant",
    version="0.1.0",
    description="Hybrid vector + graph RAG with a bounded LangGraph agent. See capstonespec.md.",
    lifespan=lifespan,
)
app.include_router(health.router)
