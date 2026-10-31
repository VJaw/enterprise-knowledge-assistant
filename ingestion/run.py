"""Ingestion orchestrator (§B5.2): raw -> clean -> metadata -> dedupe -> docs.jsonl, plus CSV -> SQLite,
then (with `--build-indexes`) chunk -> Qdrant + BM25.

Unless `--skip-graph`, the Neo4j graph is (re)loaded from the committed CSVs (no LLM calls; idempotent).
The Docker build passes `--skip-graph` because the graph lives in Aura (§B4.5).
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

from app.config import get_settings
from ingestion.chunk import chunk_documents
from ingestion.clean import clean_doc
from ingestion.index import build_indexes
from ingestion.load_sql import build_sqlite
from ingestion.loaders import load_raw_dir
from ingestion.metadata import ProcessedDoc, dedupe, load_documents_meta, to_processed

RAW = Path("data/raw")
PROCESSED = Path("data/processed")


def process_documents(raw_dir: Path = RAW, out_path: Path = PROCESSED / "docs.jsonl") -> list:
    meta = load_documents_meta(raw_dir / "tables" / "documents_meta.csv")
    docs = [to_processed(clean_doc(d), meta) for d in load_raw_dir(raw_dir)]
    docs.sort(key=lambda d: d.metadata.doc_id)
    kept, dropped = dedupe(docs)
    for dup, original in dropped:
        print(f"  dedupe: dropped {dup} (same content as {original})")

    missing_meta = sorted(set(meta) - {d.metadata.doc_id for d in kept})
    if missing_meta:
        print(f"  warning: documents_meta rows with no source file: {missing_meta}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for d in kept:
            f.write(d.model_dump_json() + "\n")
    return kept


def load_processed(path: Path = PROCESSED / "docs.jsonl") -> list[ProcessedDoc]:
    with open(path) as f:
        return [ProcessedDoc.model_validate_json(line) for line in f if line.strip()]


def index_documents(docs: list[ProcessedDoc], client=None) -> dict:
    """Chunk + build Qdrant/BM25. Pass an open QdrantClient to reuse it (embedded Qdrant locks its folder)."""
    from qdrant_client import QdrantClient

    chunks = chunk_documents(docs)
    owned = client is None
    client = client or QdrantClient(path=get_settings().qdrant_path)
    try:
        stats = build_indexes(chunks, client)
    finally:
        if owned:
            client.close()
    by_type = Counter(c.metadata.doc_type for c in chunks)
    stats["chunks_by_type"] = dict(sorted(by_type.items()))
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the ingestion pipeline.")
    ap.add_argument("--build-indexes", action="store_true", help="chunk + Qdrant + BM25")
    ap.add_argument("--skip-graph", action="store_true", help="skip the Neo4j load")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    docs = process_documents()
    by_type = Counter(d.metadata.doc_type for d in docs)
    print(f"docs.jsonl: {len(docs)} records " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))

    db_path = Path(get_settings().sqlite_path)
    counts = build_sqlite(RAW / "tables", db_path)
    print(f"sqlite {db_path} (+ read-only copy): " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    if args.build_indexes:
        t1 = time.perf_counter()
        stats = index_documents(docs)
        print(f"indexes: {stats['qdrant_points']} chunks in Qdrant + BM25 "
              f"({', '.join(f'{k}={v}' for k, v in stats['chunks_by_type'].items())}) "
              f"in {time.perf_counter() - t1:.1f}s")
    if not args.skip_graph:
        from ingestion.load_graph import load_all
        try:
            nodes, rels = load_all()
            print(f"graph: {nodes} nodes, {rels} relationships in Neo4j")
        except Exception as e:  # noqa: BLE001 - Aura paused/unreachable must not fail the local ingest
            print(f"graph: not loaded ({type(e).__name__}); run `make graph` once Aura is reachable")
    print(f"done in {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
