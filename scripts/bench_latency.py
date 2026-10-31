"""Latency baseline (§B15): 10 sequential questions per intent type, p50 / p95 end to end and per stage.

Starts its own API (cache on, as deployed; the semantic cache is emptied first), asks every question in
loadtest/queries.py `BENCH` once with a fresh session, then replays the simple questions as cache hits. Stage
times come from the Langfuse span data of each request's trace (§B13.2); `Server-Timing` is kept as well.
Writes eval/reports/latency_<tag>.json and prints the tables used in PERFORMANCE_REPORT.md.

    python scripts/bench_latency.py --tag after            # stop `make run` first (Qdrant folder lock)
    python scripts/bench_latency.py --tag after --types simple,chit_chat --n 5
    python scripts/bench_latency.py --report-only --tag after

With 10 samples, p95 (nearest rank) is the slowest request.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.api_server import clear_cache, free_port, start_api, stop_api, tail, wait_ready  # noqa: E402
from loadtest.queries import BENCH, CACHE_REPLAY, SLO_MS  # noqa: E402

REPORTS = ROOT / "eval/reports"
# stage -> how it is read from a trace's observations (first = first occurrence, sum = all occurrences)
STAGES = {
    "input_rails": ("first", "guardrails.input"),
    "cache_lookup": ("first", "cache.lookup"),
    "router": ("first", "router"),
    "rerank": ("first", "rerank.cross_encoder"),
    "graph": ("first", "neo4j.traverse"),
    "agent_llm": ("sum", "agent.step_"),
    "tools": ("sum", "tool."),
    "synthesize": ("sum", "synthesize"),
    "groundedness": ("sum", "groundedness"),
    "output_rails": ("first", "guardrails.output"),
    "cache_write": ("first", "cache.write"),
    "agent": ("first", "agent"),
    "total": ("first", "chat"),
}
SHOWN = ["input_rails", "router", "retrieval", "retrieval_exposed", "rerank", "agent_llm", "tools",
         "groundedness", "output_rails", "total"]


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))] if s else 0.0


def ts(x: str) -> float:
    return datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp() * 1000


def parse_timing(header: str) -> dict[str, float]:
    out = {}
    for part in header.split(","):
        name, _, dur = part.strip().partition(";dur=")
        if dur:
            out[name] = float(dur)
    return out


# ------------------------------------------------------------------------------------------------- requests

def ask(client: httpx.Client, base: str, kind: str, question: str) -> dict:
    t0 = time.perf_counter()
    r = client.post(f"{base}/chat", json={"message": question, "session_id": f"bench-{uuid.uuid4()}"})
    wall = (time.perf_counter() - t0) * 1000
    row = {"kind": kind, "question": question, "status": r.status_code, "wall_ms": round(wall)}
    if r.status_code == 200:
        body = r.json()
        row |= {"latency_ms": body["latency_ms"], "trace_id": body["trace_id"], "model": body["model_used"],
                "cache_hit": body["cache_hit"], "tools": [t["name"] for t in body["tool_calls"]],
                "flags": body["guardrail_flags"], "cost_usd": body["usage"].get("est_cost_usd", 0.0),
                "server_timing": parse_timing(r.headers.get("server-timing", ""))}
    else:
        row["error"] = r.text[:300]
    return row


def run_requests(types: list[str], n: int, replay: bool) -> list[dict]:
    print(f"semantic cache: removed {clear_cache()} entries")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    rows = []
    with tempfile.NamedTemporaryFile("w+", prefix="bench_api_", suffix=".log", delete=False) as log:
        proc = start_api({"USE_CACHE": "true", "RATE_LIMIT": "1000/minute"}, port, log)
        try:
            print(f"starting API on :{port} (log {log.name})")
            print(f"  health: {wait_ready(proc, base)}")
            plan = [(k, q) for k in types for q in BENCH[k][:n]]
            if replay:
                plan += [("cache_hit", q) for q in CACHE_REPLAY[:n] if "simple" in types]
            with httpx.Client(timeout=90) as client:
                ask(client, base, "warmup", "What is the data classification policy?")  # first-call costs
                for i, (kind, q) in enumerate(plan, 1):
                    row = ask(client, base, kind, q)
                    rows.append(row)
                    tools = ",".join(row.get("tools", []))[:30]
                    print(f"{i:3d}/{len(plan)} {kind:<11}{row.get('latency_ms', -1):7d} ms  "
                          f"{row.get('model', row['status'])!s:<12} {tools:<30} {q[:55]}")
        except Exception:
            print(tail(log), file=sys.stderr)
            raise
        finally:
            stop_api(proc)  # SIGINT: the lifespan flushes Langfuse
    return rows


# -------------------------------------------------------------------------------------------- Langfuse spans

def observations(client: httpx.Client, trace_id: str) -> list[dict]:
    for attempt in range(12):  # ingestion is asynchronous; the public API is rate limited
        r = client.get("/api/public/v2/observations",
                       params={"traceId": trace_id, "fields": "core,basic", "limit": 100})
        if r.status_code == 429:
            time.sleep(min(60, 5 * 2 ** attempt))
            continue
        r.raise_for_status()
        obs = r.json()["data"]
        if any(o["name"] == "chat" and o.get("endTime") for o in obs):
            return obs
        time.sleep(5)
    return []


def stage_times(obs: list[dict]) -> dict[str, float]:
    spans = sorted(((o["name"], ts(o["startTime"]), ts(o["endTime"]), o["type"]) for o in obs
                    if o.get("endTime")), key=lambda s: s[1])
    out: dict[str, float] = {}
    for stage, (how, name) in STAGES.items():
        prefix = name.endswith((".", "_"))
        hits = [s for s in spans if (s[0].startswith(name) if prefix else s[0] == name)
                and not (name == "router" and s[3] != "CHAIN") and not (name == "groundedness" and
                                                                       s[3] != "GUARDRAIL")]
        if hits:
            out[stage] = round(sum(e - b for _, b, e, _ in hits) if how == "sum" else hits[0][2] - hits[0][1])
    # Initial retrieval: from the first retrieval span (the speculative prefetch, if any) to the end of the
    # first retrieval.hybrid. "Exposed" is the part that wasn't hidden behind the router.
    first = [s for s in spans if s[0] in ("retrieval.prefetch", "retrieval.hybrid")]
    hybrid = [s for s in spans if s[0] == "retrieval.hybrid"]
    router = [s for s in spans if s[0] == "router" and s[3] == "CHAIN"]
    if first and hybrid:
        start, end = first[0][1], hybrid[0][2]
        out["retrieval"] = round(end - start)
        out["retrieval_exposed"] = round(max(0.0, end - max(start, router[0][2] if router else start)))
    return out


def attach_stages(rows: list[dict]) -> None:
    from app.config import get_settings

    s = get_settings()
    auth = (s.langfuse_public_key.get_secret_value(), s.langfuse_secret_key.get_secret_value())
    time.sleep(10)
    with httpx.Client(base_url=s.langfuse_host, auth=auth, timeout=30) as client:
        for row in rows:
            if row.get("trace_id") and not row.get("stages"):
                row["stages"] = stage_times(observations(client, row["trace_id"]))
                time.sleep(1)
    missing = sum(1 for r in rows if r.get("trace_id") and not r.get("stages"))
    print(f"Langfuse stage data for {len(rows) - missing}/{len(rows)} requests")


# -------------------------------------------------------------------------------------------------- summary

def summarise(rows: list[dict]) -> dict:
    out = {}
    for kind in [*BENCH, "cache_hit"]:
        rs = [r for r in rows if r["kind"] == kind and r["status"] == 200]
        if not rs:
            continue
        lat = [r["latency_ms"] for r in rs]
        stages = {}
        for st in SHOWN:
            vals = [r["stages"][st] for r in rs if st in r.get("stages", {})]
            if vals:
                stages[st] = {"p50": pct(vals, 0.5), "p95": pct(vals, 0.95),
                              "mean": round(statistics.mean(vals))}
        models = sorted({r["model"] for r in rs})
        out[kind] = {"n": len(rs), "errors": sum(r["kind"] == kind and r["status"] != 200 for r in rows),
                     "p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "max": max(lat), "slo_ms": SLO_MS[kind],
                     "meets_slo": pct(lat, 0.95) < SLO_MS[kind],
                     "within_slo": sum(v < SLO_MS[kind] for v in lat),
                     "models": {m: sum(r["model"] == m for r in rs) for m in models},
                     "cache_hits": sum(r["cache_hit"] for r in rs),
                     "mean_cost_usd": round(statistics.mean(r["cost_usd"] for r in rs), 5),
                     "stages_ms": stages}
    return out


def print_tables(summary: dict) -> None:
    print("\n| Type | n | p50 ms | p95 ms | SLO | Within SLO | Models |")
    print("|---|---|---|---|---|---|---|")
    for kind, s in summary.items():
        models = ", ".join(f"{m} {c}" for m, c in s["models"].items())
        print(f"| {kind} | {s['n']} | {s['p50']:,.0f} | {s['p95']:,.0f} | < {s['slo_ms']:,} ms "
              f"{'✅' if s['meets_slo'] else '❌'} | {s['within_slo']}/{s['n']} | {models} |")
    print("\nStage p50 / p95 (ms, from Langfuse spans)\n")
    print("| Type | " + " | ".join(SHOWN) + " |")
    print("|---" * (len(SHOWN) + 1) + "|")
    for kind, s in summary.items():
        cells = [f"{s['stages_ms'][st]['p50']:,.0f} / {s['stages_ms'][st]['p95']:,.0f}"
                 if st in s["stages_ms"] else "–" for st in SHOWN]
        print(f"| {kind} | " + " | ".join(cells) + " |")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", default="latest")
    ap.add_argument("--types", default=",".join(BENCH))
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--no-replay", action="store_true", help="skip the cache-hit replay")
    ap.add_argument("--report-only", action="store_true", help="re-summarise a saved run")
    ap.add_argument("--stages-only", action="store_true", help="fetch missing Langfuse stages of a saved run")
    args = ap.parse_args()
    out = REPORTS / f"latency_{args.tag}.json"
    if args.report_only or args.stages_only:
        rows = json.loads(out.read_text())["rows"]
    else:
        rows = run_requests(args.types.split(","), args.n, not args.no_replay)
        out.write_text(json.dumps({"tag": args.tag, "rows": rows}, indent=2) + "\n")  # before the slow part
    if not args.report_only:
        attach_stages(rows)
    summary = summarise(rows)
    cost = sum(r.get("cost_usd", 0.0) for r in rows)
    out.write_text(json.dumps({"tag": args.tag, "date": datetime.now().isoformat(timespec="seconds"),
                               "total_cost_usd": round(cost, 4), "summary": summary, "rows": rows},
                              indent=2) + "\n")
    print_tables(summary)
    print(f"\nestimated OpenAI cost ${cost:.3f}; wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
