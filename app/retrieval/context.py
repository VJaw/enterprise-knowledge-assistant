"""Context assembly and citations (§B8.5): `[n]` document passages, then `[Gn]` graph facts."""
from __future__ import annotations

import re

from app.api.schemas import Citation
from app.core.types import RetrievedChunk

CITE = re.compile(r"\[(G?\d+(?:\s*,\s*G?\d+)*)\]")


def format_context(chunks: list[RetrievedChunk], facts: list[RetrievedChunk] | None = None) -> str:
    """[n] (DOC-ID "title", § "section", p.N, domain/doc_type, vX) text  ...  [Gn] fact  (graph: source)."""
    blocks = []
    for i, c in enumerate(chunks, 1):
        m = c.metadata
        parts = [f'{c.doc_id} "{m.get("title")}"']
        if m.get("section"):
            parts.append(f'§ "{m["section"]}"')
        if m.get("page"):
            parts.append(f"p.{m['page']}")
        parts.append(f"{m.get('domain')}/{m.get('doc_type')}")
        if m.get("version"):
            parts.append(f"v{m['version']}")
        blocks.append(f"[{i}] ({', '.join(parts)})\n{c.text}")
    if facts:
        blocks.append("\n".join(f"[G{i}] {f.text}  (graph: {f.metadata.get('source')})"
                                for i, f in enumerate(facts, 1)))
    return "\n\n".join(blocks)


def cited_ids(answer: str) -> list[str]:
    """Citation ids ("1", "G2") in order of first appearance."""
    seen: list[str] = []
    for m in CITE.finditer(answer):
        for n in m.group(1).split(","):
            if (n := n.strip()) not in seen:
                seen.append(n)
    return seen


def build_citations(answer: str, chunks: list[RetrievedChunk],
                    facts: list[RetrievedChunk] | None = None) -> list[Citation]:
    by_id = {str(i): c for i, c in enumerate(chunks, 1)}
    by_id |= {f"G{i}": f for i, f in enumerate(facts or [], 1)}
    out = []
    for cid in cited_ids(answer):
        c = by_id.get(cid)
        if c is None:
            continue  # the model cited an id that doesn't exist; Phase 8's citation rail handles this
        if c.source_type == "graph":
            out.append(Citation(id=cid, source_type="graph", title=f"graph: {c.metadata.get('source')}",
                                snippet=c.text))
            continue
        m = c.metadata
        # Full chunk text as the snippet: RAGAS (Phase 6) uses snippets as retrieved_contexts (D-012).
        out.append(Citation(id=cid, source_type=c.source_type, doc_id=c.doc_id, title=m.get("title"),
                            section=m.get("section"), page=m.get("page"), snippet=c.text))
    return out
