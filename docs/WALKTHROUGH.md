# Walkthrough

The eight points of the final presentation, in order (`capstonespec.md` D2). Each section gives the talking points, the numbers and where the evidence lives. Section numbers like §B8 refer to [`capstonespec.md`](../capstonespec.md), and D-0nn entries are in its Decision Log (A5).

Repository: <https://github.com/VJaw/enterprise-knowledge-assistant> · Demo: the production image run locally (`docker run --rm -p 7860:7860 --env-file .env eka`, then `http://localhost:7860/docs`). It isn't hosted on Hugging Face because Docker Spaces now need a PRO subscription (§8).

---

## 1. Business problem

**Acme Corp** (fictional) keeps its knowledge in policy PDFs, technical manuals, SOPs, incident reports, an FAQ, relational tables, and in relationships between systems, teams and people that nobody wrote down in one place. Leadership named six problems; each maps to one part of the design (§B1.1):

| Problem | Response |
|---|---|
| Finding accurate information is slow | One natural-language `/chat` endpoint over every source, with cited answers; a semantic cache for repeat questions |
| Knowledge is scattered | One ingestion pipeline feeds a vector index, a knowledge graph and a SQL store, and one agent can query all three |
| Search fails on multi-hop questions | Neo4j graph traversal fused with dense and keyword search; a LangGraph agent for multi-step tool use |
| Dependence on experts | Every claim is cited, so it can be checked; "who owns / who manages" comes straight from the graph |
| No safety validation | Eight guardrail layers on input, retrieved content and output |
| No observability or evaluation | Langfuse on every hop; Promptfoo, RAGAS, a safety scorecard and load tests |

Stakeholders (§B2.1): engineering wants accurate technical answers with citations, operations wants fast SOP lookup, compliance wants safe and policy-compliant output, the AI platform team wants monitoring and evaluation, and the CTO wants a production-shaped architecture.

**Data.** 94 synthetic documents (8 policy PDFs, 6 manuals, 10 SOPs, 10 incident reports, 60 FAQ pairs) plus SQLite tables (6 teams, 40 employees, 15 systems, 10 products, 300 tickets, 1,000 transactions). The data has planted fixtures, so evaluation has known answers: PII in 4 documents, a poisoned manual with a hidden "note for AI assistants", and 5 multi-hop facts whose answer exists *only* in the graph (no document names an owning team or manager, D-009).

## 2. System architecture

One FastAPI process (one Uvicorn worker, async) in one Docker image built for a Hugging Face Docker Space. The request path (§B3.2):

1. **Input rails**: injection heuristics + LLM Guard DeBERTa, LLM Guard toxicity, Presidio PII masking (reversible `<TYPE_n>` placeholders), a topic policy, and the NeMo `self check input` LLM.
2. **Semantic cache** (Qdrant), keyed on the masked message. A hit skips straight to the output rails.
3. **Query router** (gpt-4o-mini, structured output): intent, complexity, entities, rewritten query and sub-questions.
4. **Hybrid retrieval**: dense + BM25 + graph in parallel → RRF → cross-encoder → top 6.
5. **LangGraph agent** (≤ 5 steps) with tools, routed to gpt-4o-mini or gpt-4o.
6. **Groundedness judge**, then **output rails**: citation enforcement, PII re-scan, toxicity / banned topics / secret-value policy.
7. Cache write, session checkpoint, trace finalised, response with `trace_id`.

| Layer | Choice |
|---|---|
| API | FastAPI + Uvicorn, slowapi rate limit, Prometheus `/metrics` |
| Orchestration | LangGraph `StateGraph` + `AsyncSqliteSaver` sessions |
| LLMs | OpenAI gpt-4o-mini (default) and gpt-4o (complex / multi-hop) |
| Embeddings, re-ranker | `BAAI/bge-small-en-v1.5`, `BAAI/bge-reranker-base` (local, CPU) |
| Vectors + cache | Qdrant, embedded |
| Graph | Neo4j Aura Free (managed, TLS) |
| Structured data | SQLite (read-only for the agent) |
| Guardrails | NeMo Guardrails + Presidio + LLM Guard |
| Tools | Tavily web search, SQL, Cypher templates, Python sandbox, MCP (SQLite + filesystem servers) |
| Observability | Langfuse Cloud |
| Evaluation / load | Promptfoo, RAGAS, custom safety harness, Locust |

**Deployment** (§B4). Everything except the graph and the API providers is baked into the image at build time: model weights (embedder, re-ranker, three LLM Guard models, spaCy `en_core_web_lg`), the Qdrant and BM25 indexes, SQLite, and the pre-fetched MCP servers. The Space's disk is ephemeral and the graph must survive restarts, so the graph lives in Aura; the app reloads it from the committed CSVs if it's empty. Secrets come from Space secrets only. If Aura is down, `/health` reports `graph: "degraded"` and retrieval falls back to dense + BM25; a watchdog re-enables the graph within 60 s of it coming back.

Code layout (§B16): `app/` (api, pipeline, agent, tools, retrieval, graph, guardrails, cache, llm, observability), `ingestion/`, `data/`, `eval/`, `loadtest/`, `scripts/`, `tests/` (301 tests).

## 3. Retrieval strategy (vector + graph)

**Indexing** (§B6). 278 chunks: header-aware Markdown splits, per-page PDF chunks, one chunk per FAQ pair, up to 480 bge tokens with 64 overlap. Each chunk is embedded as `"<title> (<doc_id>) | <heading path>\n<text>"`, so short chunks still carry their document and section (D-011). Payload indexes on `doc_type`, `domain`, `sensitivity`, `doc_id` and `entities` allow filtering.

**Knowledge graph** (§B7). 136 nodes and 542 relationships in Aura: Person, Team, System, Component, Product, Document (with Policy / Incident labels). The CSV backbone is authoritative (OWNS, MEMBER_OF, MANAGES, DEPENDS_ON, GOVERNS, AFFECTS…). gpt-4o-mini extraction over 218 chunks adds components and mentions, but only after validation: ontology-constrained output, grounding in the chunk text, and agreement with the backbone (D-013). A spot check found 14/15 extracted edges correct. See [`screenshots/graph.png`](screenshots/graph.png).

**Hybrid pipeline** (§B8):

- **Router.** Router filters are kept only when the user's own words ask for them (D-020). Without this rule, the LLM added impossible filters to 12 of 30 golden questions.
- **Graph branch.** Entities are linked through a Neo4j full-text index. A cue word in the question picks a parameterised Cypher template (owners, dependencies, blast radius, incidents, policies, team). The rows become `[Gn]` facts with their source CSV, plus a graph-anchored dense search. The branch has a 3 s budget.
- **Fusion and re-rank.** RRF (k = 60) over dense, BM25 and graph-anchored results, then the cross-encoder re-ranks the fused top 20 down to the top 6 (D-028).

**Evidence:**

| Config (20 labelled queries) | Recall@5 | MRR@10 |
|---|---|---|
| Dense only | 0.983 | 0.942 |
| Dense + BM25 (RRF) | 0.933 | 0.877 |
| + graph (RRF) | 1.000 | 0.917 |
| **Hybrid + re-rank** | **0.990** | **0.967** |

Plain RRF is *worse* than dense (BM25 drags paraphrases down). The graph brings recall to 1.0, and the cross-encoder restores the ranking ([`retrieval_comparison.md`](../eval/reports/retrieval_comparison.md)).

The answer-level evidence is more telling. **Before the agent existed (Phase 6), dense-only RAG answered 0 of 8 multi-hop golden questions, and the hybrid pipeline answered 7 (context recall 0.000 → 0.875).** The ownership facts exist only in the graph.

RAGAS ablation in the final agentic system ([`RAGAS_REPORT.md`](../eval/reports/RAGAS_REPORT.md), [`ragas_ablation.png`](../eval/reports/ragas_ablation.png)):

| Config | Faithfulness | Answer relevancy | Context precision | Context recall |
|---|---|---|---|---|
| A dense | 0.911 | 0.784 | 0.923 | 0.926 |
| B + BM25 | 0.884 | 0.814 | 0.899 | 0.901 |
| C + graph | 0.854 | 0.827 | 0.906 | 0.883 |
| **D full** | **0.919** | 0.803 | **0.975** | **0.932** |

Caveat: the ablation flags switch off *retrieval* components, but the agent keeps its `query_graph` tool in every config. That is why A reaches 0.917 multi-hop recall here: the graph's value moved from retrieval into a tool.

## 4. Agent workflow

A LangGraph state machine ([`agent_graph.md`](agent_graph.md), §B9): `classify → retrieve → agent ⇄ tool_node → synthesize → grounding_check → (regenerate | safe_fallback) → finalize`.

- **Tools** (Pydantic args, timeouts, errors returned as strings): `search_knowledge_base`, `query_graph` (templates only), `sql_query` (read-only connection, SELECT-only AST check, table allow-list, row cap), `python_exec` (subprocess with an audit hook blocking network / subprocess / file escape, plus rlimits), `web_search` (Tavily; refuses Acme-internal queries, D-027). It also binds 13 read-only MCP tools from the SQLite and filesystem servers; 7 write tools are filtered out at startup.
- **Bounded loop**: `AGENT_MAX_STEPS = 5` agent calls. At the bound, the agent answers from what it has gathered and flags `agent_step_limit`.
- **Model routing** (§B12.2): gpt-4o-mini by default, and gpt-4o for complex / multi-hop work (11/30 golden rows), falling back to mini on errors.
- **Citations**: one id space per turn: `[n]` passages, `[Gn]` graph facts, `[SQLn]` query results (with the query), `[WEBn]` web pages.
- **Sessions**: `thread_id = session_id` in a SQLite checkpointer, so follow-ups like "and who manages that team?" resolve.

Demo: the §B9.4 question, *"Which team owns the system that caused the most SEV-1 tickets last quarter, and what does their failover SOP say about manual promotion?"* Trace [`02a`](screenshots/02a_trace_agent_b94.png)/[`02b`](screenshots/02b_trace_agent_b94.png): gpt-4o, `sql_query` (Ledger DB) → `query_graph` (Data-Platform) → `search_knowledge_base` (SOP-OPS-014 §3.2), grounding 1.0, about 10 s, $0.025.

## 5. Guardrails implementation

Eight layers (§B10), run by NeMo Guardrails with custom actions ([`app/guardrails/`](../app/guardrails/)):

| Layer | What it does | Design point |
|---|---|---|
| L1 validation | Pydantic limits, 30 req/min rate limit | 422 / 429 in the uniform error shape |
| L2 injection | Regex heuristics block; LLM Guard DeBERTa only *escalates* to the LLM self-check | DeBERTa alone flagged 13% of benign questions ("secret rotation"), too many to block on (D-024) |
| L2b indirect injection | Every retrieved passage and tool output is scanned; suspicious paragraphs become `[removed: …]` | The poisoned manual is retrieved and cited, but its password never appears |
| L3 PII | Presidio + custom `EMPLOYEE_ID`, phone and SSN-shape recognisers; reversible placeholders | Masked before *any* LLM call, trace, checkpoint or cache entry; only the user's own values are unmasked in the reply (D-025) |
| L4 topic policy | Legal advice, HR decisions about people, credential values | Asking *how to* rotate a credential is allowed |
| L5 groundedness | Claim-level gpt-4o-mini judge, threshold 0.9 → regenerate once → drop unsupported sentences | Its score goes to Langfuse as `grounding_score` |
| L6 citations | Invalid ids removed, uncited claims dropped | Rules tuned on 30 real answers (0 wrongly changed) |
| L7 / L8 output | PII re-scan, toxicity (block ≥ 0.7), multi-label banned topics, secret-value policy, conditional LLM self-check | |

**Latency design (D-023).** Running the self-check LLM in series would have blown the 400 ms budget, so it runs *alongside* the agent and cancels it if it blocks. Corpus scans are memoised at startup. Guardrail overhead went from 674 ms p50 to **157 ms p50**.

Security controls beyond the rails (§B17): secrets only from env / Space secrets, parameterised Cypher in read transactions, the read-only SQL connection, MCP write tools removed, the filesystem server rooted at `data/raw`, confidential docs never cached, and a `trace_id` on every response.

## 6. Evaluation results

| Suite | Result | Report |
|---|---|---|
| **Promptfoo** (34 cases: 8 factual, 6 multi-hop, 4 SQL, 2 web, 3 unanswerable, 4 injection, 3 PII, 2 toxic, 2 latency/cache) | 33/34 (97.1%) at Phase 10; **34/34** after the Phase 11 latency work | [`PROMPTFOO_REPORT.md`](../eval/reports/PROMPTFOO_REPORT.md) |
| **RAGAS** (30-row golden set, 4 configs) | D: faithfulness 0.919 ✅, context precision 0.975 ✅, context recall 0.932 ✅, answer relevancy 0.803 (target 0.85; multi-hop answers are numbered sub-answer chains) | [`RAGAS_REPORT.md`](../eval/reports/RAGAS_REPORT.md) |
| **Safety** (50 adversarial + 15 benign) | Critical 100%, High 100%, overall 100%, false positives 0/15 | [`SAFETY_SCORECARD.md`](../eval/reports/SAFETY_SCORECARD.md) |

The evaluation found real defects, each fixed with a test (D-027):

1. Web search answered an internal question from an unrelated public page.
2. The router invented an entity, so the agent named the wrong manager.
3. Presidio skipped an "invalid" SSN, which reached the Langfuse trace unmasked.
4. SQL citations lacked the query, so faithfulness scored 0 on those rows.

Golden set rows are checked mechanically against their sources (`tests/eval/test_golden.py`).

## 7. Observability insights

Every request is one Langfuse trace (§B13): input rails → cache lookup → agent (router, `retrieval.hybrid` with its five child spans, `agent.step_n`, `tool.*`, groundedness judge) → output rails → cache write. It carries tags (`intent:*`, `model:*`, `cache:*`, `blocked:*`), an anonymised user id, per-model cost and the `grounding_score`. Only masked text is logged, and `scripts/check_traces.py` verifies that (5/5 traces, no raw PII).

Screenshots ([`docs/screenshots/`](screenshots/)): multi-hop trace list, the §B9.4 trace tree, a blocked injection (9 ms, 3 observations), the cost/latency dashboard, grounding scores (82 scores, average 0.99), and a cache hit (no LLM generation).

What the traces and the load tests showed ([`PERFORMANCE_REPORT.md`](../eval/reports/PERFORMANCE_REPORT.md)):

- **Top latency contributor: three sequential gpt-4o-mini calls** (router, agent, groundedness judge), 85% of the simple-query p50. Re-ranking is second, and it doubles under concurrency.
- **Speculative retrieval prefetch** alongside the router, plus re-ranking the top 20 instead of 30, cut simple-query p50 from 4.1 s to **2.6 s** (p95 5.2 → 3.2 s) with no quality regression (D-028).
- **Semantic cache**: hit p95 71 ms, a 66% hit rate on repeat-heavy traffic, a **46× p50 speed-up** and −75% cost per request. Cosine similarity alone would have served the SEV1 answer to a SEV2 question (0.962 similarity), so a hit also requires the same specifics (D-026).
- **Throughput**: 1.87 req/s over a 1 → 10 user ramp, 0/455 errors; a 5-minute soak grew memory by +9.9% with 0 errors.
- **SLOs** (local): cache < 300 ms ✅, agentic < 12 s ✅, simple < 3 s missed by ≤ 178 ms at p95, with mitigations documented.

**Production container smoke test** (Phase 12). The image was run locally with `.env` standing in for the Space secrets: Docker Desktop on Apple Silicon, linux/arm64, CPU only, 10 cores. Startup took about 18 s, and `/health` was all `ok` with 13 MCP tools bound and 7 write tools dropped. The app ran as uid 1000, and memory reached 6.0 GB after the run.

| Case | Model | Tools | Citations | Latency | Result |
|---|---|---|---|---|---|
| Factual: log retention | gpt-4o-mini | (initial retrieval) | `[1]`, `[4]` | 8.1 s | 13 months, grounding 1.0 |
| Multi-hop: Payment Gateway dependency owners | gpt-4o | (graph retrieval) | `[1]`, `[G1]`–`[G7]` | 8.4 s | all 7 owners, grounding 1.0 |
| Structured: Ledger DB SEV1 tickets | gpt-4o-mini | `sql_query` | `[SQL1]` | 6.9 s | 5 |
| Analytical: §B9.4 | gpt-4o | `sql_query` → `query_graph` → `search_knowledge_base` | `[SQL1]`, `[G1]`, `[2]` | 12.7 s | Ledger DB → Data-Platform / Anthony Gonzalez → SOP-OPS-014 |
| External: latest OpenSSL CVE | gpt-4o-mini | `web_search` | `[WEB1]`, `[WEB3]` | 12.2 s | answered |
| Attack: ignore instructions | guardrails | none | none | 14 ms | blocked |
| Attack: poisoned-doc password | guardrails | none | none | 12 ms | blocked, no leak |
| Cache hit (repeat of the factual question) | cache | none | | 115–181 ms | hit |

Without the laptop GPU, uncached answers are about 2–3× slower than the native numbers above, because the cross-encoder and the LLM Guard models run on CPU. A 2-vCPU Space would be slower again, which makes the documented mitigations (local NLI judge, skipping the router on simple turns, streaming) the first work for a hosted deployment.

## 8. Challenges and improvements

Challenges, and how they were resolved (from the Decision Log, A5, and the Handoff Log, A4):

- **Dependency resolution**: unconstrained pins pulled an ancient `llm-guard` and a RAGAS that imports a removed LangChain module, so constraints were added (D-005). The MCP SQLite server crashed on mcp 2.x and was pinned (D-021).
- **Noisy LLM graph extraction**: the first run listed every known employee as "mentioned" and read "per POL-COMP-006" as GOVERNS. Fixed with grounding rules and an authoritative CSV backbone (D-013).
- **LLM-chosen filters silently emptied retrieval**: now checked in code against the user's words (D-020).
- **Guardrail false positives vs latency**: the DeBERTa injection model was demoted from blocker to escalation trigger. Self-check speculation and memoisation kept the overhead at 157 ms (D-023, D-024).
- **Cache correctness**: a pure similarity threshold can't separate SEV1/SEV2, so the cache also requires matching specifics (D-026).
- **Simple-query SLO**: the 3 s target is just out of reach with three LLM calls in series. The remaining options trade away a safety control, so they're documented, not applied.
- **Deployment**: Hugging Face now requires a PRO subscription for Docker Spaces, even on free CPU hardware, so there is no hosted demo (D-029). The image is verified locally, and `scripts/deploy_space.py` creates the Space, sets its secrets and uploads a clean snapshot through the Hub API. It uses the API rather than `git push` because the Space's git server rejects binary files without LFS/Xet.

Known gaps:

- Agentic answers sometimes skip a redundant tool step (D-022).
- Answer relevancy is 0.803 against the 0.85 target.
- One golden row (incident → system → policy) needs a graph-retrieval template.
- No hosted demo (above). If it is hosted publicly, `/chat` has no authentication beyond a per-session rate limit, so the OpenAI hard spending limit is the backstop.

Improvements (§B18.3):

- Split into API / retrieval / agent services with Qdrant Cloud and multiple workers.
- Native Qdrant hybrid (sparse) search.
- A local NLI groundedness model for single-source answers (~0.7 s saved).
- Skipping the LLM router on simple first turns.
- Streaming responses.
- Role-based access on `sensitivity`.
- An online feedback loop into Langfuse scores.
- GraphRAG community summaries for "themes across incidents" questions.
