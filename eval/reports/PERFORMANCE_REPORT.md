# Performance & Load Test Report

Phase 11 (§B15): sequential latency per intent type before and after two optimisations, semantic-cache
effectiveness, throughput under a ramp to 10 users, a 5-minute soak, and the per-stage breakdown from Langfuse
spans. All runs are local (Apple Silicon laptop, cross-encoder on CPU/MPS) against a private API process with
guardrails on; the HF Space CPU tier is measured in Phase 12 (see *Caveats*).

**Summary**

| §B15 test | Pass criteria | Result | |
|---|---|---|---|
| Latency baseline | p95 within NFR-1 (cache hit < 300 ms, simple < 3 s, agentic < 12 s) | agentic 4/4 types ✅, chit-chat ✅, cache hit 71 ms ✅; **simple p95 3,178 ms** (7/10 under 3 s) | ⚠️ mitigated |
| Cache effectiveness | hit rate ≥ 40%, p50 ≥ 5× faster on hits | hit rate **66%**, hit p50 **76 ms** vs miss 3,496 ms (**46×**) | ✅ |
| Throughput | ≥ 1 req/s sustained, error rate < 1% | **1.87 req/s** over the ramp (3.28 req/s in the last minute at 10 users), **0 / 455 errors** | ✅ |
| Soak (optional) | memory growth ≤ 15% | **+9.9%** (median RSS, 2nd minute → last minute), trend −5 MB/min; 0 / 498 errors | ✅ |
| Stage breakdown | top-2 latency contributors | **1. the sequential gpt-4o-mini calls** (groundedness judge, router, agent); **2. the cross-encoder re-rank** under concurrency | ✅ |

Regression: Promptfoo **34 / 34** after the changes; the FAQ latency case passes for the first time since
Phase 7 (2,594 ms).

Spend for the phase's measurements ≈ $2.5 (benchmarks $0.44 + $0.43, load tests $1.47, one Promptfoo run;
Langfuse stage fetches are free).

## How it was measured

| Script | What it does | Output |
|---|---|---|
| `scripts/bench_latency.py --tag <t>` | Empties `semantic_cache`, starts the API (cache on, `RATE_LIMIT=1000/minute`), sends one warm-up, then asks the 60 questions in `loadtest/queries.py` `BENCH` (10 per type) **sequentially**, each in a fresh session, then replays the 10 simple questions as cache hits. Stage times come from each request's Langfuse observations (`/api/public/v2/observations`). | `latency_<t>.json` |
| `loadtest/run_load.py <scenario…> --stages 20` | Per scenario: empty the cache, start the API, warm up, run `locust` headless, sample the API's RSS every 5 s, then summarise and pull Langfuse stages for 20 uncached requests. | `load/<scenario>.json`, `.jsonl` (one row per request), Locust CSVs |
| `loadtest/locustfile.py` | `SimpleUser` (weight 5: factual / FAQ / SQL-count questions, each asked once per cycle), `ComplexUser` (2: multi-hop and analytical), `RepeatUser` (3: 10 repeated questions and paraphrases), `CacheMixUser` (70% repeated / 30% new). Think time 1–3 s; every request has a new `session_id`. | |

Latency is the API's `latency_ms` (server side, the same number Langfuse records as the `chat` span). With
10 samples, p95 (nearest rank) is the slowest request.

## 1. Latency baseline (sequential, 10 per type)

### Before → after

"Before" is the Phase 10 code; "after" adds the two optimisations below (D-028).

| Type | SLO (p95) | p50 before | p50 after | p95 before | p95 after | Within SLO before → after | Models (after) |
|---|---|---|---|---|---|---|---|
| simple (single-doc factual / FAQ) | < 3 s | 4,101 | **2,607** | 5,244 | **3,178** ❌ | 0/10 → **7/10** | gpt-4o-mini 10 |
| chit-chat | < 3 s | 2,503 | 2,346 | 2,998 | 2,761 ✅ | 10/10 → 10/10 | gpt-4o-mini 10 |
| multi-hop | < 12 s | 4,839 | 4,237 | 10,567 | 8,256 ✅ | 10/10 → 10/10 | gpt-4o 10 |
| structured (SQL) | < 12 s | 5,115 | 2,991 | 6,173 | 4,851 ✅ | 10/10 → 10/10 | gpt-4o-mini 9, gpt-4o 1 |
| analytical | < 12 s | 8,599 | 5,900 | 11,506 | 11,536 ✅ | 10/10 → 10/10 | gpt-4o 10 |
| external (web) | < 12 s | 9,208 | 6,836 | 10,211 | 11,684 ✅ | 10/10 → 10/10 | gpt-4o-mini 9, gpt-4o 1 |
| cache hit (9 hits) | < 300 ms | 61 | 65 | 70 | **71** ✅ | 9/9 → 9/9 | cache |

All values in ms. The cache-hit replay has 10 questions, but "How long does break-glass privileged access
last?" is never cached because its answer cites the confidential POL-COMP-004 (§B17), so it ran the full
pipeline (2.7 s) in both runs. The table reports the 9 real hits; `latency_after.json` keeps all 10.

Simple-question latencies after, sorted: 2,271 · 2,328 · 2,427 · 2,453 · 2,607 · 2,748 · 2,947 · **3,075 ·
3,078 · 3,178**. The three misses are 75–178 ms over.

### Stage breakdown (after, p50 / p95 ms)

| Type | input rails | router | retrieval | retrieval exposed | re-rank | agent LLM | tools | groundedness | output rails | total |
|---|---|---|---|---|---|---|---|---|---|---|
| simple | 102 / 111 | 801 / 961 | 1,073 / 1,298 | **247 / 533** | 702 / 976 | 636 / 719 | – | 775 / 1,050 | 33 / 45 | 2,608 / 3,178 |
| multi-hop | 102 / 143 | 1,142 / 1,414 | 1,542 / 2,152 | 445 / 818 | 805 / 1,128 | 865 / 2,915 | 586 / 2,956 | 1,048 / 2,698 | 48 / 70 | 4,237 / 8,257 |
| structured | 100 / 172 | 835 / 1,776 | 885 / 2,214 | 4 / 613 | 683 / 1,087 | 1,178 / 2,159 | 13 / 19 | 729 / 2,212 | 42 / 399 | 2,991 / 4,852 |
| analytical | 100 / 250 | 1,226 / 1,425 | 1,512 / 2,002 | 285 / 729 | 808 / 937 | 1,928 / 4,594 | 405 / 1,190 | 2,006 / 4,605 | 82 / 363 | 5,900 / 11,537 |
| external | 105 / 1,356 | 872 / 1,027 | 908 / 1,128 | 5 / 263 | 863 / 1,100 | 1,319 / 2,461 | 3,136 / 5,355 | 1,211 / 2,021 | 46 / 54 | 6,837 / 11,684 |
| chit-chat | 105 / 117 | 779 / 952 | – | – | (444 / 677) | – | – | – | 502 / 715 | 2,346 / 2,762 |

*Retrieval* runs from the first retrieval span (the prefetch, when there is one) to the end of the first
`retrieval.hybrid`; *retrieval exposed* is the part after the router finished, i.e. what the request actually
waited for. Before the change both were the same (simple: 986 / 1,271). Chit-chat's re-rank is the
speculative prefetch, which usually finishes before the router says "chit-chat" (see *Caveats*).
Output rails on chit-chat include the LLM output self-check (D-023: no groundedness verdict to vouch for it).

The simple path is now almost entirely three sequential gpt-4o-mini calls: router 801 + agent 636 +
groundedness judge 775 = 2,212 ms of the 2,608 ms p50 (85%). Retrieval adds 247 ms and the rails ~135 ms.

## 2. Optimisations applied (D-028)

1. **Speculative retrieval alongside the router.** On a first turn the agent's `classify` node starts dense +
   BM25 search and the cross-encoder re-rank of the fused top 20 on the user's (PII-masked) message as an
   `asyncio` task (with the request's filters), at the same time as the router LLM call. `retrieve` uses the
   result when the router added no filters of its own; otherwise it is cancelled and retrieval runs as before. The graph branch
   still uses the router's entities (it needs them). Follow-up turns don't prefetch: their retrieval query is
   the router's rewrite, which the raw message doesn't approximate. Effect on the simple path: retrieval the
   request waits for, p50 986 → **247 ms**.
2. **Re-rank the fused top 20 instead of 30** (`app/retrieval/rerank.py` `TOP_N`). Retrieval quality on the
   Phase 5 validation set is unchanged for the full pipeline (R@5 0.990, MRR 0.967, nDCG 0.947 at both 20 and
   30); re-rank time p50 796 → 576 ms (−28%) in isolation. This was the first option listed in the Phase 5
   handoff.

Both are covered by unit tests (`tests/retrieval/test_graph_hybrid.py`: a matching prefetch is used, a
non-matching one is ignored and cancelled; `tests/agent/test_graph.py`: first turn prefetches, follow-ups
don't, chit-chat cancels).

Sequential effect: simple p50 −36% (4,101 → 2,607 ms), structured −42%, analytical −31%, external −26%,
multi-hop −12%.

## 3. Cache effectiveness (cache off vs on, 3 `CacheMixUser`s, 2 min each)

| | Cache off | Cache on |
|---|---|---|
| Requests (errors) | 67 (0) | 103 (0) |
| Throughput | 0.55 req/s | 0.86 req/s |
| Hit rate | – | **66%** (68 / 103) |
| Repeated / paraphrased questions p50 / p95 | 2,856 / 7,270 ms | **77** / 2,375 ms |
| New questions p50 / p95 | 3,485 / 6,931 ms | 3,395 / 7,843 ms |
| All requests p50 / p95 | 2,972 / 7,270 ms | 83 / 5,656 ms |
| Hits p50 / p95 / max | – | **76 / 184 / 394 ms** |
| Misses p50 / p95 | – | 3,496 / 7,393 ms |
| **Speed-up on hits (p50)** | | **46×** |
| OpenAI cost per request | $0.00257 | $0.00064 (−75%) |

The 5 repeated-question misses with the cache on are exactly the first ask of 5 of the 10 repeated questions
(the other 5 were first asked as paraphrases). Every later repeat and paraphrase hit. Throughput rises with the
cache because users wait less between requests (think time is fixed). In the ramp and soak mixes the hit rate
was 79% and 83%, and the speed-up 53× and 65×.

Hit p95 under load stays under 300 ms in every scenario (184 / 347 / 177 ms for cache-on / ramp / soak). The
ramp's 347 ms is the p95 *including queueing* behind uncached requests at 10 users; max 697 ms.

## 4. Throughput (ramp 1 → 10 users over 3 min, then 1 min at 10; `SimpleUser`:`ComplexUser`:`RepeatUser` = 5:2:3)

| Minute | 1 | 2 | 3 | 4 (10 users) |
|---|---|---|---|---|
| req/s | 0.43 | 1.32 | 2.53 | **3.28** |

| Requests | Errors | Mean req/s | Hit rate | Hits p50 / p95 | Misses p50 / p95 | Memory (RSS) |
|---|---|---|---|---|---|---|
| 455 | **0 (0%)** | **1.87** | 79% | 99 / 347 ms | 5,243 / 10,717 ms | 1.45 → 1.91 GB (max 2.14) |

Uncached requests under load, by user type:

| Scenario | Simple misses p50 / p95 | Complex misses p50 / p95 | Uncached req/s |
|---|---|---|---|
| Sequential (bench, after) | 2,607 / 3,178 | 4,237–5,900 / 8,256–11,536 | – |
| Ramp (≤ 10 users) | 5,012 / 10,465 | 6,107 / **12,615** | 0.40 |
| Soak (5 users) | 4,832 / 7,495 | 7,483 / 9,295 | 0.28 |

("Simple" Locust users ask factual, FAQ and SQL-count questions.) Under concurrency uncached latency roughly
doubles. The stage means (below) show why: the CPU-bound re-rank doubles (761 → 1,535 ms) as requests share
the cross-encoder, more of retrieval is exposed (310 → 847 ms), and the OpenAI calls are slower too
(groundedness 869 → 1,500 ms). One complex request in the ramp exceeded 12 s (p95 12.6 s).

## 5. Soak (5 users, 5 min, same mix)

| Requests | Errors | req/s (per minute) | Hit rate | RSS median 2nd min → last min | RSS trend | Min / max |
|---|---|---|---|---|---|---|
| 498 | **0** | 1.64 (1.22, 1.62, 1.88, 1.83, 1.75) | 83% | 1,572 → 1,727 MB (**+9.9%**) | **−5 MB/min** | 952 / 2,037 MB |

macOS compresses and pages memory, so single RSS samples swing by ±400 MB (952 MB at 3:30 was a
compression dip, not a release). The criterion is applied to the medians of the second and last minutes
(the first minute includes model warm-up), and the regression slope over all 61 samples is flat. No leak is
visible. Peak RSS 2.1 GB is under the HF CPU tier's 16 GB.

## 6. Stage breakdown under load (Langfuse, mean ms over 20 uncached requests per scenario)

| Stage | cache off (3 users) | cache on (3) | ramp (≤ 10) | soak (5) |
|---|---|---|---|---|
| input rails | 114 | 202 | 117 | 133 |
| router (LLM) | 905 | 940 | 1,054 | 1,046 |
| retrieval (total / exposed) | 1,215 / 310 | 1,339 / 398 | 1,901 / 847 | 1,658 / 611 |
| ↳ re-rank (CPU) | 761 | 988 | **1,535** | 1,278 |
| agent LLM | 835 | 1,132 | 1,226 | 1,223 |
| tools | 1 | 4 | 135 | 269 |
| groundedness judge (LLM) | 869 | 938 | **1,500** | 1,376 |
| output rails | 47 | 45 | 80 | 75 |
| **total** | 3,173 | 3,814 | 5,244 | 5,064 |

**Top-2 latency contributors**

1. **The sequential gpt-4o-mini calls**: router, agent and groundedness judge, together 2.2 s of the 2.6 s
   simple p50 and 3.3–3.8 s under load. The judge (0.8–1.5 s) and the router (0.8–1.1 s) are the largest
   single stages on most requests; neither depends on local hardware.
2. **The cross-encoder re-rank**: 0.7 s sequential, 1.5 s mean at 10 users because concurrent requests share
   the CPU. The prefetch hides most of it behind the router on a quiet server; under load the hidden share
   falls from 75% to 55%. This is the stage that will grow on the HF 2-vCPU tier.

## 7. SLO status and mitigations

| SLO | Status | Mitigation |
|---|---|---|
| Cache hit p95 < 300 ms | ✅ 71 ms sequential; 177–347 ms p95 under load (347 at 10 users, including queueing) | None needed locally. On HF CPU: skip the output scanners on a hit whose text was scanned when cached (Phase 9 note). |
| Agentic p95 < 12 s | ✅ sequential (worst 11.7 s, external). Under 10 concurrent users one complex request took 12.6 s | gpt-4o routing for complex questions is the main cost; the cache absorbs repeats (79–83% hit rate in the mixes). |
| **Simple p95 < 3 s** | ❌ **3,178 ms** sequential (p50 2,607; 7/10 under 3 s); ~5 s p50 under concurrent load | See below. |

**Mitigations for the simple-question gap** (documented, not applied):

- **What already holds:** in the realistic mixes 79–83% of all requests are cache hits answered in < 100 ms
  p50, so the user-facing p95 for repeated FAQ-style questions is far below 3 s. The gap applies to first-time
  questions.
- **Groundedness judge on a local NLI model** for single-source gpt-4o-mini answers (≈ 0.7 s saved, the
  Phase 7 handoff option). Not applied: the judge is the L5 hallucination control (§B10.1), and replacing it
  needs its own evaluation against the RAGAS faithfulness and safety gates (Phase 10), which this phase can't
  redo within budget.
- **Router skip for first turns**: a first turn has no history to rewrite; a local intent classifier plus
  the existing entity regexes could replace the LLM router call for single-entity factual questions
  (≈ 0.8 s). Risk: D-020/D-027 showed router errors cause wrong filters and wrong answers.
- **Streaming synthesis** (`/chat/stream`, cut in D-026): would cut time to first token, but the output
  rails and judge must finish before text can be shown, so it would need a "verified after streaming" UX.
- **Under concurrency**: run the cross-encoder in a dedicated thread pool with a bounded queue, or swap in a
  MiniLM cross-encoder / quantised weights (Phase 5 measured options) on the HF tier.

## 8. Caveats

- **Local hardware.** These numbers are from an Apple Silicon laptop. The HF CPU tier (2 vCPU) will be
  several times slower on the cross-encoder and the local guardrail models; OpenAI calls are unaffected.
  Phase 12 should run the Locust ramp against the deployed Space (`locust -f loadtest/locustfile.py --host
  https://<space>.hf.space …`) and record the cache-hit p95 there.
- **Wasted prefetch on chit-chat.** The prefetch is started before the router knows the intent; on chit-chat
  (and blocked or SQL-only questions) its re-rank (~450 ms of CPU) is wasted. It runs while the router waits
  on the network, so it doesn't add latency on a quiet server, but it adds CPU load under concurrency.
- **Uncached capacity.** The cache-off scenario was bound by 3 users and their think time (0.55 req/s), so
  the server's throughput with no cache at all was not pushed to saturation. With the cache, 10 users
  sustained 3.28 req/s with no errors.
- **OpenAI variance.** OpenAI latency varied between runs (groundedness p50 0.78 s sequential vs 1.5 s mean
  under load). Each figure comes from a single run.
- **Out-of-credit errors are 500s.** When the OpenAI account ran out of credit mid-benchmark, `/chat`
  returned `500 INTERNAL_ERROR`. A `503 UPSTREAM_UNAVAILABLE` would be more accurate (§B11.3); noted for
  Phase 12. That partial run was discarded and the "after" benchmark re-run in full.

## Regression check

`make eval` after the optimisations (eval `eval-tWU-2026-10-31T21:25:48`): **34 / 34 passed (100%)**, up from
33 / 34 in Phase 10. The FAQ latency case ("What is the refund window?" ≤ 3,000 ms), the only failure since
Phase 7, now passes at **2,594 ms**; the cache case hit in 58 ms. The prefetch and the top-20 re-rank changed
no answer-quality case.

## Files

- `eval/reports/latency_before.json`, `latency_after.json`: per-request rows (latency, model, tools, cost,
  `Server-Timing`, Langfuse stages) and the summary.
- `eval/reports/load/<scenario>.json` (summary), `.jsonl` (per request), `.meta.json` (users, duration, RSS
  samples), `_stats*.csv` (Locust).
- Re-summarise without new requests: `python scripts/bench_latency.py --report-only --tag after`,
  `python loadtest/run_load.py --report-only cache_off cache_on ramp soak`.
