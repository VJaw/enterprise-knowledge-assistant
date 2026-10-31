"""Retrieval validation (§B6.4): hand-labelled queries -> Recall@5, MRR@10 and nDCG@10.

Relevance is judged at document level: the retrieved chunks are collapsed to their doc_ids in rank order, and
a query's relevant set lists every doc that answers it. Phase 3 measures dense only; Phase 5 adds `--compare`.

Embedded Qdrant locks its folder, so stop the API (`make run`) before running this.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdrant_client import QdrantClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.retrieval.dense import DenseRetriever  # noqa: E402

# (query, relevant doc_ids). Every label was checked against data/processed/docs.jsonl.
LABELLED: list[tuple[str, set[str]]] = [
    ("What is the retention period for customer logs?",
     {"POL-COMP-001", "FAQ-004", "FAQ-030", "FAQ-046"}),
    ("What are the steps for manual promotion during a Ledger DB failover?", {"SOP-OPS-014"}),
    ("Which refunds need approval from Finance?", {"SOP-SUP-003"}),
    ("How do I reset MFA for a customer who lost their device?",
     {"SOP-SUP-005", "FAQ-001", "FAQ-026", "FAQ-034", "FAQ-049"}),
    ("What was the root cause of the payment latency spike?", {"INC-2025-031"}),
    ("How often are secrets in the Secrets Vault rotated?", {"SOP-OPS-009"}),
    ("Which systems does the Payment Gateway depend on?", {"MAN-ENG-001"}),
    ("How far ahead of expiry must TLS certificates be renewed?", {"SOP-OPS-018", "POL-COMP-005"}),
    ("What happened in INC-2025-012?", {"INC-2025-012"}),
    ("How quickly must we respond to a chargeback notice?", {"SOP-SUP-008"}),
]
CHUNK_K = 20  # chunks fetched per query, collapsed to a doc ranking


def ranked_docs(chunks) -> list[str]:
    out: list[str] = []
    for c in chunks:
        if c.doc_id not in out:
            out.append(c.doc_id)
    return out


def recall_at(docs: list[str], rel: set[str], k: int) -> float:
    return len(rel & set(docs[:k])) / len(rel)


def mrr_at(docs: list[str], rel: set[str], k: int) -> float:
    return next((1 / (i + 1) for i, d in enumerate(docs[:k]) if d in rel), 0.0)


def ndcg_at(docs: list[str], rel: set[str], k: int) -> float:
    dcg = sum(1 / math.log2(i + 2) for i, d in enumerate(docs[:k]) if d in rel)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(rel), k)))
    return dcg / idcg


async def evaluate(retriever) -> tuple[list[dict], dict]:
    rows = []
    for q, rel in LABELLED:
        docs = ranked_docs(await retriever.retrieve(q, None, k=CHUNK_K))
        first = next((i + 1 for i, d in enumerate(docs) if d in rel), None)
        rows.append({"query": q, "relevant": sorted(rel), "top5": docs[:5], "first_rank": first,
                     "recall@5": recall_at(docs, rel, 5), "mrr@10": mrr_at(docs, rel, 10),
                     "ndcg@10": ndcg_at(docs, rel, 10)})
    n = len(rows)
    summary = {m: sum(r[m] for r in rows) / n for m in ("recall@5", "mrr@10", "ndcg@10")}
    return rows, summary


def write_report(rows: list[dict], summary: dict, path: Path) -> None:
    s = get_settings()
    lines = [
        "# Retrieval Baseline (Phase 3, dense only)",
        "",
        f"Generated {date.today().isoformat()} by `scripts/validate_retrieval.py`. "
        f"Embeddings `{s.embed_model}`, Qdrant cosine, {len(rows)} hand-labelled queries.",
        "Relevance is judged per document: retrieved chunks (top 20) are collapsed to doc ids in rank order.",
        "",
        "| Config | Recall@5 | MRR@10 | nDCG@10 |",
        "|---|---|---|---|",
        f"| Dense only | {summary['recall@5']:.3f} | {summary['mrr@10']:.3f} | {summary['ndcg@10']:.3f} |",
        "",
        "## Per query",
        "",
        "| # | Query | Relevant | First relevant rank | Recall@5 | Top 5 docs |",
        "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['query']} | {', '.join(r['relevant'])} | {r['first_rank'] or '>20 chunks'} "
                     f"| {r['recall@5']:.2f} | {', '.join(r['top5'])} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "eval/reports/retrieval_baseline.md")
    args = ap.parse_args()
    try:
        client = QdrantClient(path=str(ROOT / get_settings().qdrant_path))
    except RuntimeError as e:
        print(f"cannot open Qdrant ({e}). Stop the API first: embedded Qdrant allows one process.")
        return 1
    try:
        rows, summary = asyncio.run(evaluate(DenseRetriever(client)))
    finally:
        client.close()

    for i, r in enumerate(rows, 1):
        print(f"{i:>2}. R@5={r['recall@5']:.2f} first={r['first_rank']!s:>4}  {r['query']}")
    print("dense only: " + "  ".join(f"{k}={v:.3f}" for k, v in summary.items()))
    write_report(rows, summary, args.out)
    print(f"report -> {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
