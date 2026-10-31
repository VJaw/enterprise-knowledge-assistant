# v0 (Phase 3): API + dense retrieval. Node and uv for the MCP servers are added in Phase 7.
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

# Everything below runs as the Space user, so baked models and indexes are writable at runtime.
USER user
COPY --chown=user:user app/__init__.py app/config.py app/
COPY --chown=user:user app/bootstrap app/bootstrap
RUN python -m app.bootstrap.download_models      # bake embedding/re-ranker weights

COPY --chown=user:user . .
RUN python -m ingestion.run --build-indexes --skip-graph   # bake Qdrant + BM25 + SQLite (graph lives in Aura)

EXPOSE 7860
CMD ["/app/docker/entrypoint.sh"]
