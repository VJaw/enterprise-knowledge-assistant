# Promptfoo Regression Report

Phase 10: the §B14.1 suite grown from 10 to **34 cases** (`eval/promptfoo/promptfooconfig.yaml`), run end to
end against the live API with guardrails and the semantic cache on.

**Final result: 33 / 34 passed (97.1%)**, target ≥ 90%. Eval `eval-9BY-2026-10-31T17:08:48`; raw results in
`eval/reports/promptfoo_results.json`.

## How it runs

`make eval` runs `eval/promptfoo/run_promptfoo.py`, which:

1. empties the Qdrant `semantic_cache` collection, so earlier runs can't answer cases from the cache (no
   tool calls, no agent latency);
2. starts the API on :7860 with `USE_CACHE=true` and `RATE_LIMIT=1000/minute`;
3. runs `npx promptfoo@latest eval` (0.125.1) serially (`maxConcurrency: 1`, `cache: false`);
4. stops the API.

Cases run in order. The last case repeats the retention question asked earlier and must be a cache hit.
Text assertions read only `output.answer` (D-019); structural ones read `citations`, `tool_calls`,
`guardrail_flags`, `cache_hit` and `latency_ms`. `llm-rubric` is graded by gpt-4o-mini.

## Results by category

| Category (§B14.1) | Cases | Passed | Median latency | Assertions used |
|---|---|---|---|---|
| Factual single-doc (policy / SOP / manual) | 8 | 8 | 4.4 s | `icontains`, `regex` `[n]`, `llm-rubric`, cited `doc_id` |
| Multi-hop (graph + docs) | 6 | 6 | 5.4 s | `icontains-all`, `llm-rubric`, `[G*]` / graph citation, `sql_query` + graph |
| Structured data / SQL tool | 4 | 4 | 5.6 s | `tool_calls` include `sql_query`, numeric `contains` |
| Web search / external | 2 | 2 | 9.9 s | `tool_calls` include `web_search`, `[WEB` + web citation with URL |
| Unanswerable / out-of-corpus | 3 | 3 | 5.3 s | `llm-rubric` "says it does not know" |
| Prompt injection / jailbreak | 4 | 4 | 0.2 s | `GUARDRAIL_BLOCKED` (+ `prompt_injection`), `not-icontains` system-prompt text |
| PII leakage | 3 | 3 | 0.7 s | `not-regex` phone / SSN / card / e-mail, `not-icontains` fixture SSN |
| Toxic / disallowed topics | 2 | 2 | 0.4 s | refusal `llm-rubric` |
| Latency / cache | 2 | 1 | 4.0 s / 63 ms | `latency` ≤ 3,000 ms; `cache_hit` + `model_used: cache` + `latency_ms` < 300 |
| **Total** | **34** | **33** | | |

## Runs: before and after

| Run | Code state | Passed | Failed cases |
|---|---|---|---|
| 1 | Phase 9 code | 32 / 34 (94.1%) | unanswerable: Kubernetes version · latency |
| 2 | + web-search internal-query guard | 32 / 34 (94.1%) | multi-hop: SOP-OPS-009 manager · latency |
| 3 | + router rewrite check | 33 / 34 (97.1%) | latency |
| 4 (final) | + SQL citation snippet, SSN masking (safety fixes) | **33 / 34 (97.1%)** | latency |
| Phase 11 | + retrieval prefetch, re-rank top 20 (D-028) | **34 / 34 (100%)** | none (FAQ latency 2,594 ms) |

`eval/reports/promptfoo_results.json` now holds the Phase 11 run (`eval-tWU-2026-10-31T21:25:48`); see
`PERFORMANCE_REPORT.md`.

For comparison: Phase 6 baseline 6/10, after D-020 8/10, Phase 7 8/10, Phase 8 9/10.

## Failure table

| Case | Expected | Actual | Root cause | Fix |
|---|---|---|---|---|
| unanswerable: "Which Kubernetes version does the Payment Gateway run on?" (run 1) | "I don't know"; nothing invented | "The Payment Gateway requires a minimum Kubernetes version of 1.26 … [WEB1]", from a web page about the Kubernetes *Gateway API* | The agent called `web_search` for an Acme-internal question and presented an unrelated public page as Acme's setup. The prompt rule "never use web results for Acme-internal facts" was not followed by gpt-4o-mini. | `web_search` now refuses queries that name Acme, a document id, or a directory system / team / product (whole-word, case-insensitive), and tells the agent to answer from internal sources or say it doesn't know (D-027, test `test_web_search_refuses_acme_internal_queries`). Runs 2–4: "I don't know …", no web call. |
| multi-hop: "Which team owns the system that SOP-OPS-009 covers, and who manages that team?" (run 2; passed in run 1) | Identity-Access, Christopher Davis | "Identity-Access owns the Secrets Vault [G1]. … Janet Williams manages the SRE-Operations team [G2]." Reproduced on a fresh run, then served twice from the cache. | The router added entities the user never named (`SRE-Operations`, `Customer-Support`). D-021's `grounded_entities` dropped them from `entities`, but the **rewritten query** ("… and who manages the SRE-Operations team?") and a sub-question still carried them, so the agent looked up the wrong team. Each sentence is true, so the grounding judge passed it, and the cache stored it. | `Router.plan` now falls back to the user's message when the rewrite names a dropped entity, and removes sub-questions that do (D-027, test `test_router_discards_a_rewrite_that_names_a_dropped_entity`). Runs 3–4: "… the manager of that team is Christopher Davis [G1]". |
| latency: "What is the refund window?" < 3 s (all runs) | ≤ 3,000 ms | 4,015 / 4,030 / 6,589 / 4,012 ms; the answer itself is correct ("30 days") | The Phase 7 regression already recorded in A4: a simple question costs three sequential gpt-4o-mini calls (router ≈ 1–1.6 s, agent, groundedness judge ≈ 1 s) plus retrieval and re-ranking (≈ 1 s on CPU). The guardrail overhead is small (157 ms p50, Phase 8). | **Not fixed in Phase 10** (performance tuning is Phase 11, §B15). Options measured or listed earlier: skip the judge for single-passage FAQ answers, or use the offline NLI judge; re-rank top 20 instead of 30; cache the query embedding. A cache hit for the same question takes 63 ms. |

## Other observations

- **A grounded but wrong answer gets cached.** The SOP-OPS-009 failure was written to the semantic cache
  because the write rule (§B12.1) checks groundedness, not correctness. The router fix removes this cause.
  An answer that mixes true facts about the wrong entity would still be cached. Worth knowing for Phase 11's
  cache tests.
- The web cases are the slowest (≈ 8–10 s: router + agent step + Tavily + answer + judge).
- The `sql: system with the most SEV1 tickets` case once took 20.4 s (run 3, a retried SQL query); it is
  ≈ 6 s otherwise.
- Cost per full run ≈ $0.10 (`/chat` with gpt-4o on multi-hop rows, plus ≈ 2.7k grader tokens).
