"""Loaders: PDF page numbers survive cleaning; Markdown front-matter is parsed; FAQ items split."""
import json

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from ingestion.clean import clean_doc
from ingestion.loaders import load_faq, load_markdown, load_pdf


def _make_pdf(path, n_pages=3):
    c = canvas.Canvas(str(path), pagesize=LETTER)
    for i in range(1, n_pages + 1):
        c.drawString(72, 750, "ACME CORP | POL-COMP-009 | Test Policy | v1.0")
        for n in range(6):
            c.drawString(72, 700 - 20 * n, f"Section {i}.{n} body text.")
        c.drawString(72, 40, "INTERNAL - Uncontrolled when printed")
        c.drawString(500, 40, f"Page {i}")
        c.showPage()
    c.save()


def test_pdf_page_numbers_kept(tmp_path):
    pdf = tmp_path / "policies" / "POL-COMP-009_test-policy.pdf"
    pdf.parent.mkdir()
    _make_pdf(pdf)
    doc = clean_doc(load_pdf(pdf, tmp_path))
    assert doc.fmt == "pdf" and doc.type_hint == "policy"
    assert doc.source_path == "policies/POL-COMP-009_test-policy.pdf"
    assert [p.page for p in doc.pages] == [1, 2, 3]
    for p in doc.pages:
        assert all(f"Section {p.page}.{n} body text." in p.text for n in range(6))
        assert "ACME CORP" not in p.text and "Uncontrolled" not in p.text


def test_markdown_front_matter_parsed(tmp_path):
    md = tmp_path / "sops" / "SOP-OPS-099_test.md"
    md.parent.mkdir()
    md.write_text('---\ndoc_id: "SOP-OPS-099"\ntitle: "Test SOP"\nversion: "1.2"\n'
                  'systems: ["Ledger DB"]\n---\n\n# Test SOP\n\n## 1. Purpose\nDo the thing.\n')
    doc = load_markdown(md, tmp_path)
    assert doc.front_matter["doc_id"] == "SOP-OPS-099"
    assert doc.front_matter["version"] == "1.2"
    assert doc.front_matter["systems"] == ["Ledger DB"]
    assert doc.type_hint == "sop"
    assert not doc.text.startswith("---") and "## 1. Purpose" in doc.text


def test_faq_items_become_separate_docs(tmp_path):
    path = tmp_path / "faq.json"
    path.write_text(json.dumps({"owner": "TEAM-05", "sensitivity": "public", "items": [
        {"faq_id": "FAQ-001", "category": "Billing", "question": "What is the refund window?",
         "answer": "30 days."},
        {"faq_id": "FAQ-002", "category": "Billing", "question": "Q2?", "answer": "A2."}]}))
    docs = load_faq(path, tmp_path)
    assert [d.front_matter["doc_id"] for d in docs] == ["FAQ-001", "FAQ-002"]
    assert docs[0].text == "Q: What is the refund window?\nA: 30 days."
    assert docs[0].front_matter["owner"] == "TEAM-05" and docs[0].front_matter["doc_type"] == "faq"
