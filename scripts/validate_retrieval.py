"""Retrieval validation (§B6.4): 20 hand-labelled queries -> Recall@5, MRR@10 and nDCG@10.

Relevance is judged at document level: the retrieved chunks are collapsed to their doc_ids in rank order, and
a query's relevant set lists every doc that answers it.

    python scripts/validate_retrieval.py            # dense only -> eval/reports/retrieval_baseline.md
    python scripts/validate_retrieval.py --compare  # all configs -> eval/reports/retrieval_comparison.md

`--compare` routes each query once with gpt-4o-mini (§B8.1, ~20 calls, < $0.01) and gives every config the
same plan: the original query as `rewritten_query`, the router's entities, and no router filters, so the
configs differ only in their retrievers. It needs Neo4j Aura for the graph configs and also times the full
hybrid + rerank pipeline (`--repeat` passes after one warm-up pass) for the p95 latency gate.

Embedded Qdrant locks its folder, so stop the API (`make run`) before running this.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdrant_client import QdrantClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.retrieval.dense import DenseRetriever  # noqa: E402

# (query, relevant doc_ids). Every label was checked against data/processed/docs.jsonl and, for the graph
# questions (#13-#16, #19), against data/raw/tables/{incident,policy}_systems.csv.
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
    # Phase 5 additions: exact commands, ID lookups, paraphrases and graph-dependent questions.
    ("When should I run ledgerctl promote --replica ledger-db-replica-1?", {"SOP-OPS-014"}),
    ("What does vaultctl rotate do and when is it used?", {"SOP-OPS-009"}),
    ("Which incidents affected the Ledger DB?", {"INC-2025-012", "INC-2025-031"}),
    ("Which incidents involved the Kafka Cluster?", {"INC-2025-008", "INC-2025-036", "INC-2025-044"}),
    ("Which policies govern the Secrets Vault?", {"POL-COMP-004", "POL-COMP-005"}),
    ("Which teams own the systems Payment Gateway depends on?", {"MAN-ENG-001"}),
    ("What evidence should be gathered under SOP-SUP-008?", {"SOP-SUP-008"}),
    ("What follow-up actions came out of INC-2025-044?", {"INC-2025-044"}),
    ("If the primary ledger database dies and automatic failover doesn't kick in, how do we bring a replica "
     "online by hand?", {"SOP-OPS-014", "INC-2025-012"}),
    ("Which policies apply to the Billing Service?", {"POL-COMP-006", "POL-COMP-008"}),
]
CHUNK_K = 20  # chunks fetched per query by the single-retriever configs, collapsed to a doc ranking
CONFIGS = [  # (label, use_bm25, use_graph, use_rerank); "dense"/"bm25" alone are run directly
    ("Dense only", None, None, None),
    ("BM25 only", None, None, None),
    ("Dense + BM25 (RRF)", True, False, False),
    ("Dense + BM25 + graph (RRF)", True, True, False),
    ("Dense + BM25 + rerank", True, False, True),
    ("Hybrid + rerank (full)", True, True, True),
]


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


def score_rows(results: list[list[str]]) -> tuple[list[dict], dict]:
    rows = []
    for (q, rel), docs in zip(LABELLED, results, strict=True):
        first = next((i + 1 for i, d in enumerate(docs) if d in rel), None)
        rows.append({"query": q, "relevant": sorted(rel), "top5": docs[:5], "first_rank": first,
                     "recall@5": recall_at(docs, rel, 5), "mrr@10": mrr_at(docs, rel, 10),
                     "ndcg@10": ndcg_at(docs, rel, 10)})
    n = len(rows)
    return rows, {m: sum(r[m] for r in rows) / n for m in ("recall@5", "mrr@10", "ndcg@10")}


async def evaluate(retriever) -> tuple[list[dict], dict]:
    return score_rows([ranked_docs(await retriever.retrieve(q, None, k=CHUNK_K)) for q, _ in LABELLED])


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, math.ceil(p * len(xs)) - 1)]


async def compare(client: QdrantClient, repeat: int) -> dict:
    from langchain_openai import ChatOpenAI

    from app.graph.neo4j_client import Neo4jClient
    from app.observability.langfuse import init_langfuse
    from app.retrieval.bm25 import BM25Retriever
    from app.retrieval.graph import GraphRetriever
    from app.retrieval.hybrid import HybridRetriever
    from app.retrieval.rerank import CrossEncoderReranker, get_cross_encoder
    from app.retrieval.router import Router, load_known_entities

    s = get_settings()
    lf = init_langfuse(s)
    secret = s.openai_api_key.get_secret_value() if s.openai_api_key else None
    router = Router(ChatOpenAI(model=s.model_fast, temperature=0, api_key=secret, timeout=30, max_retries=2),
                    load_known_entities())
    graph_client = Neo4jClient(s.neo4j_uri, s.neo4j_username,
                               s.neo4j_password.get_secret_value() if s.neo4j_password else None)
    graph_ok = await graph_client.connect()
    get_cross_encoder()
    dense, bm25 = DenseRetriever(client), BM25Retriever()
    graph, reranker = GraphRetriever(graph_client, dense), CrossEncoderReranker()

    plans = []
    for q, _ in LABELLED:
        p = await router.plan(q)
        plans.append(p.model_copy(update={"rewritten_query": q, "filters": {}}))

    out: dict = {"configs": {}, "plans": plans, "graph_ok": graph_ok, "facts": {}}
    for label, use_bm25, use_graph, use_rerank in CONFIGS:
        if label == "Dense only":
            out["configs"][label] = await evaluate(dense)
            continue
        if label == "BM25 only":
            out["configs"][label] = await evaluate(bm25)
            continue
        h = HybridRetriever(dense, bm25, graph, reranker, use_bm25=use_bm25, use_graph=use_graph,
                            use_rerank=use_rerank, graph_available=lambda: graph_ok)
        k = 6 if use_rerank else CHUNK_K * 3  # rerank keeps the pipeline's top 6; RRF lists are ranked whole
        results = []
        for i, p in enumerate(plans):
            r = await h.search(p, None, k=k)
            results.append(ranked_docs(r.chunks))
            if label == "Hybrid + rerank (full)":
                out["facts"][i] = [f.text for f in r.facts]
        out["configs"][label] = score_rows(results)

    full = HybridRetriever(dense, bm25, graph, reranker, graph_available=lambda: graph_ok)
    stages = ("dense", "bm25", "graph", "rerank_wait")  # rerank_wait: re-ranking time not hidden behind graph
    lat: dict[str, list[float]] = {"total": [], **{k: [] for k in stages}}
    for rnd in range(repeat + 1):
        for p in plans:
            t0 = time.perf_counter()
            r = await full.search(p, None)
            if rnd == 0:
                continue  # warm-up pass
            lat["total"].append((time.perf_counter() - t0) * 1000)
            for key in stages:
                lat[key].append(r.timings_ms.get(key, 0))
    out["latency"] = {k: {"p50": statistics.median(v), "p95": pct(v, 0.95), "max": max(v)}
                      for k, v in lat.items()}
    out["latency_n"] = len(lat["total"])

    # USE_GRAPH=false degradation check: the same pipeline without the graph still returns chunks.
    no_graph = HybridRetriever(dense, bm25, graph, reranker, graph_available=lambda: False)
    r = await no_graph.search(plans[15], None)
    out["no_graph"] = {"flags": r.flags, "chunks": len(r.chunks), "facts": len(r.facts)}

    await graph_client.close()
    lf.flush()
    return out


def write_baseline(rows: list[dict], summary: dict, path: Path) -> None:
    s = get_settings()
    lines = [
        "# Retrieval Baseline (dense only)",
        "",
        f"Generated {date.today().isoformat()} by `scripts/validate_retrieval.py`. "
        f"Embeddings `{s.embed_model}`, Qdrant cosine, {len(rows)} hand-labelled queries.",
        "Relevance is judged per document: retrieved chunks (top 20) are collapsed to doc ids in rank order.",
        "",
        "| Config | Recall@5 | MRR@10 | nDCG@10 |",
        "|---|---|---|---|",
        f"| Dense only | {summary['recall@5']:.3f} | {summary['mrr@10']:.3f} | {summary['ndcg@10']:.3f} |",
        "",
        *per_query_table(rows),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def per_query_table(rows: list[dict]) -> list[str]:
    lines = ["## Per query", "", "| # | Query | Relevant | First relevant rank | Recall@5 | Top 5 docs |",
             "|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['query']} | {', '.join(r['relevant'])} | {r['first_rank'] or '—'} "
                     f"| {r['recall@5']:.2f} | {', '.join(r['top5'])} |")
    return lines


def write_comparison(res: dict, path: Path) -> None:
    s = get_settings()
    cfg = res["configs"]
    lat = res["latency"]
    lines = [
        "# Retrieval Comparison (Phase 5)",
        "",
        f"Generated {date.today().isoformat()} by `scripts/validate_retrieval.py --compare`. "
        f"{len(LABELLED)} hand-labelled queries; embeddings `{s.embed_model}`, re-ranker `{s.rerank_model}`, "
        f"router `{s.model_fast}`. Neo4j Aura reachable: {res['graph_ok']}.",
        "",
        "Relevance is judged per document (retrieved chunks collapsed to doc ids in rank order). Single "
        "retrievers and RRF configs are ranked over their whole candidate list; rerank configs are scored "
        "on exactly what the pipeline returns (top 6 chunks with cross-encoder score ≥ 0.1). Every config "
        "gets the same router plan (original query, router entities, no router filters).",
        "",
        "| Config | Recall@5 | MRR@10 | nDCG@10 |",
        "|---|---|---|---|",
    ]
    for label, (_, m) in cfg.items():
        lines.append(f"| {label} | {m['recall@5']:.3f} | {m['mrr@10']:.3f} | {m['ndcg@10']:.3f} |")
    lines += [
        "",
        f"## Latency (hybrid + rerank, {res['latency_n']} searches after a warm-up pass, local CPU)",
        "",
        "Router (gpt-4o-mini) time is not included: it is a separate generation span before retrieval.",
        "",
        "| Stage | p50 ms | p95 ms | max ms |",
        "|---|---|---|---|",
    ]
    for k, v in lat.items():
        lines.append(f"| {k} | {v['p50']:.0f} | {v['p95']:.0f} | {v['max']:.0f} |")
    ng = res["no_graph"]
    lines += ["", f"Graph unavailable check (query #16 with the graph switched off): {ng['chunks']} chunks, "
                  f"{ng['facts']} facts, flags {ng['flags']}.", ""]
    lines += ["## Router plans and graph facts (full pipeline)", "",
              "| # | Intent | Entities | Graph facts |", "|---|---|---|---|"]
    for i, p in enumerate(res["plans"]):
        facts = "<br>".join(res["facts"].get(i, [])) or "—"
        lines.append(f"| {i + 1} | {p.intent} | {', '.join(p.entities) or '—'} | {facts} |")
    full_rows = cfg["Hybrid + rerank (full)"][0]
    dense_rows = cfg["Dense only"][0]
    lines += ["", "## Per query: dense only vs hybrid + rerank", "",
              "| # | Query | Relevant | Dense R@5 / first | Hybrid+rerank R@5 / first "
              "| Hybrid+rerank top docs |",
              "|---|---|---|---|---|---|"]
    for i, (d, h) in enumerate(zip(dense_rows, full_rows, strict=True), 1):
        lines.append(f"| {i} | {d['query']} | {', '.join(d['relevant'])} | {d['recall@5']:.2f} / "
                     f"{d['first_rank'] or '—'} | {h['recall@5']:.2f} / {h['first_rank'] or '—'} "
                     f"| {', '.join(h['top5'])} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--compare", action="store_true", help="dense vs BM25 vs hybrid vs hybrid + rerank")
    ap.add_argument("--repeat", type=int, default=3, help="timed passes over the queries for latency")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    try:
        client = QdrantClient(path=str(ROOT / get_settings().qdrant_path))
    except RuntimeError as e:
        print(f"cannot open Qdrant ({e}). Stop the API first: embedded Qdrant allows one process.")
        return 1
    try:
        if args.compare:
            res = asyncio.run(compare(client, args.repeat))
        else:
            rows, summary = asyncio.run(evaluate(DenseRetriever(client)))
    finally:
        client.close()

    if not args.compare:
        for i, r in enumerate(rows, 1):
            print(f"{i:>2}. R@5={r['recall@5']:.2f} first={r['first_rank']!s:>4}  {r['query']}")
        print("dense only: " + "  ".join(f"{k}={v:.3f}" for k, v in summary.items()))
        out = args.out or ROOT / "eval/reports/retrieval_baseline.md"
        write_baseline(rows, summary, out)
    else:
        print(f"{'config':<30} recall@5  mrr@10  ndcg@10")
        for label, (_, m) in res["configs"].items():
            print(f"{label:<30} {m['recall@5']:.3f}     {m['mrr@10']:.3f}   {m['ndcg@10']:.3f}")
        full = res["configs"]["Hybrid + rerank (full)"][0]
        dense = res["configs"]["Dense only"][0]
        for i, (d, h) in enumerate(zip(dense, full, strict=True), 1):
            mark = "" if h["recall@5"] == d["recall@5"] and h["first_rank"] == d["first_rank"] else "  <-"
            print(f"{i:>2}. dense R@5={d['recall@5']:.2f} first={d['first_rank']!s:>4} | hybrid+rr "
                  f"R@5={h['recall@5']:.2f} first={h['first_rank']!s:>4}  {h['query'][:60]}{mark}")
        lat = res["latency"]
        print("latency ms (hybrid+rerank): " + "  ".join(
            f"{k} p50={v['p50']:.0f}/p95={v['p95']:.0f}" for k, v in lat.items()))
        print(f"graph off: {res['no_graph']}")
        out = args.out or ROOT / "eval/reports/retrieval_comparison.md"
        write_comparison(res, out)
    print(f"report -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
