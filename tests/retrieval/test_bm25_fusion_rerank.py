"""BM25Retriever over a tiny built index, RRF fusion, and the cross-encoder wrapper with a fake model."""
import json
import pickle

import pytest
from rank_bm25 import BM25Okapi

from app.core.types import RetrievedChunk
from app.retrieval.bm25 import BM25Retriever, matches, tokenize
from app.retrieval.fusion import rrf
from app.retrieval.rerank import CrossEncoderReranker, passage

ROWS = [
    ("SOP-OPS-014::003", "SOP-OPS-014", "sop", "Run ledgerctl promote --replica ledger-db-replica-1.",
     ["Ledger DB"]),
    ("INC-2025-012::000", "INC-2025-012", "incident", "Ledger DB primary failed; manual promotion.",
     ["Ledger DB"]),
    ("FAQ-004::000", "FAQ-004", "faq", "Customer logs are retained for 13 months.", []),
    ("SOP-OPS-009::002", "SOP-OPS-009", "sop", "Use vaultctl rotate every 90 days.", ["Secrets Vault"]),
]


def chunk(cid, score=0.0, doc_id=None, **meta):
    return RetrievedChunk(chunk_id=cid, doc_id=doc_id or cid.split("::")[0], text=f"text of {cid}",
                          score=score, source_type="document", metadata={"title": "T", **meta})


@pytest.fixture
def bm25(tmp_path):
    rows = [{"chunk_id": cid, "doc_id": did, "chunk_index": 0, "text": text, "heading_path": "Procedure",
             "entities": ents,
             "metadata": {"doc_id": did, "title": did, "doc_type": dt, "domain": "operations",
                          "sensitivity": "internal"}}
            for cid, did, dt, text, ents in ROWS]
    with open(tmp_path / "chunks.jsonl", "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    corpus = [tokenize(f"{r['doc_id']} {r['text']}") for r in rows]
    with open(tmp_path / "bm25.pkl", "wb") as f:
        pickle.dump({"chunk_ids": [r["chunk_id"] for r in rows], "bm25": BM25Okapi(corpus)}, f)
    return BM25Retriever(tmp_path)


async def test_bm25_exact_command_and_id_lookup(bm25):
    hits = await bm25.retrieve("ledgerctl promote", None, k=5)
    assert hits[0].doc_id == "SOP-OPS-014" and hits[0].score > 0
    assert hits[0].metadata["doc_type"] == "sop" and hits[0].metadata["entities"] == ["Ledger DB"]
    assert (await bm25.retrieve("What happened in INC-2025-012?", None, k=5))[0].doc_id == "INC-2025-012"


async def test_bm25_filters_k_and_no_match(bm25):
    hits = await bm25.retrieve("ledger promotion", {"doc_type": "incident"}, k=5)
    assert [h.doc_id for h in hits] == ["INC-2025-012"]
    assert len(await bm25.retrieve("ledger", None, k=1)) == 1
    assert await bm25.retrieve("the of and", None, k=5) == []  # only stop words
    assert await bm25.retrieve("kubernetes", None, k=5) == []  # zero-score docs are not returned
    with pytest.raises(ValueError):
        await bm25.retrieve("ledger", {"owner": "TEAM-01"}, k=5)


def test_matches_scalar_list_and_entities():
    meta = {"doc_type": "sop", "entities": ["Ledger DB", "Auth Service"]}
    assert matches(meta, {"doc_type": "sop"}) and matches(meta, {"doc_type": ["faq", "sop"]})
    assert matches(meta, {"entities": "Ledger DB"})
    assert matches(meta, {"entities": ["Kafka Cluster", "Auth Service"]})
    assert not matches(meta, {"doc_type": "faq"}) and not matches(meta, {"entities": ["Kafka Cluster"]})
    assert matches(meta, None)


def test_rrf_scores_dedupes_and_records_ranks():
    a, b, c = chunk("A::0"), chunk("B::0"), chunk("C::0")
    fused = rrf({"dense": [a, b], "bm25": [b, c, b], "graph": []})
    assert [f.chunk_id for f in fused] == ["B::0", "A::0", "C::0"]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)  # dense rank 2 + bm25 rank 1 (duplicate ignored)
    assert fused[0].metadata["ranks"] == {"dense": 2, "bm25": 1}
    assert fused[1].score == pytest.approx(1 / 61) and fused[2].score == pytest.approx(1 / 62)
    assert a.score == 0.0 and "ranks" not in a.metadata  # inputs are not mutated


class FakeCrossEncoder:
    def __init__(self, by_doc):
        self.by_doc = by_doc
        self.calls = []

    def predict(self, pairs, batch_size, show_progress_bar):
        self.calls.append(pairs)
        return [next(v for k, v in self.by_doc.items() if f"({k})" in text) for _, text in pairs]


def test_rerank_orders_caps_and_drops_low_scores():
    model = FakeCrossEncoder({"A": 0.2, "B": 0.9, "C": 0.05, "D": 0.5})
    chunks = [chunk(f"{d}::0", score=1 / (60 + i), heading_path="H") for i, d in enumerate("ABCD", 1)]
    out = CrossEncoderReranker(model).rerank("q", chunks, top_k=2)
    assert [c.chunk_id for c in out] == ["B::0", "D::0"]
    assert out[0].score == 0.9 and out[0].metadata["rrf"] == pytest.approx(1 / 62)
    out = CrossEncoderReranker(model).rerank("q", chunks, top_k=6)
    assert [c.chunk_id for c in out] == ["B::0", "D::0", "A::0"]  # C (0.05) is below the 0.1 floor
    lengths = [len(t) for _, t in model.calls[-1]]
    assert lengths == sorted(lengths)  # scored shortest first


async def test_rerank_scores_in_stages():
    model = FakeCrossEncoder({"A": 0.3, "B": 0.6})
    rr = CrossEncoderReranker(model)
    a, b = chunk("A::0"), chunk("B::0")
    scores = await rr.ascore("q", [a]) | await rr.ascore("q", [b])
    assert [c.chunk_id for c in rr.select([a, b], scores)] == ["B::0", "A::0"]
    assert await rr.ascore("q", []) == {}


def test_passage_carries_doc_id_and_heading():
    p = passage(chunk("INC-2025-012::001", title="Ledger DB Primary Failure", heading_path="Timeline"))
    assert p.startswith("Ledger DB Primary Failure (INC-2025-012) | Timeline\n")
