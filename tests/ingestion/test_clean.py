"""Cleaning: header/footer stripping, de-hyphenation, PDF bullet glyphs."""
from ingestion.clean import clean_doc, dehyphenate, normalize, strip_headers_footers
from ingestion.loaders import Page, RawDoc

HEADER = "ACME CORP | POL-COMP-001 | Data Retention Policy | v3.0"
FOOTER = "INTERNAL - Uncontrolled when printed"


WORDS = ["alpha", "bravo", "charlie", "delta"]


def _body(i: int) -> str:
    return "\n".join(f"Body text {WORDS[i - 1]} line {n}." for n in range(1, 8))


def _pages(n: int) -> list[Page]:
    return [Page(i, f"{HEADER}\n{FOOTER}\nPage {i}\n{_body(i)}") for i in range(1, n + 1)]


def test_repeated_header_and_footer_are_stripped():
    out = strip_headers_footers(_pages(3))
    for p in out:
        assert HEADER not in p.text
        assert FOOTER not in p.text
        assert f"Page {p.page}" not in p.text
        assert _body(p.page) in p.text


def test_footer_at_bottom_of_page_is_stripped():
    pages = [Page(i, f"{_body(i)}\n{FOOTER}\nPage {i} of 3") for i in range(1, 4)]
    out = strip_headers_footers(pages)
    assert all(FOOTER not in p.text and "of 3" not in p.text for p in out)
    assert all(p.text == _body(p.page) for p in out)


def test_line_repeated_only_once_is_kept():
    pages = _pages(3)
    pages[0] = Page(1, "Only on page one\n" + pages[0].text)
    out = strip_headers_footers(pages)
    assert "Only on page one" in out[0].text


def test_single_page_keeps_its_first_lines():
    out = strip_headers_footers([Page(1, "Title line\nBody")])
    assert out[0].text == "Title line\nBody"


def test_dehyphenate_joins_words_split_across_lines():
    assert dehyphenate("data reten-\ntion period") == "data retention period"
    assert dehyphenate("SOP-OPS-014") == "SOP-OPS-014"


def test_normalize_bullets_whitespace_and_unicode():
    text = normalize("\x7f first item\n• second item\n\n\n\nﬁnal   line  ")
    assert text == "- first item\n- second item\n\nfinal line"


def test_clean_doc_rebuilds_text_from_pages():
    doc = clean_doc(RawDoc("x.pdf", "pdf", "", pages=_pages(2)))
    assert HEADER not in doc.text
    assert doc.text == _body(1) + "\n\n" + _body(2)
