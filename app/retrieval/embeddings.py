"""bge-small embeddings (§B6.2): one shared model, normalised vectors, query prefix on queries only."""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np

from app.config import get_settings

# Tokenizers are used before uvicorn --reload / MCP subprocesses fork; silence the fork warning.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
DIM = 384


@lru_cache
def get_embedder():
    from sentence_transformers import SentenceTransformer  # heavy import, deferred until first use

    # CPU everywhere, so local runs behave like the HF Space.
    return SentenceTransformer(get_settings().embed_model, device="cpu")


def get_tokenizer():
    return get_embedder().tokenizer


def embed_passages(texts: list[str], batch_size: int = 32) -> np.ndarray:
    return get_embedder().encode(texts, batch_size=batch_size, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False)


def embed_query(query: str) -> list[float]:
    vec = get_embedder().encode(QUERY_PREFIX + query, normalize_embeddings=True, convert_to_numpy=True)
    return vec.tolist()
