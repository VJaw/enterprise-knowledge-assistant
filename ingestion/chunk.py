"""Chunking strategies per doc type (§B6.1).

- Markdown SOPs / manuals / incidents: header-aware split on `#`/`##`/`###`, then recursive if too long.
  Incident reports have exactly the four `##` sections, so this yields one chunk per section.
- PDF policies: per page, soft line wraps joined, then recursive split; `page` and `section` kept.
- FAQ: one chunk per Q/A pair.

Lengths are counted with the embedding model's own tokenizer, so every chunk fits bge's 512-token window
together with the title line that `index_text` prepends.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from pydantic import BaseModel, Field

from ingestion.metadata import DocMetadata, ProcessedDoc

MAX_TOKENS = 480  # ≤ 512 (§B6.1), leaving room for the index_text header line
OVERLAP_TOKENS = 64
SEPARATORS = ["\n\n", "\n", ". ", " "]

TokenCounter = Callable[[str], int]


class Chunk(BaseModel):
    chunk_id: str  # "<doc_id>::<chunk_index:03d>"
    doc_id: str
    chunk_index: int
    text: str
    heading_path: str | None = None
    metadata: DocMetadata  # parent metadata with page/section set for this chunk
    entities: list[str] = Field(default_factory=list)  # linked in Phase 4 (vector-to-graph bridging)


def default_token_counter() -> TokenCounter:
    from app.retrieval.embeddings import get_tokenizer

    tok = get_tokenizer()
    return lambda s: len(tok.encode(s, add_special_tokens=False))


# ---------------------------------------------------------------- recursive split
def split_recursive(text: str, count: TokenCounter, max_tokens: int = MAX_TOKENS,
                    overlap: int = OVERLAP_TOKENS, separators: list[str] = SEPARATORS) -> list[str]:
    """Split on the coarsest separator that works, merge pieces up to `max_tokens` with `overlap`."""
    text = text.strip()
    if not text:
        return []
    if count(text) <= max_tokens:
        return [text]
    seps = [s for s in separators if s in text]
    if not seps:  # one enormous token run: hard split by characters
        step = max(1, len(text) * max_tokens // count(text))
        return [text[i:i + step] for i in range(0, len(text), step)]
    sep, finer = seps[0], separators[separators.index(seps[0]) + 1:]

    parts = text.split(sep)
    units: list[str] = []
    for i, p in enumerate(parts):
        p = p + sep if i < len(parts) - 1 else p
        if count(p) > max_tokens:
            units.extend(u + " " for u in split_recursive(p, count, max_tokens, overlap, finer))
        elif p.strip():
            units.append(p)

    chunks: list[str] = []
    cur: list[str] = []
    for u in units:
        if cur and count("".join(cur + [u])) > max_tokens:
            chunks.append("".join(cur).strip())
            tail: list[str] = []  # carry trailing units worth ≤ overlap tokens into the next chunk
            for v in reversed(cur):
                if count("".join([v] + tail)) > overlap:
                    break
                tail.insert(0, v)
            cur = tail
            while cur and count("".join(cur + [u])) > max_tokens:
                cur.pop(0)
        cur.append(u)
    if cur:
        chunks.append("".join(cur).strip())
    return [c for c in chunks if c]


# ---------------------------------------------------------------- markdown
MD_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def markdown_sections(text: str) -> list[tuple[str | None, str]]:
    """[(heading_path, section_text)]. The H1 is the doc title, so it is left out of the path.

    Headings inside ``` fences are ignored; sections with no body beyond their heading are skipped.
    """
    sections: list[tuple[str | None, str]] = []
    path: list[tuple[int, str]] = []
    lines: list[str] = []
    in_fence = False

    def flush():
        body = [ln for ln in lines if not MD_HEADING.match(ln)]
        if "\n".join(body).strip():
            hp = " > ".join(t for lvl, t in path if lvl > 1) or None
            sections.append((hp, "\n".join(lines).strip()))

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else MD_HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, m.group(2).strip()))
            lines = [line]
        else:
            lines.append(line)
    flush()
    return sections


def chunk_markdown(doc: ProcessedDoc, count: TokenCounter) -> list[tuple[str, str | None, int | None]]:
    out = []
    for heading_path, section in markdown_sections(doc.text):
        for piece in split_recursive(section, count):
            out.append((piece, heading_path, None))
    return out


# ---------------------------------------------------------------- PDF
PDF_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z][^.:]{0,70})$")
BULLET = re.compile(r"^(?:[-*•]|\d+[.)])\s+")


def _pdf_heading(line: str) -> tuple[int, str] | None:
    m = PDF_HEADING.match(line.strip())
    if not m or len(m.group(2).split()) > 9:
        return None
    return m.group(1).count(".") + 1, line.strip()  # "3. Scope" -> 1, "3.1 Periods" -> 2


def join_soft_wraps(page_text: str) -> str:
    """pypdf keeps the PDF's visual line wraps; join them into paragraphs, keeping headings and bullets."""
    out: list[str] = []
    for raw in page_text.split("\n"):
        line = raw.strip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        starts_block = bool(_pdf_heading(line) or BULLET.match(line))
        prev = out[-1] if out else ""
        if prev and not starts_block and not _pdf_heading(prev):
            out[-1] = prev + " " + line
        else:
            out.append(line)
    return "\n".join(out).strip()


def chunk_pdf(doc: ProcessedDoc, count: TokenCounter) -> list[tuple[str, str | None, int | None]]:
    out = []
    carried: list[tuple[int, str]] = []  # heading stack in force at the top of the page
    for page in doc.pages or []:
        text = join_soft_wraps(page["text"])
        marks: list[tuple[int, list]] = []  # (char offset of a heading line, stack after it)
        stack, pos = list(carried), 0
        for ln in text.split("\n"):
            if h := _pdf_heading(ln):
                while stack and stack[-1][0] >= h[0]:
                    stack.pop()
                stack.append(h)
                marks.append((pos, list(stack)))
            pos += len(ln) + 1

        search_from = 0
        for piece in split_recursive(text, count):
            at = text.find(piece[:60], search_from)
            at = search_from if at < 0 else at
            search_from = at + 1
            st = carried
            for o, s in marks:  # latest heading at or before the chunk start
                if o > at:
                    break
                st = s
            if not st:  # chunk starts before the first heading: use the first heading inside it
                st = next((s for o, s in marks if at <= o < at + len(piece)), [])
            out.append((piece, " > ".join(t for _, t in st) or None, page["page"]))
        carried = stack
    return out


# ---------------------------------------------------------------- dispatch
def chunk_document(doc: ProcessedDoc, count: TokenCounter) -> list[Chunk]:
    m = doc.metadata
    if m.doc_type == "faq":
        category = doc.attributes.get("category")
        pieces = [(doc.text.strip(), category, None)]
    elif doc.pages is not None:
        pieces = chunk_pdf(doc, count)
    else:
        pieces = chunk_markdown(doc, count)

    chunks = []
    for i, (text, heading_path, page) in enumerate(pieces):
        meta = m.model_copy(update={"page": page, "section": heading_path})
        chunks.append(Chunk(chunk_id=f"{m.doc_id}::{i:03d}", doc_id=m.doc_id, chunk_index=i, text=text,
                            heading_path=heading_path, metadata=meta))
    return chunks


def chunk_documents(docs: Iterable[ProcessedDoc], count: TokenCounter | None = None) -> list[Chunk]:
    count = count or default_token_counter()
    return [c for d in docs for c in chunk_document(d, count)]


def index_text(chunk: Chunk) -> str:
    """What gets embedded and BM25-indexed: a title/heading line for context, then the chunk text."""
    m = chunk.metadata
    head = f"{m.title} ({m.doc_id})" + (f" | {chunk.heading_path}" if chunk.heading_path else "")
    return f"{head}\n{chunk.text}"
