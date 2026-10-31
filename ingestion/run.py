"""Ingestion orchestrator (§B5.2): raw -> clean -> metadata -> dedupe -> docs.jsonl, plus CSV -> SQLite.

Chunking/indexing arrive in Phase 3 and the graph load in Phase 4; their flags are accepted now so the
Dockerfile command (`python -m ingestion.run --build-indexes --skip-graph`) stays stable.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

from app.config import get_settings
from ingestion.clean import clean_doc
from ingestion.load_sql import build_sqlite
from ingestion.loaders import load_raw_dir
from ingestion.metadata import dedupe, load_documents_meta, to_processed

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the ingestion pipeline.")
    ap.add_argument("--build-indexes", action="store_true", help="chunk + Qdrant + BM25 (Phase 3)")
    ap.add_argument("--skip-graph", action="store_true", help="skip the Neo4j load (Phase 4)")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    docs = process_documents()
    by_type = Counter(d.metadata.doc_type for d in docs)
    print(f"docs.jsonl: {len(docs)} records " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))

    db_path = Path(get_settings().sqlite_path)
    counts = build_sqlite(RAW / "tables", db_path)
    print(f"sqlite {db_path} (+ read-only copy): " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    if args.build_indexes:
        print("indexes: not built yet (Phase 3)")
    if not args.skip_graph:
        print("graph: not loaded yet (Phase 4, `make graph`)")
    print(f"done in {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
