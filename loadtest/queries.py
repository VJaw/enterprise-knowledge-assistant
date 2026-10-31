"""Query sets for the latency benchmark (scripts/bench_latency.py) and the Locust users
(loadtest/locustfile.py).

Plain data, no app imports: Locust monkey-patches the standard library at import time and must never share a
process with the app stack (Phase 1 handoff).

`BENCH` has 10 questions per intent type (§B8.1 intents, plus the SQL path). `SLO_MS` maps each type to its
§B2.3 NFR-1 target: simple questions (single-doc factual, FAQ, chit-chat) < 3 s, agentic ones < 12 s, cache
hits < 300 ms.
"""

BENCH: dict[str, list[str]] = {
    "simple": [
        "What is the refund window?",
        "What is the retention period for customer logs?",
        "What is the session timeout for the Customer Portal?",
        "Which currencies does Acme Pay support?",
        "How long does it take for a refund to reach my bank account?",
        "At what consumer lag should the Kafka Cluster alert fire?",
        "How far ahead of expiry must API Gateway TLS certificates be renewed?",
        "How long does break-glass privileged access last?",
        "Which SOP covers MFA reset?",
        "What are the recovery time and recovery point objectives for the Ledger DB?",
    ],
    "multi_hop": [
        "Who manages the team that owns the system covered by SOP-OPS-014 (Database Failover)?",
        "Which team owns the system that SOP-OPS-009 (Secrets Rotation) covers, and who manages that team?",
        "Who is the manager of the team that owns the system handled by SOP-OPS-022 (Data Warehouse "
        "Backfill)?",
        "Who manages the team that owns the system covered by SOP-SUP-003 (Refund Escalation)?",
        "SOP-SUP-008 (Chargeback Handling) requires a risk score from one of our systems. Which team owns "
        "that system, and who manages the team?",
        "Which teams own the systems Payment Gateway depends on?",
        "Which team owns the system affected by INC-2025-022 (the expired TLS certificate), and who manages "
        "that team?",
        "Which policies govern the systems affected by incident INC-2025-012?",
        "Which systems depend on the Auth Service, and which teams own them?",
        "Which team owns the Fraud Detection Engine, and who manages that team?",
    ],
    "structured": [
        "How many SEV1 tickets are there in total?",
        "How many support tickets currently have the status 'open'?",
        "Which system has the most SEV1 tickets, and how many does it have?",
        "How many transactions were recorded in the EMEA region?",
        "How many employees are on the Data-Platform team?",
        "How many SEV2 tickets are there?",
        "How many transactions were recorded in the APAC region?",
        "How many transactions were made in GBP?",
        "Which product has the most tickets?",
        "How many tickets are currently in progress?",
    ],
    "analytical": [
        "Compare the refund escalation and chargeback handling SOPs: what are the approval thresholds and "
        "deadlines in each?",
        "What were the common root causes across the SEV1 incidents?",
        "Summarise the lessons learned from INC-2025-012 and INC-2025-031.",
        "Which incidents affected the Payment Gateway, and what actions were taken afterwards?",
        "Explain how a refund over $5,000 is processed end to end.",
        "What would be affected if the Kafka Cluster went down, and how is it monitored?",
        "Which system has the most SEV1 tickets, and what does its failover SOP say about manual promotion?",
        "What is the average transaction amount per region?",
        "How do the secrets rotation SOP and the access control policy differ on key rotation?",
        "What are the main steps in our incident response process, from detection to postmortem?",
    ],
    "external": [
        "What is the latest OpenSSL CVE?",
        "What is the latest stable release of the Python programming language?",
        "What is the latest version of Kubernetes?",
        "What is the current version of the PCI DSS standard?",
        "What is the latest stable version of PostgreSQL?",
        "What is the latest LTS release of Node.js?",
        "What do the latest NIST guidelines say about password rotation?",
        "What is the latest stable version of Redis?",
        "What is the latest version of Neo4j?",
        "What is the newest version of the TLS protocol?",
    ],
    "chit_chat": [
        "Hello! What can you help me with?",
        "Thanks, that was helpful!",
        "Good morning!",
        "Who are you?",
        "What kinds of questions can I ask you?",
        "Hi there",
        "Thank you very much",
        "Can you help me find information?",
        "Bye for now",
        "How are you today?",
    ],
}

# Replayed after the first pass: every one was answered (and cached) in that pass.
CACHE_REPLAY = BENCH["simple"]

SLO_MS = {"simple": 3000, "chit_chat": 3000, "multi_hop": 12000, "structured": 12000, "analytical": 12000,
          "external": 12000, "cache_hit": 300}

# Locust cache scenario (§B15: 70% repeated / paraphrased). Paraphrases from scripts/cache_threshold.py that
# hit at 0.92 with the specifics guard.
REPEATED = [
    "What is the refund window?",
    "What is the retention period for customer logs?",
    "How long are customer logs retained?",
    "What's the retention period of customer logs?",
    "Which SOP covers MFA reset?",
    "what sop covers mfa reset",
    "Which SOP is about resetting MFA?",
    "Who owns the Ledger DB?",
    "Who is the owner of the Ledger DB?",
    "How many SEV1 tickets are there in total?",
]
# Questions asked once each in a run (cache misses by construction).
NOVEL = [q for kind in ("simple", "structured") for q in BENCH[kind] if q not in REPEATED] + [
    "How long are audit logs in the Audit Log Store retained, and can they be changed?",
    "Who has to approve a refund of more than $5,000?",
    "How quickly must we respond to a chargeback notice?",
    "What caused the payment latency spike in INC-2025-031?",
    "Does Acme store my full card number?",
    "How do I rotate the secrets in the Secrets Vault?",
    "How should I report a suspected security incident?",
    "Where are API keys for the Payment Gateway stored?",
    "How often must passwords and access keys be rotated?",
    "Which policy covers legal holds on log retention?",
    "What happened in INC-2025-012?",
    "What are the steps for manual promotion during a Ledger DB failover?",
    "How quickly must an emergency secret rotation happen?",
    "What is Acme's parental leave policy?",
]
COMPLEX = BENCH["multi_hop"] + BENCH["analytical"][:4]
