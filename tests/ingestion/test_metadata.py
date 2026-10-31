"""Metadata merge (documents_meta > front-matter > filename rules), validation and de-duplication."""
from datetime import date

import pytest
from pydantic import ValidationError

from ingestion.loaders import RawDoc
from ingestion.metadata import build_metadata, dedupe, load_documents_meta, to_processed

META = {"SOP-OPS-014": {"doc_id": "SOP-OPS-014", "title": "Database Failover", "doc_type": "sop",
                        "domain": "operations", "owner": "TEAM-04", "version": "2.1",
                        "effective_date": "2025-03-12", "sensitivity": "internal"}}


def _md(front_matter: dict, path="data/raw/sops/SOP-OPS-014_database-failover.md", text="body") -> RawDoc:
    return RawDoc(path, "md", text, front_matter=front_matter, type_hint="sop")


def test_documents_meta_wins_over_front_matter():
    meta, attrs = build_metadata(_md({"doc_id": "SOP-OPS-014", "title": "Old title", "version": "1.0",
                                      "systems": ["Ledger DB"]}), META)
    assert meta.title == "Database Failover"
    assert meta.version == "2.1"
    assert meta.effective_date == date(2025, 3, 12)
    assert meta.owner == "TEAM-04"
    assert meta.source_path == "data/raw/sops/SOP-OPS-014_database-failover.md"
    assert attrs == {"systems": ["Ledger DB"]}


def test_front_matter_used_when_not_in_documents_meta():
    meta, _ = build_metadata(_md({"doc_id": "SOP-OPS-099", "title": "New SOP", "sensitivity": "confidential"},
                                 path="data/raw/sops/SOP-OPS-099_new.md"), META)
    assert (meta.doc_id, meta.title, meta.sensitivity) == ("SOP-OPS-099", "New SOP", "confidential")
    assert (meta.doc_type, meta.domain) == ("sop", "operations")  # filename rules


def test_filename_rules_alone_give_complete_metadata():
    raw = RawDoc("data/raw/policies/POL-COMP-042_some-policy.pdf", "pdf", "x", pages=[], type_hint="policy")
    meta, _ = build_metadata(raw, {})
    assert (meta.doc_id, meta.doc_type, meta.domain, meta.sensitivity) == (
        "POL-COMP-042", "policy", "compliance", "internal")
    assert meta.title == "Some policy"


def test_missing_required_fields_fail_validation():
    with pytest.raises(ValidationError):
        build_metadata(RawDoc("data/raw/misc/notes.md", "md", "x"), {})


def test_load_documents_meta_renames_owner(tmp_path):
    csv = tmp_path / "documents_meta.csv"
    csv.write_text("doc_id,title,doc_type,domain,owner_team_id,version,effective_date,sensitivity\n"
                   "POL-COMP-001,Data Retention Policy,policy,compliance,TEAM-06,3.0,2025-02-01,internal\n")
    rows = load_documents_meta(csv)
    assert rows["POL-COMP-001"]["owner"] == "TEAM-06" and "owner_team_id" not in rows["POL-COMP-001"]


def test_dedupe_by_content_hash():
    a = to_processed(_md({"doc_id": "SOP-OPS-014"}, text="same text"), META)
    b = to_processed(_md({"doc_id": "SOP-OPS-015", "title": "Copy"},
                         path="data/raw/sops/SOP-OPS-015_copy.md", text="same text"), META)
    c = to_processed(_md({"doc_id": "SOP-OPS-016", "title": "Other"},
                         path="data/raw/sops/SOP-OPS-016_other.md", text="different"), META)
    kept, dropped = dedupe([a, b, c])
    assert [d.metadata.doc_id for d in kept] == ["SOP-OPS-014", "SOP-OPS-016"]
    assert dropped == [("SOP-OPS-015", "SOP-OPS-014")]
