"""Chunking strategies (§B6.1) with a whitespace token counter, so no model is loaded."""
from ingestion.chunk import chunk_document, index_text, join_soft_wraps, markdown_sections, split_recursive
from ingestion.metadata import DocMetadata, ProcessedDoc


def words(s: str) -> int:
    return len(s.split())


def doc(doc_id="SOP-OPS-014", doc_type="sop", text="", pages=None, attributes=None):
    meta = DocMetadata(doc_id=doc_id, title="Database Failover", doc_type=doc_type, domain="operations",
                       sensitivity="internal", source_path=f"data/raw/{doc_id}.md")
    return ProcessedDoc(metadata=meta, text=text, pages=pages, attributes=attributes or {}, sha256="x")


SOP = """# Database Failover

## Purpose

Fail over the Ledger DB.

## Procedure

### 3.1 Automatic Failover

1. Replica is promoted automatically.

### 3.2 Manual Promotion

1. Freeze writes.
2. Run `ledgerctl promote`.

```
# not a heading
```

## References
- POL-COMP-001
"""


def test_markdown_heading_paths_skip_title_and_empty_sections():
    secs = markdown_sections(SOP)
    assert [hp for hp, _ in secs] == ["Purpose", "Procedure > 3.1 Automatic Failover",
                                      "Procedure > 3.2 Manual Promotion", "References"]
    manual = dict(secs)["Procedure > 3.2 Manual Promotion"]
    assert manual.startswith("### 3.2 Manual Promotion") and "# not a heading" in manual


def test_markdown_chunks_carry_metadata_and_ids():
    chunks = chunk_document(doc(text=SOP), words)
    assert [c.chunk_id for c in chunks] == [f"SOP-OPS-014::{i:03d}" for i in range(4)]
    c = chunks[2]
    assert c.metadata.section == c.heading_path == "Procedure > 3.2 Manual Promotion"
    assert c.metadata.page is None and c.entities == []


def test_incident_splits_one_chunk_per_section():
    sections = ["Summary", "Timeline", "RCA", "Actions"]
    text = "# Outage\n\n" + "\n\n".join(f"## {s}\n{s} body text." for s in sections)
    chunks = chunk_document(doc("INC-2025-031", "incident", text), words)
    assert [c.heading_path for c in chunks] == ["Summary", "Timeline", "RCA", "Actions"]


def test_split_recursive_respects_limit_and_overlaps():
    text = "\n\n".join(" ".join(f"p{i}w{j}" for j in range(30)) + "." for i in range(10))
    pieces = split_recursive(text, words, max_tokens=100, overlap=40)
    assert len(pieces) > 1
    assert all(words(p) <= 100 for p in pieces)
    # consecutive chunks share the overlapping paragraph
    assert pieces[0].split("\n\n")[-1] == pieces[1].split("\n\n")[0]


def test_split_recursive_falls_back_to_finer_separators():
    long_para = ". ".join(" ".join(["word"] * 20) for _ in range(20))  # 400 words, no newlines
    pieces = split_recursive(long_para, words, max_tokens=50, overlap=0)
    assert all(words(p) <= 50 for p in pieces)
    assert sum(words(p) for p in pieces) == 400


def test_pdf_soft_wraps_joined_headings_and_bullets_kept():
    page = ("3. Policy Statements\n3.1 Data Retention Periods\n- Customer application logs are retained\n"
            "for 13 months.\n- Audit logs 7 years.")
    assert join_soft_wraps(page).split("\n") == [
        "3. Policy Statements", "3.1 Data Retention Periods",
        "- Customer application logs are retained for 13 months.", "- Audit logs 7 years."]


def test_pdf_chunks_keep_page_numbers_and_carry_sections_across_pages():
    pages = [{"page": 1, "text": "Data Retention Policy\n1. Purpose\nWhy we retain.\n3. Policy Statements\n"
                                 "3.1 Data Retention Periods\nLogs 13 months."},
             {"page": 2, "text": "Continued text about\nretention periods."}]
    d = doc("POL-COMP-001", "policy", "unused", pages=pages)
    chunks = chunk_document(d, words)
    assert [c.metadata.page for c in chunks] == [1, 2]
    assert chunks[0].metadata.section == "1. Purpose"  # starts before the first heading
    assert chunks[1].metadata.section == "3. Policy Statements > 3.1 Data Retention Periods"
    assert chunks[1].text == "Continued text about retention periods."


def test_faq_is_one_chunk_with_category():
    text = "Q: How do I reset MFA?\nA: From the portal."
    d = doc("FAQ-001", "faq", text, attributes={"category": "Account"})
    [c] = chunk_document(d, words)
    assert c.heading_path == "Account" and c.text.startswith("Q: How do I reset MFA?")


def test_index_text_prefixes_title_id_and_heading():
    c = chunk_document(doc(text=SOP), words)[2]
    head = index_text(c).splitlines()[0]
    assert head == "Database Failover (SOP-OPS-014) | Procedure > 3.2 Manual Promotion"
