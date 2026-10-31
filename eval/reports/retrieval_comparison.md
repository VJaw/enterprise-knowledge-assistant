# Retrieval Comparison (Phase 5)

Generated 2026-10-30 by `scripts/validate_retrieval.py --compare`. 20 hand-labelled queries; embeddings `BAAI/bge-small-en-v1.5`, re-ranker `BAAI/bge-reranker-base`, router `gpt-4o-mini`. Neo4j Aura reachable: True.

Relevance is judged per document (retrieved chunks collapsed to doc ids in rank order). Single retrievers and RRF configs are ranked over their whole candidate list; rerank configs are scored on exactly what the pipeline returns (top 6 chunks with cross-encoder score ≥ 0.1). Every config gets the same router plan (original query, router entities, no router filters).

| Config | Recall@5 | MRR@10 | nDCG@10 |
|---|---|---|---|
| Dense only | 0.983 | 0.942 | 0.939 |
| BM25 only | 0.850 | 0.791 | 0.808 |
| Dense + BM25 (RRF) | 0.933 | 0.877 | 0.889 |
| Dense + BM25 + graph (RRF) | 1.000 | 0.917 | 0.921 |
| Dense + BM25 + rerank | 0.990 | 0.967 | 0.947 |
| Hybrid + rerank (full) | 0.990 | 0.967 | 0.947 |

## Latency (hybrid + rerank, 60 searches after a warm-up pass, local CPU)

Router (gpt-4o-mini) time is not included: it is a separate generation span before retrieval.

| Stage | p50 ms | p95 ms | max ms |
|---|---|---|---|
| total | 1063 | 1198 | 1296 |
| dense | 15 | 16 | 17 |
| bm25 | 1 | 3 | 3 |
| graph | 266 | 648 | 750 |
| rerank_wait | 776 | 1093 | 1102 |

Graph unavailable check (query #16 with the graph switched off): 6 chunks, 0 facts, flags ['graph_unavailable'].

## Router plans and graph facts (full pipeline)

| # | Intent | Entities | Graph facts |
|---|---|---|---|
| 1 | factual | — | — |
| 2 | factual | Ledger DB | Ledger DB -[HAS_COMPONENT]-> connection pool<br>Ledger DB -[HAS_COMPONENT]-> primary instance<br>Ledger DB -[HAS_COMPONENT]-> replica instance<br>Ledger DB <-[DEPENDS_ON]- Billing Service <-[AFFECTS]- INC-2025-012<br>Ledger DB <-[DEPENDS_ON]- Payment Gateway <-[AFFECTS]- INC-2025-031<br>Ledger DB -[DEPENDS_ON]-> Secrets Vault <-[MENTIONS]- MAN-ENG-001<br>Ledger DB -[DEPENDS_ON]-> Monitoring Stack <-[MENTIONS]- MAN-ENG-004<br>Ledger DB <-[GOVERNS]- POL-COMP-001 <-[RELATED_TO]- MAN-ENG-006<br>Ledger DB <-[DEPENDS_ON]- Data Warehouse <-[GOVERNS]- POL-COMP-001<br>Ledger DB -[DEPENDS_ON]-> Monitoring Stack <-[GOVERNS]- POL-COMP-003 |
| 3 | factual | — | — |
| 4 | factual | Customer-Support | Customer-Support <-[AUTHORED_BY]- SOP-SUP-003<br>Customer-Support <-[AUTHORED_BY]- SOP-SUP-005<br>Customer-Support <-[AUTHORED_BY]- SOP-SUP-003 -[RELATED_TO]-> SOP-SUP-008<br>Customer-Support <-[MEMBER_OF]- Angela Cohen<br>Customer-Support <-[MEMBER_OF]- Deborah Lynch<br>Customer-Support <-[MEMBER_OF]- Frank Gray<br>Customer-Support <-[MEMBER_OF]- George Daniel<br>Customer-Support <-[MEMBER_OF]- Megan Parsons<br>Customer-Support <-[MEMBER_OF]- Veronica Bowman<br>Customer-Support -[OWNS]-> Support Desk |
| 5 | analytical | Payment Gateway | Payment Gateway -[HAS_COMPONENT]-> Checkout API<br>Payment Gateway -[HAS_COMPONENT]-> Payment Router<br>Payment Gateway -[HAS_COMPONENT]-> Settlement Batch Job<br>Payment Gateway -[HAS_COMPONENT]-> Tokenization Service<br>Payment Gateway -[DEPENDS_ON]-> Ledger DB <-[AFFECTS]- INC-2025-031<br>Payment Gateway -[DEPENDS_ON]-> Fraud Detection Engine <-[AFFECTS]- INC-2025-040<br>Payment Gateway <-[OWNS]- Payments-Core <-[AUTHORED_BY]- MAN-ENG-001<br>Payment Gateway -[DEPENDS_ON]-> Auth Service <-[MENTIONS]- MAN-ENG-002<br>Payment Gateway -[DEPENDS_ON]-> Ledger DB <-[MENTIONS]- MAN-ENG-004<br>Payment Gateway -[DEPENDS_ON]-> Ledger DB <-[GOVERNS]- POL-COMP-003 |
| 6 | factual | Secrets Vault | Secrets Vault <-[DEPENDS_ON]- Auth Service -[HAS_COMPONENT]-> Token Issuer<br>Secrets Vault -[HAS_COMPONENT]-> Token signing keys<br>Secrets Vault <-[DEPENDS_ON]- Auth Service <-[AFFECTS]- INC-2025-003<br>Secrets Vault <-[DEPENDS_ON]- Payment Gateway <-[MENTIONS]- MAN-ENG-001<br>Secrets Vault <-[OWNS]- Identity-Access <-[AUTHORED_BY]- MAN-ENG-002<br>Secrets Vault <-[DEPENDS_ON]- Auth Service <-[GOVERNS]- POL-COMP-004<br>Secrets Vault <-[DEPENDS_ON]- Payment Gateway <-[GOVERNS]- POL-COMP-005<br>Secrets Vault <-[GOVERNS]- POL-COMP-004 <-[RELATED_TO]- SOP-OPS-009<br>Secrets Vault <-[OWNS]- Identity-Access -[OWNS]-> Auth Service<br>Secrets Vault <-[DEPENDS_ON]- Payment Gateway <-[DEPENDS_ON]- Billing Service |
| 7 | multi_hop | Payment Gateway | Payment Gateway DEPENDS_ON API Gateway (tier 1); SRE-Operations team OWNS API Gateway; manager: Janet Williams<br>Payment Gateway DEPENDS_ON Auth Service (tier 1); Identity-Access team OWNS Auth Service; manager: Christopher Davis<br>Payment Gateway DEPENDS_ON Kafka Cluster (tier 1); Data-Platform team OWNS Kafka Cluster; manager: Anthony Gonzalez<br>Payment Gateway DEPENDS_ON Ledger DB (tier 1); Data-Platform team OWNS Ledger DB; manager: Anthony Gonzalez<br>Payment Gateway DEPENDS_ON Secrets Vault (tier 1); Identity-Access team OWNS Secrets Vault; manager: Christopher Davis<br>Payment Gateway DEPENDS_ON Fraud Detection Engine (tier 2); Payments-Core team OWNS Fraud Detection Engine; manager: Priya Rao<br>Payment Gateway DEPENDS_ON Notification Service (tier 2); SRE-Operations team OWNS Notification Service; manager: Janet Williams |
| 8 | factual | — | — |
| 9 | factual | INC-2025-012 | INC-2025-012 -[AUTHORED_BY]-> SRE-Operations <-[AUTHORED_BY]- MAN-ENG-004<br>INC-2025-012 -[AUTHORED_BY]-> SRE-Operations <-[AUTHORED_BY]- MAN-ENG-006<br>INC-2025-012 -[AFFECTS]-> Ledger DB <-[GOVERNS]- POL-COMP-007<br>INC-2025-012 -[AUTHORED_BY]-> SRE-Operations <-[AUTHORED_BY]- SOP-OPS-014<br>INC-2025-012 -[RESOLVED_BY]-> Jeffrey Lawrence<br>INC-2025-012 -[AFFECTS]-> Ledger DB <-[DEPENDS_ON]- Billing Service<br>INC-2025-012 -[AFFECTS]-> Ledger DB<br>INC-2025-012 -[AUTHORED_BY]-> SRE-Operations<br>INC-2025-012 -[AFFECTS]-> Ledger DB -[HAS_COMPONENT]-> connection pool<br>INC-2025-012 -[AFFECTS]-> Ledger DB -[HAS_COMPONENT]-> primary instance |
| 10 | factual | — | — |
| 11 | factual | Ledger DB | Ledger DB -[HAS_COMPONENT]-> connection pool<br>Ledger DB -[HAS_COMPONENT]-> primary instance<br>Ledger DB -[HAS_COMPONENT]-> replica instance<br>Ledger DB <-[DEPENDS_ON]- Billing Service <-[AFFECTS]- INC-2025-012<br>Ledger DB <-[DEPENDS_ON]- Payment Gateway <-[AFFECTS]- INC-2025-031<br>Ledger DB -[DEPENDS_ON]-> Secrets Vault <-[MENTIONS]- MAN-ENG-001<br>Ledger DB -[DEPENDS_ON]-> Monitoring Stack <-[MENTIONS]- MAN-ENG-004<br>Ledger DB <-[GOVERNS]- POL-COMP-001 <-[RELATED_TO]- MAN-ENG-006<br>Ledger DB <-[DEPENDS_ON]- Data Warehouse <-[GOVERNS]- POL-COMP-001<br>Ledger DB -[DEPENDS_ON]-> Monitoring Stack <-[GOVERNS]- POL-COMP-003 |
| 12 | factual | Secrets Vault | Secrets Vault <-[DEPENDS_ON]- Auth Service -[HAS_COMPONENT]-> Token Issuer<br>Secrets Vault -[HAS_COMPONENT]-> Token signing keys<br>Secrets Vault <-[DEPENDS_ON]- Auth Service <-[AFFECTS]- INC-2025-003<br>Secrets Vault <-[DEPENDS_ON]- Payment Gateway <-[MENTIONS]- MAN-ENG-001<br>Secrets Vault <-[OWNS]- Identity-Access <-[AUTHORED_BY]- MAN-ENG-002<br>Secrets Vault <-[DEPENDS_ON]- Auth Service <-[GOVERNS]- POL-COMP-004<br>Secrets Vault <-[DEPENDS_ON]- Payment Gateway <-[GOVERNS]- POL-COMP-005<br>Secrets Vault <-[GOVERNS]- POL-COMP-004 <-[RELATED_TO]- SOP-OPS-009<br>Secrets Vault <-[OWNS]- Identity-Access -[OWNS]-> Auth Service<br>Secrets Vault <-[DEPENDS_ON]- Payment Gateway <-[DEPENDS_ON]- Billing Service |
| 13 | multi_hop | Ledger DB | INC-2025-031 (SEV1, 2025-08-11) AFFECTS Ledger DB: Payment latency spike<br>INC-2025-012 (SEV1, 2025-03-27) AFFECTS Ledger DB: Ledger DB primary failure |
| 14 | multi_hop | Kafka Cluster | INC-2025-044 (SEV2, 2025-11-18) AFFECTS Kafka Cluster: Audit log ingestion gap<br>INC-2025-036 (SEV3, 2025-09-03) AFFECTS Kafka Cluster: Notification delivery backlog<br>INC-2025-008 (SEV2, 2025-02-19) AFFECTS Kafka Cluster: Kafka consumer lag delays fraud scoring |
| 15 | multi_hop | Secrets Vault | POL-COMP-004 "Access Control Policy" GOVERNS Secrets Vault<br>POL-COMP-005 "Encryption and Key Management Policy" GOVERNS Secrets Vault |
| 16 | multi_hop | Payment Gateway | Payment Gateway DEPENDS_ON API Gateway (tier 1); SRE-Operations team OWNS API Gateway; manager: Janet Williams<br>Payment Gateway DEPENDS_ON Auth Service (tier 1); Identity-Access team OWNS Auth Service; manager: Christopher Davis<br>Payment Gateway DEPENDS_ON Kafka Cluster (tier 1); Data-Platform team OWNS Kafka Cluster; manager: Anthony Gonzalez<br>Payment Gateway DEPENDS_ON Ledger DB (tier 1); Data-Platform team OWNS Ledger DB; manager: Anthony Gonzalez<br>Payment Gateway DEPENDS_ON Secrets Vault (tier 1); Identity-Access team OWNS Secrets Vault; manager: Christopher Davis<br>Payment Gateway DEPENDS_ON Fraud Detection Engine (tier 2); Payments-Core team OWNS Fraud Detection Engine; manager: Priya Rao<br>Payment Gateway DEPENDS_ON Notification Service (tier 2); SRE-Operations team OWNS Notification Service; manager: Janet Williams |
| 17 | factual | SOP-SUP-008 | SOP-SUP-008 -[RELATED_TO]-> POL-COMP-001<br>SOP-SUP-008 -[RELATED_TO]-> POL-COMP-001 -[RELATED_TO]-> POL-COMP-002<br>SOP-SUP-008 -[RELATED_TO]-> POL-COMP-001 -[RELATED_TO]-> POL-COMP-003<br>SOP-SUP-008 -[MENTIONS]-> Fraud Detection Engine <-[GOVERNS]- POL-COMP-008<br>SOP-SUP-008 -[AUTHORED_BY]-> Customer-Support <-[AUTHORED_BY]- SOP-SUP-003<br>SOP-SUP-008 -[MENTIONS]-> Billing Service<br>SOP-SUP-008 -[MENTIONS]-> Fraud Detection Engine<br>SOP-SUP-008 -[AUTHORED_BY]-> Customer-Support<br>SOP-SUP-008 -[RELATED_TO]-> POL-COMP-002 <-[RELATED_TO]- INC-2025-003<br>SOP-SUP-008 -[MENTIONS]-> Fraud Detection Engine <-[AFFECTS]- INC-2025-008 |
| 18 | factual | INC-2025-044 | INC-2025-044 -[AUTHORED_BY]-> SRE-Operations <-[AUTHORED_BY]- MAN-ENG-004<br>INC-2025-044 -[AFFECTS]-> Auth Service <-[GOVERNS]- POL-COMP-003<br>INC-2025-044 -[AFFECTS]-> Auth Service <-[GOVERNS]- POL-COMP-004<br>INC-2025-044 -[AFFECTS]-> Kafka Cluster <-[GOVERNS]- POL-COMP-006<br>INC-2025-044 -[AUTHORED_BY]-> SRE-Operations <-[AUTHORED_BY]- SOP-OPS-011<br>INC-2025-044 -[RESOLVED_BY]-> Robin Ellis<br>INC-2025-044 -[AFFECTS]-> Auth Service -[DEPENDS_ON]-> Audit Log Store<br>INC-2025-044 -[AFFECTS]-> Auth Service<br>INC-2025-044 -[AFFECTS]-> Auth Service -[DEPENDS_ON]-> Kafka Cluster<br>INC-2025-044 -[AUTHORED_BY]-> SRE-Operations |
| 19 | factual | Ledger DB | Ledger DB -[HAS_COMPONENT]-> connection pool<br>Ledger DB -[HAS_COMPONENT]-> primary instance<br>Ledger DB -[HAS_COMPONENT]-> replica instance<br>Ledger DB <-[DEPENDS_ON]- Billing Service <-[AFFECTS]- INC-2025-012<br>Ledger DB <-[DEPENDS_ON]- Payment Gateway <-[AFFECTS]- INC-2025-031<br>Ledger DB -[DEPENDS_ON]-> Secrets Vault <-[MENTIONS]- MAN-ENG-001<br>Ledger DB -[DEPENDS_ON]-> Monitoring Stack <-[MENTIONS]- MAN-ENG-004<br>Ledger DB <-[GOVERNS]- POL-COMP-001 <-[RELATED_TO]- MAN-ENG-006<br>Ledger DB <-[DEPENDS_ON]- Data Warehouse <-[GOVERNS]- POL-COMP-001<br>Ledger DB -[DEPENDS_ON]-> Monitoring Stack <-[GOVERNS]- POL-COMP-003 |
| 20 | factual | Billing Service | POL-COMP-006 "Change Management Policy" GOVERNS Billing Service<br>POL-COMP-008 "Payment Card Data Handling Policy" GOVERNS Billing Service |

## Per query: dense only vs hybrid + rerank

| # | Query | Relevant | Dense R@5 / first | Hybrid+rerank R@5 / first | Hybrid+rerank top docs |
|---|---|---|---|---|---|
| 1 | What is the retention period for customer logs? | FAQ-004, FAQ-030, FAQ-046, POL-COMP-001 | 1.00 / 1 | 1.00 / 1 | FAQ-030, FAQ-004, FAQ-046, POL-COMP-001 |
| 2 | What are the steps for manual promotion during a Ledger DB failover? | SOP-OPS-014 | 1.00 / 1 | 1.00 / 1 | SOP-OPS-014, INC-2025-012 |
| 3 | Which refunds need approval from Finance? | SOP-SUP-003 | 1.00 / 1 | 1.00 / 1 | SOP-SUP-003 |
| 4 | How do I reset MFA for a customer who lost their device? | FAQ-001, FAQ-026, FAQ-034, FAQ-049, SOP-SUP-005 | 1.00 / 1 | 0.80 / 1 | SOP-SUP-005, FAQ-001, FAQ-026, FAQ-049 |
| 5 | What was the root cause of the payment latency spike? | INC-2025-031 | 1.00 / 1 | 1.00 / 1 | INC-2025-031, SOP-SUP-005, MAN-ENG-006, MAN-ENG-001 |
| 6 | How often are secrets in the Secrets Vault rotated? | SOP-OPS-009 | 1.00 / 1 | 1.00 / 1 | SOP-OPS-009, MAN-ENG-002, POL-COMP-005 |
| 7 | Which systems does the Payment Gateway depend on? | MAN-ENG-001 | 1.00 / 1 | 1.00 / 1 | MAN-ENG-001, POL-COMP-005, SOP-OPS-011, POL-COMP-006 |
| 8 | How far ahead of expiry must TLS certificates be renewed? | POL-COMP-005, SOP-OPS-018 | 1.00 / 1 | 1.00 / 1 | SOP-OPS-018, POL-COMP-005, MAN-ENG-006, INC-2025-022 |
| 9 | What happened in INC-2025-012? | INC-2025-012 | 1.00 / 3 | 1.00 / 1 | INC-2025-012, MAN-ENG-006, INC-2025-040 |
| 10 | How quickly must we respond to a chargeback notice? | SOP-SUP-008 | 1.00 / 1 | 1.00 / 1 | SOP-SUP-008 |
| 11 | When should I run ledgerctl promote --replica ledger-db-replica-1? | SOP-OPS-014 | 1.00 / 1 | 1.00 / 1 | SOP-OPS-014 |
| 12 | What does vaultctl rotate do and when is it used? | SOP-OPS-009 | 1.00 / 1 | 1.00 / 1 | SOP-OPS-009 |
| 13 | Which incidents affected the Ledger DB? | INC-2025-012, INC-2025-031 | 1.00 / 1 | 1.00 / 1 | INC-2025-012, MAN-ENG-006, SOP-OPS-014, INC-2025-031 |
| 14 | Which incidents involved the Kafka Cluster? | INC-2025-008, INC-2025-036, INC-2025-044 | 0.67 / 1 | 1.00 / 1 | INC-2025-008, SOP-OPS-007, POL-COMP-007, INC-2025-036, INC-2025-044 |
| 15 | Which policies govern the Secrets Vault? | POL-COMP-004, POL-COMP-005 | 1.00 / 1 | 1.00 / 1 | POL-COMP-005, INC-2025-003, POL-COMP-004, MAN-ENG-001, MAN-ENG-002 |
| 16 | Which teams own the systems Payment Gateway depends on? | MAN-ENG-001 | 1.00 / 1 | 1.00 / 1 | MAN-ENG-001, POL-COMP-005, SOP-OPS-011, POL-COMP-006 |
| 17 | What evidence should be gathered under SOP-SUP-008? | SOP-SUP-008 | 1.00 / 1 | 1.00 / 1 | SOP-SUP-008, SOP-SUP-003 |
| 18 | What follow-up actions came out of INC-2025-044? | INC-2025-044 | 1.00 / 2 | 1.00 / 3 | INC-2025-012, MAN-ENG-004, INC-2025-044, INC-2025-008, SOP-OPS-011 |
| 19 | If the primary ledger database dies and automatic failover doesn't kick in, how do we bring a replica online by hand? | INC-2025-012, SOP-OPS-014 | 1.00 / 1 | 1.00 / 1 | SOP-OPS-014, INC-2025-012 |
| 20 | Which policies apply to the Billing Service? | POL-COMP-006, POL-COMP-008 | 1.00 / 1 | 1.00 / 1 | POL-COMP-006, SOP-SUP-003, POL-COMP-008 |
