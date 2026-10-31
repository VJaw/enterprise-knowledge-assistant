"""Guardrail latency and false positives on benign questions (Phase 8 gate: overhead p50 < 400 ms).

Sends the golden-set questions (all benign) to a running API one at a time, reads the per-stage times from the
`Server-Timing` header, and reports p50 / p95 per rail plus the overhead the rails add on the critical path
(input rails + untrusted-content scan + output rails + any wait for the LLM self-check after the agent).
Any GUARDRAIL_BLOCKED on these questions is a false positive. Writes eval/reports/guardrail_latency.json.

    make run   # in another shell
    python scripts/bench_guardrails.py [--url http://localhost:7860] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import statistics
import uuid
from pathlib import Path

import httpx

GOLDEN = Path("eval/datasets/golden.jsonl")
OUT = Path("eval/reports/guardrail_latency.json")
STAGES = ["input", "llm_guard_injection", "llm_guard_toxicity_input", "presidio_mask",
          "self_check_before_agent", "untrusted", "self_check_input", "self_check_wait", "output",
          "citations_enforce", "presidio_output", "llm_guard_output", "prescan", "groundedness", "guardrails",
          "agent", "total"]


def parse_timing(header: str) -> dict[str, float]:
    out = {}
    for part in header.split(","):
        name, _, dur = part.strip().partition(";dur=")
        if dur:
            out[name] = float(dur)
    return out


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))] if s else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:7860")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    rows = [json.loads(line) for line in GOLDEN.open()][: args.limit]

    results = []
    with httpx.Client(timeout=60) as client:
        for i, row in enumerate(rows, 1):
            r = client.post(f"{args.url}/chat", json={"message": row["user_input"],
                                                       "session_id": f"bench-{uuid.uuid4()}"})
            r.raise_for_status()
            body, timing = r.json(), parse_timing(r.headers.get("server-timing", ""))
            flags = body["guardrail_flags"]
            results.append({"question": row["user_input"], "query_type": row["query_type"], "flags": flags,
                            "blocked": "GUARDRAIL_BLOCKED" in flags, "timings": timing})
            print(f"{i:2d}/{len(rows)} overhead {timing.get('guardrails', 0):6.0f} ms  total "
                  f"{timing.get('total', 0):6.0f} ms  {body['guardrail_flags']}  {row['user_input'][:60]}")

    summary = {}
    for stage in STAGES:
        vals = [r["timings"][stage] for r in results if stage in r["timings"]]
        if vals:
            summary[stage] = {"n": len(vals), "p50": pct(vals, 0.5), "p95": pct(vals, 0.95),
                              "mean": round(statistics.mean(vals), 1)}
    blocked = [r["question"] for r in results if r["blocked"]]
    suspected = sum("injection_suspected" in r["flags"] for r in results)
    report = {"n": len(results), "false_positives": blocked, "injection_suspected": suspected,
              "stages_ms": summary, "rows": results}
    OUT.write_text(json.dumps(report, indent=2) + "\n")

    print(f"\n{'stage':<26}{'n':>4}{'p50':>9}{'p95':>9}{'mean':>9}")
    for stage, s in summary.items():
        print(f"{stage:<26}{s['n']:>4}{s['p50']:>9.0f}{s['p95']:>9.0f}{s['mean']:>9.0f}")
    print(f"\nfalse positives (blocked benign): {len(blocked)}/{len(results)} {blocked}")
    print(f"DeBERTa-suspected (self-check ran before the agent): {suspected}/{len(results)}")
    p50 = summary.get("guardrails", {}).get("p50", 0.0)
    print(f"guardrail overhead p50 = {p50:.0f} ms -> {'PASS' if p50 < 400 else 'FAIL'} (< 400 ms)")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
