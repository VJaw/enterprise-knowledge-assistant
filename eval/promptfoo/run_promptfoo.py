"""Run the Promptfoo regression suite (§B14.1) against a fresh API process.

    python eval/promptfoo/run_promptfoo.py            # make eval
    python eval/promptfoo/run_promptfoo.py --keep-cache

The suite runs end to end with the semantic cache **on** (one case checks a cache hit on a repeated question),
so earlier runs would otherwise answer most cases from the cache: no tool calls, no agent latency. The
runner therefore empties the `semantic_cache` collection first, then serves the API on :7860 (the port the
config targets) with a high rate limit, runs `npx promptfoo@latest eval`, and stops the API. Results ->
eval/reports/promptfoo_results.json. Stop `make run` first (embedded Qdrant takes a folder lock).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.api_server import clear_cache, start_api, stop_api, tail, wait_ready  # noqa: E402

CONFIG = ROOT / "eval/promptfoo/promptfooconfig.yaml"
PORT = 7860


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep-cache", action="store_true", help="don't empty the semantic cache first")
    args, extra = ap.parse_known_args()  # anything else goes to promptfoo (e.g. --filter-pattern)
    if not args.keep_cache:
        print(f"semantic cache: removed {clear_cache()} entries")
    flags = {"USE_CACHE": "true", "RATE_LIMIT": "1000/minute"}
    base = f"http://127.0.0.1:{PORT}"
    with tempfile.NamedTemporaryFile("w+", prefix="promptfoo_api_", suffix=".log", delete=False) as log:
        proc = start_api(flags, PORT, log)
        try:
            print(f"starting API on :{PORT} (log {log.name})")
            print(f"  health: {wait_ready(proc, base)}")
            env = os.environ | {"PROMPTFOO_DISABLE_TELEMETRY": "1"}
            cmd = ["npx", "-y", "promptfoo@latest", "eval", "-c", str(CONFIG.relative_to(ROOT)), "--no-cache",
                   *extra]
            return subprocess.run(cmd, cwd=ROOT, env=env).returncode
        except Exception:
            print(tail(log), file=sys.stderr)
            print("If Qdrant is locked, stop the running API (`make run`) and retry.", file=sys.stderr)
            raise
        finally:
            stop_api(proc)


if __name__ == "__main__":
    sys.exit(main())
