# Retrieval Baseline (Phase 3, dense only)

Generated 2026-10-30 by `scripts/validate_retrieval.py`. Embeddings `BAAI/bge-small-en-v1.5`, Qdrant cosine, 10 hand-labelled queries.
Relevance is judged per document: retrieved chunks (top 20) are collapsed to doc ids in rank order.

| Config | Recall@5 | MRR@10 | nDCG@10 |
|---|---|---|---|
| Dense only | 1.000 | 0.933 | 0.933 |

## Per query

| # | Query | Relevant | First relevant rank | Recall@5 | Top 5 docs |
|---|---|---|---|---|---|
| 1 | What is the retention period for customer logs? | FAQ-004, FAQ-030, FAQ-046, POL-COMP-001 | 1 | 1.00 | FAQ-030, FAQ-004, FAQ-046, MAN-ENG-004, POL-COMP-001 |
| 2 | What are the steps for manual promotion during a Ledger DB failover? | SOP-OPS-014 | 1 | 1.00 | SOP-OPS-014, INC-2025-012, MAN-ENG-001, SOP-OPS-011, INC-2025-031 |
| 3 | Which refunds need approval from Finance? | SOP-SUP-003 | 1 | 1.00 | SOP-SUP-003, FAQ-033, FAQ-008, FAQ-051, FAQ-016 |
| 4 | How do I reset MFA for a customer who lost their device? | FAQ-001, FAQ-026, FAQ-034, FAQ-049, SOP-SUP-005 | 1 | 1.00 | FAQ-026, FAQ-001, FAQ-049, FAQ-034, SOP-SUP-005 |
| 5 | What was the root cause of the payment latency spike? | INC-2025-031 | 1 | 1.00 | INC-2025-031, MAN-ENG-001, INC-2025-008, INC-2025-036, INC-2025-040 |
| 6 | How often are secrets in the Secrets Vault rotated? | SOP-OPS-009 | 1 | 1.00 | SOP-OPS-009, MAN-ENG-002, INC-2025-003, MAN-ENG-003, POL-COMP-004 |
| 7 | Which systems does the Payment Gateway depend on? | MAN-ENG-001 | 1 | 1.00 | MAN-ENG-001, MAN-ENG-005, MAN-ENG-006, FAQ-019, INC-2025-031 |
| 8 | How far ahead of expiry must TLS certificates be renewed? | POL-COMP-005, SOP-OPS-018 | 1 | 1.00 | SOP-OPS-018, INC-2025-022, MAN-ENG-006, MAN-ENG-002, POL-COMP-005 |
| 9 | What happened in INC-2025-012? | INC-2025-012 | 3 | 1.00 | INC-2025-040, INC-2025-044, INC-2025-012, INC-2025-017, MAN-ENG-002 |
| 10 | How quickly must we respond to a chargeback notice? | SOP-SUP-008 | 1 | 1.00 | SOP-SUP-008, SOP-SUP-003, FAQ-016, INC-2025-036, FAQ-033 |
