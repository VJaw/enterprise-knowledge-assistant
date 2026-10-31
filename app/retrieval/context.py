"""Context assembly and citations (§B8.5).

Every item the model can cite carries a citation id: `[n]` document passages, `[Gn]` graph facts, `[SQLn]`
query results and `[WEBn]` external web results. Ids are assigned once per turn by `label()` (initial
retrieval first, then each tool result in call order) and stored in `metadata["cite_id"]`, so a passage that
comes back from a second search keeps the id it already has."""
from __future__ import annotations

import re

from app.api.schemas import Citation
from app.core.types import RetrievedChunk

ID = r"(?:G|SQL|WEB)?\d+"
CITE = re.compile(rf"\[({ID}(?:\s*,\s*{ID})*)\]")
PREFIX = {"document": "", "graph": "G", "sql": "SQL", "web": "WEB"}


def _key(c: RetrievedChunk) -> str:
    # Graph fact chunk ids are per-query counters ("graph:1"), so facts are deduped by their sentence.
    return f"{c.source_type}:{c.text if c.source_type == 'graph' else c.chunk_id}"


def label(contexts: list[RetrievedChunk], new: list[RetrievedChunk]
          ) -> tuple[list[RetrievedChunk], list[RetrievedChunk]]:
    """Give each new item the next id of its kind. Returns (all contexts, the new items with their ids).

    An item already in `contexts` keeps its id and isn't added twice; inputs are copied, never mutated."""
    known = {_key(c): c for c in contexts}
    counts = {kind: sum(c.source_type == kind for c in contexts) for kind in PREFIX}
    out, labelled = list(contexts), []
    for c in new:
        if (k := _key(c)) in known:
            labelled.append(known[k])
            continue
        counts[c.source_type] += 1
        c = c.model_copy(update={"metadata": {**c.metadata,
                                              "cite_id": f"{PREFIX[c.source_type]}{counts[c.source_type]}"}})
        known[k] = c
        out.append(c)
        labelled.append(c)
    return out, labelled


def format_item(c: RetrievedChunk) -> str:
    m, cid = c.metadata, c.metadata.get("cite_id", "?")
    if c.source_type == "graph":
        return f"[{cid}] {c.text}  (graph: {m.get('source')})"
    if c.source_type == "sql":
        return f"[{cid}] (sql: {m.get('query')})\n{c.text}"
    if c.source_type == "web":
        return f'[{cid}] (external web result: "{m.get("title")}", {m.get("url")})\n{c.text}'
    parts = [f'{c.doc_id} "{m.get("title")}"']
    if m.get("section"):
        parts.append(f'§ "{m["section"]}"')
    if m.get("page"):
        parts.append(f"p.{m['page']}")
    if m.get("domain") or m.get("doc_type"):
        parts.append(f"{m.get('domain')}/{m.get('doc_type')}")
    if m.get("version"):
        parts.append(f"v{m['version']}")
    return f"[{cid}] ({', '.join(parts)})\n{c.text}"


def format_items(items: list[RetrievedChunk]) -> str:
    """Documents as blocks, then consecutive graph facts as one block of lines."""
    blocks: list[str] = []
    facts: list[str] = []
    for c in items:
        if c.source_type == "graph":
            facts.append(format_item(c))
            continue
        blocks.append(format_item(c))
    if facts:
        blocks.append("\n".join(facts))
    return "\n\n".join(blocks)


def format_context(chunks: list[RetrievedChunk], facts: list[RetrievedChunk] | None = None) -> str:
    """[n] (DOC-ID "title", § "section", p.N, domain/doc_type, vX) text  ...  [Gn] fact  (graph: source)."""
    return format_items(label([], [*chunks, *(facts or [])])[0])


def cited_ids(answer: str) -> list[str]:
    """Citation ids ("1", "G2", "SQL1", "WEB3") in order of first appearance."""
    seen: list[str] = []
    for m in CITE.finditer(answer):
        for n in m.group(1).split(","):
            if (n := n.strip()) not in seen:
                seen.append(n)
    return seen


def citation(c: RetrievedChunk, cid: str) -> Citation:
    m = c.metadata
    if c.source_type == "graph":
        return Citation(id=cid, source_type="graph", title=f"graph: {m.get('source')}", snippet=c.text)
    if c.source_type == "sql":
        return Citation(id=cid, source_type="sql", title=f"sql: {m.get('query')}", snippet=c.text)
    if c.source_type == "web":
        return Citation(id=cid, source_type="web", title=m.get("title"), url=m.get("url"), snippet=c.text)
    # Full chunk text as the snippet: RAGAS (Phase 6) uses snippets as retrieved_contexts (D-012).
    return Citation(id=cid, source_type=c.source_type, doc_id=c.doc_id, title=m.get("title"),
                    section=m.get("section"), page=m.get("page"), snippet=c.text)


def citations_for(answer: str, contexts: list[RetrievedChunk]) -> list[Citation]:
    """Citations for the ids the answer uses, in order of first use. Ids that don't exist are skipped
    (Phase 8's citation rail handles them)."""
    by_id = {c.metadata["cite_id"]: c for c in contexts if "cite_id" in c.metadata}
    return [citation(by_id[cid], cid) for cid in cited_ids(answer) if cid in by_id]


def build_citations(answer: str, chunks: list[RetrievedChunk],
                    facts: list[RetrievedChunk] | None = None) -> list[Citation]:
    return citations_for(answer, label([], [*chunks, *(facts or [])])[0])
