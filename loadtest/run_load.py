"""Run the §B15 load scenarios against a fresh API process and summarise them.

    python loadtest/run_load.py cache_off cache_on ramp [soak]   # stop `make run` first (Qdrant lock)
    python loadtest/run_load.py --report-only cache_off cache_on ramp

Scenarios (each empties the semantic cache, starts the API with a high rate limit, sends one warm-up request,
runs the Locust users headless and stops the API):
- cache_off / cache_on: 3 CacheMixUsers (70% repeated or paraphrased questions) for 2 min, cache off vs on.
- ramp: SimpleUser / ComplexUser / RepeatUser (5:2:3) ramped from 1 to 10 users over 3 min, then 1 min at 10.
- soak: the same mix, 5 users for 5 min, watching the API's memory.

The API's resident memory is sampled every 5 s. Outputs: eval/reports/load/<scenario>_*.csv (Locust),
<scenario>.jsonl (one row per request) and <scenario>.json (summary). `--stages N` also pulls the Langfuse
span data of N uncached requests per scenario for the stage breakdown.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.api_server import clear_cache, free_port, start_api, stop_api, tail, wait_ready  # noqa: E402
from scripts.bench_latency import STAGES, pct  # noqa: E402

OUT = ROOT / "eval/reports/load"
MIX = ["SimpleUser", "ComplexUser", "RepeatUser"]
SCENARIOS = {
    "cache_off": {"flags": {"USE_CACHE": "false"}, "users": ["CacheMixUser"], "u": 3, "r": 3, "t": "2m"},
    "cache_on": {"flags": {"USE_CACHE": "true"}, "users": ["CacheMixUser"], "u": 3, "r": 3, "t": "2m"},
    "ramp": {"flags": {"USE_CACHE": "true"}, "users": MIX, "u": 10, "r": 0.05, "t": "4m"},
    "soak": {"flags": {"USE_CACHE": "true"}, "users": MIX, "u": 5, "r": 5, "t": "5m"},
}


class MemorySampler(threading.Thread):
    def __init__(self, pid: int, every_s: float = 5.0):
        super().__init__(daemon=True)
        self.proc, self.every_s, self.samples = psutil.Process(pid), every_s, []
        self.stopped = threading.Event()

    def run(self) -> None:
        while not self.stopped.is_set():
            try:
                self.samples.append((round(time.time(), 1), round(self.proc.memory_info().rss / 2**20)))
            except psutil.Error:
                return
            self.stopped.wait(self.every_s)


def run_scenario(name: str) -> None:
    sc = SCENARIOS[name]
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {name}: semantic cache: removed {clear_cache()} entries")
    log_path = OUT / f"{name}.jsonl"
    log_path.unlink(missing_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.NamedTemporaryFile("w+", prefix=f"load_{name}_", suffix=".log", delete=False) as log:
        proc = start_api(sc["flags"] | {"RATE_LIMIT": "1000/minute"}, port, log)
        try:
            print(f"starting API on :{port} (log {log.name})")
            print(f"  health: {wait_ready(proc, base)}")
            warmup = {"message": "What is the data classification policy?"}
            httpx.post(f"{base}/chat", json=warmup, timeout=60)
            mem = MemorySampler(proc.pid)
            mem.start()
            cmd = [str(ROOT / ".venv/bin/locust"), "-f", "loadtest/locustfile.py", "--headless",
                   "-u", str(sc["u"]), "-r", str(sc["r"]), "-t", sc["t"], "--host", base,
                   "--csv", str(OUT / name), "--csv-full-history", "--only-summary", "--stop-timeout", "60",
                   *sc["users"]]
            t0 = time.time()
            subprocess.run(cmd, cwd=ROOT, env=os.environ | {"LOAD_LOG": str(log_path)}, check=False)
            t1 = time.time()
            mem.stopped.set()
            mem.join()
        except Exception:
            print(tail(log), file=sys.stderr)
            raise
        finally:
            stop_api(proc)
    meta = {"scenario": name, **{k: sc[k] for k in ("flags", "users", "u", "r", "t")}, "start": t0, "end": t1,
            "memory_mb": mem.samples}
    (OUT / f"{name}.meta.json").write_text(json.dumps(meta) + "\n")


def latency(rows: list[dict]) -> dict:
    lat = [r["latency_ms"] for r in rows]
    return {"n": len(lat), "p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "max": max(lat)} if lat else {"n": 0}


def summarise(name: str, stages: int) -> dict:
    meta = json.loads((OUT / f"{name}.meta.json").read_text())
    rows = [json.loads(line) for line in (OUT / f"{name}.jsonl").open()]
    ok = [r for r in rows if r["status"] == 200]
    duration = meta["end"] - meta["start"]
    last = [r for r in rows if r["t"] >= meta["end"] - 60]
    hits = [r for r in ok if r["cache_hit"]]
    misses = [r for r in ok if not r["cache_hit"] and not r.get("blocked")]
    mem = [m for _, m in meta["memory_mb"]]
    after_warm = [m for t, m in meta["memory_mb"] if t >= meta["start"] + 60] or mem
    s = {
        "scenario": name, "users": meta["u"], "duration_s": round(duration), "requests": len(rows),
        "failures": len(rows) - len(ok), "error_rate": round((len(rows) - len(ok)) / max(1, len(rows)), 4),
        "rps": round(len(rows) / duration, 2), "rps_last_60s": round(len(last) / 60, 2),
        "latency": latency(ok), "by_kind": {k: latency([r for r in ok if r["kind"] == k])
                                            for k in sorted({r["kind"] for r in ok})},
        "cache_hits": len(hits), "hit_rate": round(len(hits) / max(1, len(ok)), 3),
        "hit_latency": latency(hits), "miss_latency": latency(misses),
        "models": {m: sum(r["model"] == m for r in ok) for m in sorted({r["model"] for r in ok})},
        "cost_usd": round(sum(r.get("cost_usd", 0.0) for r in ok), 4),
        "memory_mb": {"start": mem[0] if mem else None, "after_1min": after_warm[0],
                      "end": mem[-1] if mem else None, "max": max(mem) if mem else None,
                      "growth_after_1min": round(mem[-1] / after_warm[0] - 1, 3) if mem else None},
    }
    if hits and misses:
        s["speedup_p50"] = round(s["miss_latency"]["p50"] / max(1, s["hit_latency"]["p50"]), 1)
    if stages:
        s["stages"] = stage_breakdown(misses, stages)
    (OUT / f"{name}.json").write_text(json.dumps(s, indent=2) + "\n")
    return s


def stage_breakdown(rows: list[dict], n: int) -> dict:
    """Mean per-stage time over a sample of uncached requests, from their Langfuse traces."""
    from app.config import get_settings
    from scripts.bench_latency import observations, stage_times

    sample = [r for r in rows if r.get("trace_id")][:: max(1, len(rows) // n)][:n]
    st = get_settings()
    auth = (st.langfuse_public_key.get_secret_value(), st.langfuse_secret_key.get_secret_value())
    per = []
    with httpx.Client(base_url=st.langfuse_host, auth=auth, timeout=30) as client:
        for r in sample:
            if stages := stage_times(observations(client, r["trace_id"])):
                per.append(stages)
    keys = [k for k in [*STAGES, "retrieval", "retrieval_exposed"] if any(k in p for p in per)]
    return {"n": len(per), "mean_ms": {k: round(statistics.mean(p.get(k, 0) for p in per)) for k in keys}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scenarios", nargs="+", choices=list(SCENARIOS))
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--stages", type=int, default=0, help="Langfuse stage breakdown over N uncached requests")
    args = ap.parse_args()
    for name in args.scenarios:
        if not args.report_only:
            run_scenario(name)
        s = summarise(name, args.stages)
        print(json.dumps({k: v for k, v in s.items() if k != "by_kind"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
