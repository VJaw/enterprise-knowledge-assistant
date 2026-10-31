"""DenseRetriever over an in-memory Qdrant with a fake embedder, plus the BM25 tokeniser."""
import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.retrieval.bm25 import tokenize
from app.retrieval.dense import DenseRetriever, build_filter

TOPICS = ["retention", "failover", "refund"]


def fake_embed(text: str) -> list[float]:
    v = [1.0 if t in text.lower() else 0.0 for t in TOPICS]
    return v if any(v) else [0.1, 0.1, 0.1]


@pytest.fixture
def retriever():
    client = QdrantClient(":memory:")
    client.create_collection("documents", vectors_config=VectorParams(size=3, distance=Distance.COSINE))
    rows = [("POL-COMP-001::000", "POL-COMP-001", "policy", "Customer logs: retention 13 months."),
            ("SOP-OPS-014::003", "SOP-OPS-014", "sop", "Manual failover promotion steps."),
            ("SOP-SUP-003::001", "SOP-SUP-003", "sop", "Refund over $5,000 needs Finance.")]
    client.upsert("documents", points=[
        PointStruct(id=i, vector=fake_embed(text),
                    payload={"chunk_id": cid, "doc_id": did, "doc_type": dt, "text": text, "title": did})
        for i, (cid, did, dt, text) in enumerate(rows)])
    return DenseRetriever(client, embed=fake_embed)


async def test_dense_returns_best_match_first(retriever):
    chunks = await retriever.retrieve("what is the log retention period?", None, k=2)
    assert chunks[0].doc_id == "POL-COMP-001"
    assert chunks[0].source_type == "document" and chunks[0].score > chunks[1].score
    assert chunks[0].metadata["doc_type"] == "policy" and "text" not in chunks[0].metadata


async def test_dense_metadata_filter(retriever):
    chunks = await retriever.retrieve("retention", {"doc_type": "sop"}, k=5)
    assert {c.doc_id for c in chunks} == {"SOP-OPS-014", "SOP-SUP-003"}


def test_unknown_filter_key_rejected():
    with pytest.raises(ValueError, match="unsupported filter keys"):
        build_filter({"owner": "TEAM-01"})


def test_bm25_tokenize_keeps_ids_and_drops_stop_words():
    toks = tokenize("What is SOP-OPS-014 and INC-2025-031 about the Ledger DB failover?")
    assert "sop-ops-014" in toks and "inc-2025-031" in toks
    assert {"ledger", "db", "failover"} <= set(toks)
    assert not {"what", "is", "the", "and", "sop", "ops", "014"} & set(toks)
