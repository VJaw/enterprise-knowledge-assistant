"""Index builders (§B6.2, §B6.3): Qdrant `documents` collection + payload indexes, and the BM25 pickle."""
from __future__ import annotations

import pickle
import uuid
import warnings
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, PointStruct, VectorParams
from rank_bm25 import BM25Okapi

from app.retrieval.bm25 import tokenize
from app.retrieval.embeddings import DIM, embed_passages
from ingestion.chunk import Chunk, index_text

DOCUMENTS = "documents"
SEMANTIC_CACHE = "semantic_cache"
PAYLOAD_INDEXES = ["doc_type", "domain", "sensitivity", "doc_id", "entities"]
INDEX_DIR = Path("data/indexes")
BATCH = 64


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def chunk_payload(c: Chunk) -> dict:
    """Flat payload: metadata fields at the top level so they can be filtered on."""
    return {**c.metadata.model_dump(mode="json"), "chunk_id": c.chunk_id, "chunk_index": c.chunk_index,
            "text": c.text, "heading_path": c.heading_path, "entities": c.entities}


def build_qdrant(chunks: list[Chunk], client: QdrantClient) -> int:
    if client.collection_exists(DOCUMENTS):
        client.delete_collection(DOCUMENTS)
    client.create_collection(DOCUMENTS, vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))
    with warnings.catch_warnings():  # embedded Qdrant ignores payload indexes; kept for a server Qdrant
        warnings.simplefilter("ignore", UserWarning)
        for field in PAYLOAD_INDEXES:
            client.create_payload_index(DOCUMENTS, field, PayloadSchemaType.KEYWORD)
    if not client.collection_exists(SEMANTIC_CACHE):  # filled from Phase 9 (§B12.1)
        client.create_collection(SEMANTIC_CACHE,
                                 vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))

    vectors = embed_passages([index_text(c) for c in chunks])
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        client.upsert(DOCUMENTS, points=[
            PointStruct(id=point_id(c.chunk_id), vector=v.tolist(), payload=chunk_payload(c))
            for c, v in zip(batch, vectors[i:i + BATCH], strict=True)
        ])
    return client.count(DOCUMENTS, exact=True).count


def build_bm25(chunks: list[Chunk], path: Path) -> None:
    corpus = [tokenize(index_text(c)) for c in chunks]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump({"chunk_ids": [c.chunk_id for c in chunks], "bm25": BM25Okapi(corpus)}, f)


def write_chunks(chunks: list[Chunk], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for c in chunks:
            f.write(c.model_dump_json() + "\n")


def build_indexes(chunks: list[Chunk], client: QdrantClient, index_dir: Path = INDEX_DIR) -> dict:
    n = build_qdrant(chunks, client)
    build_bm25(chunks, index_dir / "bm25.pkl")
    write_chunks(chunks, index_dir / "chunks.jsonl")
    return {"qdrant_points": n, "bm25_docs": len(chunks)}
