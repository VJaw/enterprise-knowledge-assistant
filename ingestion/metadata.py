"""Document metadata (§B5.3): merge documents_meta.csv > front-matter > filename rules."""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ingestion.loaders import RawDoc


class DocMetadata(BaseModel):
    doc_id: str  # "POL-COMP-007"
    title: str
    doc_type: Literal["policy", "manual", "sop", "incident", "kb", "faq"]
    domain: Literal["engineering", "operations", "compliance", "support"]
    owner: str | None = None  # team or person id -> links to graph
    version: str | None = None
    effective_date: date | None = None
    sensitivity: Literal["public", "internal", "confidential"]
    source_path: str
    page: int | None = None  # PDFs; set per chunk in Phase 3
    section: str | None = None  # heading path; set per chunk in Phase 3


class ProcessedDoc(BaseModel):
    """One line of data/processed/docs.jsonl."""

    metadata: DocMetadata
    text: str
    pages: list[dict] | None = None  # [{"page": 1, "text": ...}] for PDFs, so chunks keep page numbers
    attributes: dict = {}  # front-matter extras: systems, severity, incident_date, resolved_by, category
    sha256: str


DOC_ID = re.compile(r"^((?:POL|MAN|SOP|INC)-[A-Z0-9]+-\d{3})")
PREFIX_DOMAIN = {"POL-COMP": "compliance", "MAN-ENG": "engineering", "SOP-OPS": "operations",
                 "SOP-SUP": "support", "INC-": "engineering", "FAQ-": "support"}
META_FIELDS = set(DocMetadata.model_fields)


def load_documents_meta(path: Path) -> dict[str, dict]:
    """documents_meta.csv rows keyed by doc_id, renamed to DocMetadata fields."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        r = {k: v for k, v in r.items() if v not in ("", None)}
        if "owner_team_id" in r:
            r["owner"] = r.pop("owner_team_id")
        out[r["doc_id"]] = r
    return out


def filename_rules(doc: RawDoc) -> dict:
    """Fallback metadata derived from the path: data/raw/<type dir>/<DOC-ID>_<slug>.<ext>."""
    stem = Path(doc.source_path).stem
    rules: dict = {}
    if m := DOC_ID.match(stem):
        rules["doc_id"] = m.group(1)
        rules["title"] = stem[len(m.group(1)):].strip("_-").replace("-", " ").capitalize() or m.group(1)
        for prefix, domain in PREFIX_DOMAIN.items():
            if m.group(1).startswith(prefix):
                rules["domain"] = domain
                break
    if doc.type_hint:
        rules["doc_type"] = doc.type_hint
    rules["sensitivity"] = "internal"
    return rules


def build_metadata(doc: RawDoc, documents_meta: dict[str, dict]) -> tuple[DocMetadata, dict]:
    """Return (metadata, front-matter extras). Precedence: documents_meta > front-matter > filename."""
    fm = {k: v for k, v in doc.front_matter.items() if v not in ("", None)}
    merged = {**filename_rules(doc), **{k: v for k, v in fm.items() if k in META_FIELDS}}
    doc_id = merged.get("doc_id")
    if doc_id in documents_meta:
        merged.update(documents_meta[doc_id])
    merged["source_path"] = doc.source_path
    merged = {k: (str(v) if k == "version" else v) for k, v in merged.items() if k in META_FIELDS}
    attributes = {k: v for k, v in fm.items() if k not in META_FIELDS}
    return DocMetadata(**merged), attributes


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_processed(doc: RawDoc, documents_meta: dict[str, dict]) -> ProcessedDoc:
    meta, attributes = build_metadata(doc, documents_meta)
    pages = [{"page": p.page, "text": p.text} for p in doc.pages] if doc.pages is not None else None
    return ProcessedDoc(metadata=meta, text=doc.text, pages=pages, attributes=attributes,
                        sha256=content_hash(doc.text))


def dedupe(docs: list[ProcessedDoc]) -> tuple[list[ProcessedDoc], list[tuple[str, str]]]:
    """Keep the first doc per sha256 of normalised text. Returns (kept, [(dropped_id, kept_id)])."""
    seen: dict[str, str] = {}
    kept, dropped = [], []
    for d in docs:
        if d.sha256 in seen:
            dropped.append((d.metadata.doc_id, seen[d.sha256]))
            continue
        seen[d.sha256] = d.metadata.doc_id
        kept.append(d)
    return kept, dropped

