"""Text cleaning (§B5.2): unicode NFKC, header/footer strip, de-hyphenation, whitespace."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import replace

from ingestion.loaders import Page, RawDoc

PAGE_NUMBER = re.compile(r"^\s*(page\s+)?\d+(\s*(of|/)\s*\d+)?\s*$", re.I)
EDGE_LINES = 3  # header/footer candidates: first and last N non-empty lines of each page
BULLET = re.compile(r"^[ \t]*[\x7f\u2022\u25aa\u25cf\uf0b7][ \t]*", re.M)  # PDF bullet glyphs
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _key(line: str) -> str:
    """Digits collapse on page-label lines, so 'Page 2 of 3' and 'Page 3 of 3' are one footer."""
    key = line.strip().lower()
    return re.sub(r"\d+", "#", key) if re.search(r"\bpage\b", key) else key


def strip_headers_footers(pages: list[Page], min_share: float = 0.6) -> list[Page]:
    """Drop lines repeated at the top/bottom of most pages, plus bare page-number lines."""
    if not pages:
        return pages
    edge_counts: Counter[str] = Counter()
    for p in pages:
        lines = [ln for ln in p.text.splitlines() if ln.strip()]
        edges = {_key(ln) for ln in lines[:EDGE_LINES] + lines[-EDGE_LINES:]}
        edge_counts.update(edges)
    threshold = max(2, math.ceil(min_share * len(pages)))
    repeated = {k for k, c in edge_counts.items() if c >= threshold} if len(pages) > 1 else set()

    out = []
    for p in pages:
        lines = p.text.splitlines()
        nonempty = [i for i, ln in enumerate(lines) if ln.strip()]
        edge_idx = set(nonempty[:EDGE_LINES] + nonempty[-EDGE_LINES:])
        kept = [ln for i, ln in enumerate(lines)
                if not (i in edge_idx and (_key(ln) in repeated or PAGE_NUMBER.match(ln)))]
        out.append(Page(p.page, "\n".join(kept)))
    return out


def dehyphenate(text: str) -> str:
    """Join words split across a line break: 'reten-\\ntion' -> 'retention'."""
    return re.sub(r"([a-z])-\n([a-z])", r"\1\2", text)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = BULLET.sub("- ", text)
    text = CONTROL.sub("", text)
    text = dehyphenate(text)
    text = "\n".join(ln.rstrip() for ln in text.split("\n"))
    text = re.sub(r"(?<=\S)[ \t]{2,}", " ", text)  # keeps leading indentation (nested Markdown lists)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_doc(doc: RawDoc) -> RawDoc:
    if doc.pages is not None:
        pages = [Page(p.page, normalize(p.text)) for p in strip_headers_footers(doc.pages)]
        return replace(doc, pages=pages, text="\n\n".join(p.text for p in pages if p.text))
    return replace(doc, text=normalize(doc.text))
