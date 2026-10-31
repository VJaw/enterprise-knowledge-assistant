"""BM25 tokenisation (§B6.3), shared by the indexer and (from Phase 5) the BM25 retriever.

Lower-cased, stop words removed, and ID-like tokens (`SOP-OPS-014`, `INC-2025-031`, `EMP-22280`) kept whole
so exact ID lookups match.
"""
from __future__ import annotations

import re

ID_TOKEN = re.compile(r"\b[a-z]+(?:-[a-z]+)*-\d[a-z0-9]*(?:-[a-z0-9]+)*\b")
WORD = re.compile(r"[a-z0-9]+")

STOP_WORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being below between
both but by can could did do does doing down during each few for from further had has have having he her
here hers him his how i if in into is it its itself just me more most my no nor not now of off on once only
or other our ours out over own same she should so some such than that the their theirs them then there these
they this those through to too under until up very was we were what when where which while who whom why will
with would you your yours
""".split())


def tokenize(text: str) -> list[str]:
    text = text.lower()
    ids = ID_TOKEN.findall(text)
    rest = ID_TOKEN.sub(" ", text)
    words = [w for w in WORD.findall(rest) if len(w) > 1 and w not in STOP_WORDS]
    return ids + words
