"""Spot-check Langfuse traces (§B13): the hierarchy each kind of request should have, cost per model, and
that no raw PII appears in any logged input, output or metadata.

    PYTHONPATH=. python scripts/check_traces.py <trace_id>[:kind] ...

kind is one of agent (default), cache_hit, blocked. PII = the planted fixture values (data/fixtures.json),
the gate's jane@acme.com, plus any email / phone / card / SSN pattern outside a <PLACEHOLDER>.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

from app.config import get_settings

EXPECTED = {
    "agent": {"chat", "guardrails.input", "cache.lookup", "agent", "router", "retrieval.hybrid",
              "qdrant.dense", "bm25", "rerank.cross_encoder", "fusion.rrf", "groundedness",
              "guardrails.output",
              "citations.enforce", "presidio.output", "llm_guard.toxicity"},
    "cache_hit": {"chat", "guardrails.input", "cache.lookup", "guardrails.output", "citations.enforce",
                  "presidio.output", "llm_guard.toxicity"},
    "blocked": {"chat", "guardrails.input", "llm_guard.injection"},
}
FORBIDDEN = {"cache_hit": {"agent", "router", "groundedness_judge", "synthesize"},
             "blocked": {"agent", "router", "cache.lookup"}}
PII_PATTERNS = [re.compile(p) for p in (
    r"[\w.+-]+@[\w-]+\.[\w.]+",  # email
    r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b",  # card
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
    r"\(\d{3}\)\s?\d{3}-\d{4}|\b\d{3}[-.]\d{3}[-.]\d{4}\b",  # US phone
)]


def fixture_values() -> set[str]:
    values = {"jane@acme.com"}
    data = json.loads(Path("data/fixtures.json").read_text())

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, str) and re.search(r"email|phone|card|ssn|ip", k, re.I):
                    values.add(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return {v for v in values if len(v) > 5}


def observations(client: httpx.Client, trace_id: str) -> list[dict]:
    r = client.get("/api/public/v2/observations",
                   params={"traceId": trace_id, "fields": "core,basic,io,model,usage", "limit": 100})
    r.raise_for_status()
    return r.json()["data"]


def check(client, trace_id: str, kind: str, pii: set[str]) -> bool:
    obs = []
    for _ in range(6):  # ingestion is asynchronous
        obs = observations(client, trace_id)
        if obs and "chat" in {o["name"] for o in obs}:
            break
        time.sleep(5)
    names = {o["name"] for o in obs}
    missing = EXPECTED[kind] - names
    extra = FORBIDDEN.get(kind, set()) & names
    blob = json.dumps([{k: o.get(k) for k in ("input", "output", "metadata")} for o in obs])
    unmasked = re.sub(r"<[A-Z_]+_\d+>", "", blob)
    leaks = sorted({v for v in pii if v in blob} |
                   {m.group(0) for p in PII_PATTERNS for m in p.finditer(unmasked)
                    if not m.group(0).endswith((".py", ".md", ".com/", ".json"))})
    gens = [o for o in obs if o.get("type") == "GENERATION"]
    cost = sum(float(o.get("totalCost") or 0) for o in gens)
    models = sorted({o.get("model") or "?" for o in gens})
    ok = not missing and not extra and not leaks
    print(f"{'PASS' if ok else 'FAIL'} {trace_id} [{kind}] {len(obs)} observations, {len(gens)} generations "
          f"{models}, cost ${cost:.5f}")
    if missing:
        print(f"   missing spans: {sorted(missing)}")
    if extra:
        print(f"   unexpected spans: {sorted(extra)}")
    if leaks:
        print(f"   PII found: {leaks}")
    return ok


def main(args: list[str]) -> int:
    s = get_settings()
    auth = (s.langfuse_public_key.get_secret_value(), s.langfuse_secret_key.get_secret_value())
    pii = fixture_values()
    with httpx.Client(base_url=s.langfuse_host, auth=auth, timeout=30) as client:
        results = [check(client, *(a.split(":") + ["agent"])[:2], pii) for a in args]
    print(f"\n{sum(results)}/{len(results)} traces pass")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
