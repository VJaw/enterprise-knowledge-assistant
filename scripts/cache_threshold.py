"""Check CACHE_SIM_THRESHOLD (§B12.1) on this corpus: cosine similarity of cache keys (normalised questions
embedded as queries, as app/cache/semantic_cache.py does) for paraphrases that should hit and near-miss
questions that must not. No API calls; doesn't open Qdrant, so it can run next to the API.

    PYTHONPATH=. python scripts/cache_threshold.py
"""
from __future__ import annotations

import numpy as np

from app.cache.semantic_cache import normalise, specifics
from app.config import get_settings
from app.retrieval.embeddings import embed_query

# (cached question, new question): same answer
PARAPHRASES = [
    ("What is the retention period for customer logs?", "How long are customer logs retained?"),
    ("What is the retention period for customer logs?", "What's the retention period of customer logs?"),
    ("What is the retention period for customer logs?", "how long do we keep customer application logs"),
    ("Which SOP covers MFA reset?", "Which SOP is about resetting MFA?"),
    ("Which SOP covers MFA reset?", "what sop covers mfa reset"),
    ("Who owns the Ledger DB?", "Which team owns Ledger DB?"),
    ("Who owns the Ledger DB?", "Who is the owner of the Ledger DB?"),
    ("What is the refund window?", "How many days do customers have to request a refund?"),
    ("How do I manually promote the Ledger DB replica?",
     "What are the steps to promote the Ledger DB replica?"),
    ("Which teams own the systems Payment Gateway depends on?",
     "Which teams own the systems the Payment Gateway depends on?"),
]
# (cached question, new question): different answer
NEAR_MISSES = [
    ("What is the retention period for customer logs?", "What is the retention period for audit logs?"),
    ("What is the retention period for customer logs?", "What is the retention period for backups?"),
    ("Who owns the Ledger DB?", "Who owns the Payment Gateway?"),
    ("Who owns the Ledger DB?", "Who manages the team that owns the Ledger DB?"),
    ("Which SOP covers MFA reset?", "Which SOP covers secrets rotation?"),
    ("How many SEV1 tickets did Ledger DB have?", "How many SEV2 tickets did Ledger DB have?"),
    ("How many SEV1 tickets did Ledger DB have?", "How many SEV1 tickets did Payment Gateway have?"),
    ("What is the refund window?", "Who approves refunds over $5,000?"),
    ("What happened in INC-2025-012?", "What happened in INC-2025-017?"),
    ("What depends on Auth Service?", "What does Auth Service depend on?"),
]


def sim(a: str, b: str) -> float:
    return float(np.dot(embed_query(normalise(a)), embed_query(normalise(b))))


def hit(a: str, b: str, t: float) -> tuple[float, bool, bool]:
    """(similarity, hit on the threshold alone, hit with the specifics guard)."""
    s = sim(a, b)
    return s, s >= t, s >= t and specifics(normalise(a)) == specifics(normalise(b))


def main() -> None:
    t = get_settings().cache_sim_threshold
    totals = {}
    for label, pairs, want_hit in (("paraphrases (should hit)", PARAPHRASES, True),
                                   ("near misses (must not hit)", NEAR_MISSES, False)):
        print(f"\n{label}   [sim, threshold only, with guard]")
        rows = [(a, b, *hit(a, b, t)) for a, b in pairs]
        for a, b, s, raw, guarded in rows:
            mark = "ok " if guarded == want_hit else "BAD"
            verdicts = f"{'hit ' if raw else 'miss'} {'hit ' if guarded else 'miss'}"
            print(f"  {s:.4f} {verdicts} {mark} {a!r} ~ {b!r}")
        totals[want_hit] = (sum(r[3] for r in rows), sum(r[4] for r in rows), len(rows),
                            [r[2] for r in rows])
    (pr, pg, pn, ps), (nr, ng, nn, ns) = totals[True], totals[False]
    print(f"\nthreshold {t}: paraphrase hits {pr}/{pn} (with guard {pg}/{pn}); near-miss hits {nr}/{nn} "
          f"(with guard {ng}/{nn}); min paraphrase {min(ps):.4f}, max near miss {max(ns):.4f}")


if __name__ == "__main__":
    main()
