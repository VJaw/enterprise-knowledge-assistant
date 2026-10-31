# Phase 7: API + hybrid retrieval + agent. Node (npx) and uv (uvx) run the MCP servers (§B4.5).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false

# HF Spaces runs as uid 1000
RUN useradd -m -u 1000 user
WORKDIR /app

# CPU-only torch first, so the pinned lock doesn't pull the CUDA build and its nvidia-* wheels.
COPY requirements.lock .
RUN pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.lock \
    && chown user:user /app

# Node for the filesystem MCP server; uv provides uvx for the sqlite MCP server. After the pip layer, so adding
# them doesn't invalidate the cached torch / requirements layers.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/* && pip install uv==0.13.6

# Everything below runs as the Space user, so baked models and indexes are writable at runtime.
USER user
COPY --chown=user:user app/__init__.py app/config.py app/
COPY --chown=user:user app/bootstrap app/bootstrap
RUN python -m app.bootstrap.download_models      # bake embedding/re-ranker weights

# Pre-fetch the pinned MCP servers into the user's caches so startup doesn't download them (same pins as
# app/config.py). The servers exit as soon as stdin closes.
RUN uvx --with "mcp<2" --from mcp-server-sqlite==2025.4.25 mcp-server-sqlite --help > /dev/null
RUN mkdir -p /tmp/mcp-root \
    && (npx -y @modelcontextprotocol/server-filesystem@2.1.0 /tmp/mcp-root < /dev/null > /dev/null 2>&1 || true) \
    && ls ~/.npm/_npx > /dev/null

COPY --chown=user:user . .
RUN python -m ingestion.run --build-indexes --skip-graph   # bake Qdrant + BM25 + SQLite (graph lives in Aura)

EXPOSE 7860
CMD ["/app/docker/entrypoint.sh"]
