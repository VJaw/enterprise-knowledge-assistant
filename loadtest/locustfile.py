"""Locust users for the load tests (§B15). Run through loadtest/run_load.py, which starts the API and picks
the users per scenario, or by hand against a running API:

    locust -f loadtest/locustfile.py --headless -u 10 -r 0.05 -t 4m --host http://localhost:7860 \
        SimpleUser ComplexUser RepeatUser

- SimpleUser: single-doc factual / FAQ / SQL-count questions (the < 3 s class), each asked once per run.
- ComplexUser: multi-hop and analytical questions (gpt-4o, tool loops; the < 12 s class).
- RepeatUser: a small set of repeated questions and paraphrases (what the semantic cache is for).
- CacheMixUser: the cache scenario, 70% repeated / paraphrased and 30% new questions.

Every request uses a new session id: the rate limit is per session (30/min), and a shared session would feed
earlier answers into the router's rewriting. Each response's latency, cache hit, model and kind go to the
JSONL file named by LOAD_LOG (when set) for loadtest/run_load.py to summarise.

Never import the app here: Locust monkey-patches the standard library with gevent at import time (Phase 1).
"""
from __future__ import annotations

import itertools
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path

from locust import HttpUser, between, task

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loadtest.queries import COMPLEX, NOVEL, REPEATED  # noqa: E402

LOG = open(os.environ["LOAD_LOG"], "a", buffering=1) if os.environ.get("LOAD_LOG") else None  # noqa: SIM115
_novel = itertools.cycle(random.Random(42).sample(NOVEL, len(NOVEL)))  # shared by all users of a run
_complex = itertools.cycle(random.Random(7).sample(COMPLEX, len(COMPLEX)))


class ChatUser(HttpUser):
    abstract = True
    wait_time = between(1, 3)  # think time

    def ask(self, kind: str, question: str) -> None:
        t0 = time.time()
        with self.client.post("/chat", json={"message": question, "session_id": f"load-{uuid.uuid4()}"},
                              name=f"/chat [{kind}]", catch_response=True, timeout=60) as r:
            row = {"t": round(t0, 3), "kind": kind, "question": question, "status": r.status_code,
                   "wall_ms": round((time.time() - t0) * 1000)}
            if r.status_code == 200:
                body = r.json()
                row |= {"latency_ms": body["latency_ms"], "cache_hit": body["cache_hit"],
                        "model": body["model_used"], "cost_usd": body["usage"].get("est_cost_usd", 0.0),
                        "trace_id": body["trace_id"],
                        "blocked": "GUARDRAIL_BLOCKED" in body["guardrail_flags"]}
                r.success()
            else:
                r.failure(f"HTTP {r.status_code}")
            if LOG:
                LOG.write(json.dumps(row) + "\n")


class SimpleUser(ChatUser):
    weight = 5

    @task
    def simple(self):
        self.ask("simple", next(_novel))


class ComplexUser(ChatUser):
    weight = 2

    @task
    def complex(self):
        self.ask("complex", next(_complex))


class RepeatUser(ChatUser):
    weight = 3

    @task
    def repeat(self):
        self.ask("repeat", random.choice(REPEATED))


class CacheMixUser(ChatUser):
    @task(7)
    def repeat(self):
        self.ask("repeat", random.choice(REPEATED))

    @task(3)
    def novel(self):
        self.ask("novel", next(_novel))
