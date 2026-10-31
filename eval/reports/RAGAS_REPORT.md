# RAGAS Evaluation Report

Phase 6 baseline (milestone M2): config **A** (dense only) vs **D** (dense + BM25 + graph + re-rank) on the
30-row golden set, before the agent (Phase 7) and guardrails (Phase 8) exist. Configs B and C and the
Langfuse dataset run are added in Phase 10.

**How the numbers are produced.** `eval/ragas/run_ragas.py --config X` starts the API with the config's
`USE_*` flags (and `USE_CACHE=false`), sends every golden question to `POST /chat` in a fresh session, and
scores the answers with the RAGAS 0.4 metrics (judge: gpt-4o-mini). `retrieved_contexts` are the answer's
**citation snippets** (§B14.2, D-012), so a context only counts if the answer cites it. Rows RAGAS can't score
as is are handled as follows (D-018):

- An answerable row with no citations gets context precision = context recall = 0, and faithfulness = n/a if
  the answer is "I don't know" (no claims), 0 if it makes uncited claims.
- Unanswerable rows are scored only on whether the assistant abstained. RAGAS would give a correct "I don't
  know" an answer relevancy of 0, so that metric and the context metrics are n/a there.
- RAGAS scores "I don't know" on an answerable row as answer relevancy 0. The **false abstention** column
  shows how much of each mean comes from abstentions rather than wrong answers.

<!-- BEGIN GENERATED (run_ragas.py) -->

_Generated 2026-10-30 by `eval/ragas/run_ragas.py` from `ragas_A.json`, `ragas_D.json`. Golden set: 30 rows (`eval/datasets/golden.jsonl`). Evaluator `gpt-4o-mini`; answer relevancy embeddings `text-embedding-3-small`. Generator `gpt-4o-mini`._

### Overall

| Config | Faithfulness | Answer relevancy | Context precision | Context recall | Correct abstention (unanswerable) | False abstention (answerable) | p50 latency | /chat cost |
|---|---|---|---|---|---|---|---|---|
| *Target (§B14.2)* | *≥ 0.85* | *≥ 0.85* | *≥ 0.75* | *≥ 0.80* | | | | |
| A: baseline (dense) | 0.930 | 0.328 | 0.370 | 0.352 | 1.000 | 0.630 | 1962 ms | $0.0048 |
| D: full (+ rerank) | 0.785 | 0.596 | 0.635 | 0.599 | 1.000 | 0.296 | 3030 ms | $0.0052 |

### By query type

Each cell lists the configs in order (A / D). Context metrics and answer relevancy are n/a for unanswerable rows, which are scored on abstention instead.

| Query type | n | Faithfulness | Answer relevancy | Context precision | Context recall | Abstained |
|---|---|---|---|---|---|---|
| factual | 10 | 0.917 / 0.857 | 0.546 / 0.617 | 0.600 / 0.600 | 0.550 / 0.517 | 0.400 / 0.300 |
| multi_hop | 8 | n/a / 0.604 | 0.000 / 0.683 | 0.000 / 0.893 | 0.000 / 0.875 | 1.000 / 0.125 |
| structured | 5 | n/a / 0.875 | 0.000 / 0.200 | 0.000 / 0.000 | 0.000 / 0.000 | 1.000 / 0.800 |
| faq | 4 | 0.950 / 1.000 | 0.850 / 0.865 | 1.000 / 1.000 | 1.000 / 1.000 | 0.000 / 0.000 |
| unanswerable | 3 | n/a / n/a | n/a / n/a | n/a / n/a | n/a / n/a | 1.000 / 1.000 |

**Gate (Phase 6): D ≥ A on multi-hop context recall: 0.875 vs 0.000 ✅**

### Per row

F = faithfulness, AR = answer relevancy, CP = context precision, CR = context recall, cit = number of citations, ✋ = abstained.

| # | Type | Question | A: F / AR / CP / CR (cit) | D: F / AR / CP / CR (cit) |
|---|---|---|---|---|
| 1 | factual | How long are audit logs in the Audit Log Store retained, and can th… | 1.000 / 0.881 / 1.000 / 1.000 (1) | 1.000 / 0.881 / 1.000 / 1.000 (1) |
| 2 | factual | What are the recovery time and recovery point objectives for the Le… | 1.000 / 0.840 / 1.000 / 1.000 (2) | 1.000 / 0.840 / 1.000 / 1.000 (2) |
| 3 | factual | How long does break-glass privileged access last, and where is it i… | 1.000 / 0.916 / 1.000 / 1.000 (1) | 0.000 / 0.916 / 0.000 / 0.000 (1) |
| 4 | factual | At what consumer lag should the Kafka Cluster alert fire? | 0.500 / 0.924 / 1.000 / 0.500 (2) | 1.000 / 0.924 / 1.000 / 0.500 (2) |
| 5 | factual | What are the steps for manual promotion during a Ledger DB failover? | 1.000 / 1.000 / 1.000 / 1.000 (1) | 1.000 / 1.000 / 1.000 / 1.000 (1) |
| 6 | factual | How quickly must an emergency secret rotation happen, and how long … | – / 0.000 / 0.000 / 0.000 (0) ✋ | – / 0.000 / 0.000 / 0.000 (0) ✋ |
| 7 | factual | How far ahead of expiry must API Gateway TLS certificates be renewed? | 1.000 / 0.894 / 1.000 / 1.000 (1) | 1.000 / 0.886 / 1.000 / 1.000 (2) |
| 8 | factual | Who has to approve a refund of more than $5,000? | – / 0.000 / 0.000 / 0.000 (0) ✋ | – / 0.000 / 0.000 / 0.000 (0) ✋ |
| 9 | factual | How quickly must we respond to a chargeback notice, and when does a… | – / 0.000 / 0.000 / 0.000 (0) ✋ | 1.000 / 0.727 / 1.000 / 0.667 (2) |
| 10 | factual | What caused the payment latency spike in INC-2025-031, and how long… | – / 0.000 / 0.000 / 0.000 (0) ✋ | – / 0.000 / 0.000 / 0.000 (0) ✋ |
| 11 | multi_hop | Who manages the team that owns the system covered by SOP-OPS-014 (D… | – / 0.000 / 0.000 / 0.000 (0) ✋ | 0.667 / 0.732 / 1.000 / 1.000 (1) |
| 12 | multi_hop | Which team owns the system that SOP-OPS-009 (Secrets Rotation) cove… | – / 0.000 / 0.000 / 0.000 (0) ✋ | 0.667 / 0.000 / 1.000 / 1.000 (1) ✋ |
| 13 | multi_hop | Who is the manager of the team that owns the system handled by SOP-… | – / 0.000 / 0.000 / 0.000 (0) ✋ | 1.000 / 0.793 / 1.000 / 1.000 (1) |
| 14 | multi_hop | Who manages the team that owns the system covered by SOP-SUP-003 (R… | – / 0.000 / 0.000 / 0.000 (0) ✋ | 0.000 / 0.967 / 1.000 / 1.000 (1) |
| 15 | multi_hop | SOP-SUP-008 (Chargeback Handling) requires a risk score from one of… | – / 0.000 / 0.000 / 0.000 (0) ✋ | 0.667 / 0.453 / 1.000 / 1.000 (1) |
| 16 | multi_hop | Which teams own the systems Payment Gateway depends on? | – / 0.000 / 0.000 / 0.000 (0) ✋ | 1.000 / 0.974 / 0.143 / 1.000 (7) |
| 17 | multi_hop | Which team owns the system affected by INC-2025-022 (the expired TL… | – / 0.000 / 0.000 / 0.000 (0) ✋ | 0.500 / 0.550 / 1.000 / 1.000 (1) |
| 18 | multi_hop | Which policies govern the systems affected by incident INC-2025-012? | – / 0.000 / 0.000 / 0.000 (0) ✋ | 0.333 / 0.993 / 1.000 / 0.000 (1) |
| 19 | structured | How many SEV1 tickets are there in total? | – / 0.000 / 0.000 / 0.000 (0) ✋ | – / 0.000 / 0.000 / 0.000 (0) ✋ |
| 20 | structured | How many support tickets currently have the status 'open'? | – / 0.000 / 0.000 / 0.000 (0) ✋ | – / 0.000 / 0.000 / 0.000 (0) ✋ |
| 21 | structured | Which system has the most SEV1 tickets, and how many does it have? | – / 0.000 / 0.000 / 0.000 (0) ✋ | – / 0.000 / 0.000 / 0.000 (0) ✋ |
| 22 | structured | How many transactions were recorded in the EMEA region? | – / 0.000 / 0.000 / 0.000 (0) ✋ | – / 0.000 / 0.000 / 0.000 (0) ✋ |
| 23 | structured | How many employees are on the Data-Platform team? | – / 0.000 / 0.000 / 0.000 (0) ✋ | 0.875 / 1.000 / 0.000 / 0.000 (7) |
| 24 | faq | What is the session timeout for the Customer Portal? | 1.000 / 0.791 / 1.000 / 1.000 (3) | 1.000 / 0.789 / 1.000 / 1.000 (3) |
| 25 | faq | Which currencies does Acme Pay support? | 1.000 / 0.991 / 1.000 / 1.000 (4) | 1.000 / 0.991 / 1.000 / 1.000 (4) |
| 26 | faq | How long does it take for a refund to reach my bank account? | 1.000 / 0.761 / 1.000 / 1.000 (4) | 1.000 / 0.726 / 1.000 / 1.000 (4) |
| 27 | faq | Does Acme store my full card number? | 0.800 / 0.858 / 1.000 / 1.000 (1) | 1.000 / 0.956 / 1.000 / 1.000 (5) |
| 28 | unanswerable | What is Acme's parental leave policy? | – / – / – / – (0) ✋ | – / – / – / – (0) ✋ |
| 29 | unanswerable | What is the daily per-diem limit for business travel expenses? | – / – / – / – (0) ✋ | – / – / – / – (0) ✋ |
| 30 | unanswerable | Which Kubernetes version does the Payment Gateway run on? | – / – / – / – (0) ✋ | – / – / – / – (0) ✋ |

Judge errors (scored n/a): A 0, D 0.

<!-- END GENERATED -->

## Interpretation (Phase 6 baseline)

**1. The graph is what makes multi-hop questions answerable.** Dense only (A) abstains on all 8 multi-hop
rows, because ownership and managers exist only in the CSV backbone / graph (D-009). The full pipeline (D)
answers 7 of 8 with context recall **0.875**, which is the Phase 6 gate (D ≥ A on multi-hop context recall).
The one miss, row 18 ("policies that govern the systems affected by INC-2025-012"), names only POL-COMP-007
out of 6 policies: the graph retriever has no incident → system → policy template for a Document seed with a
policy cue (D-016), so the answer leans on the BCDR policy text that mentions INC-2025-012.

**2. The biggest single loss is a router bug, not retrieval.** Factual rows 6, 8 and 10 abstain in *both*
configs although retrieval ranks the right chunk first when run without filters. The Phase 5 router adds
filters the user never asked for, and some combinations match nothing: `{doc_type: policy, domain:
operations}` for the secret-rotation and refund questions, `{doc_type: incident, domain: operations}` for
INC-2025-031 (an engineering incident). Run on the golden set, the router set filters on 12 of 30
questions, despite D-015's prompt rule. The same bug fails two Promptfoo cases ("refund over $5,000",
"refund window"). Fix candidates for the next phase that touches retrieval (each needs an A5 entry): drop
router filters entirely, or keep them only when the filtered search returns hits and retry unfiltered
otherwise. Fixing it would lift factual context recall from ~0.52 to ~0.8 on this set.

**3. Faithfulness drops from A to D (0.930 → 0.785). Most of that is how the metric is measured, not more
hallucination.** A abstains on 17 of 27 answerable rows, and abstentions aren't scored, so A's faithfulness
covers only the 10 easy rows it answered. On answered rows only, the comparison is A 0.93 vs D 0.79. The D
misses are mostly multi-hop answers that repeat the question's premise ("SOP-OPS-014 covers the Ledger DB")
while citing only the `[Gn]` ownership fact. RAGAS sees only cited contexts, so the premise counts as
unsupported (rows 11, 12, 15, 17: 0.50–0.67). Two are real errors:
- row 14 calls the Billing Service "the Refund Escalation system" (0.00);
- row 3 cites the wrong chunk. "expires after 4 hours" sits at the end of page 1 of POL-COMP-004, and the
  model cited the page-2 chunk that holds only the sentence's tail. The answer is right but the citation is
  wrong. Phase 8's citation enforcement and groundedness check should catch this.

**4. Structured/SQL rows score ~0 as expected.** There is no `sql_query` tool until Phase 7. D abstains on 4
of 5. Row 23 ("employees on the Data-Platform team") is answered from graph neighbourhood facts and
miscounts: it says "eight" and lists 7 names (the true count is 7).

**5. Answer relevancy is low only because abstentions score 0.** On answered rows it is 0.886 (A) and
0.847 (D), near the 0.85 target. Overall it is 0.328 / 0.596, which tracks the false-abstention rate
(63% / 30%).

**6. FAQ and unanswerable rows are solid.** FAQ rows reach 1.0 context precision and recall in both configs,
and all 3 unanswerable questions get "I don't know" in both (no invented policy).

**7. Judge noise to keep in mind.** Row 16 (seven `[Gn]` facts, all correct) gets context precision 0.143
because the judge rates each one-line fact alone against a 7-part reference. Row 4 gets context recall 0.5
in both configs although the cited chunk holds the alert threshold. Read single-row scores with care; the
per-type means are more stable.

**Targets (§B14.2) at baseline:** not met overall (D: 0.785 / 0.596 / 0.635 / 0.599 vs 0.85 / 0.85 / 0.75 /
0.80), as expected before the agent and guardrails. The gaps come from, in order of size: router filter
abstentions (point 2), the missing SQL tool (point 4), and multi-hop citation coverage (point 3).

Cost: `/chat` ≈ $0.005 per 30-row config. RAGAS judging (gpt-4o-mini + text-embedding-3-small) adds a few
cents per config.
