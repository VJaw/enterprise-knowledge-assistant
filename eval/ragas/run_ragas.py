"""RAGAS evaluation (§B14.2): golden set -> /chat -> faithfulness, answer relevancy, context precision/recall.

    python eval/ragas/run_ragas.py --config A     # A dense | B +BM25 | C +graph | D full (+rerank)
    python eval/ragas/run_ragas.py --report-only  # rebuild RAGAS_REPORT.md from the saved results

Each run starts its own API process (`uvicorn app.main:app`) with the config's feature flags in the
environment, calls `/chat` once per golden row (a fresh session per row, so no history leaks between
questions), and stops it. Embedded Qdrant takes a folder lock, so stop `make run` first.
`retrieved_contexts` are the citation snippets (D-012): full chunk text for documents, the fact sentence
for graph citations.

Scoring uses the RAGAS 0.4 metric collections with gpt-4o-mini (and text-embedding-3-small for answer
relevancy's question similarity). Rules for rows RAGAS can't score as is (D-018):
- answerable row, no citations: context precision and recall are 0 (nothing relevant was retrieved);
  faithfulness is 0 if the answer still makes claims, n/a if it abstained ("I don't know").
- unanswerable row: the context metrics and answer relevancy are n/a; the row is scored on whether the
  assistant abstained (correct abstention), and on faithfulness if it answered anyway.
Results -> eval/reports/ragas_<config>.json; the generated part of eval/reports/RAGAS_REPORT.md is rebuilt
from every saved config, and the hand-written notes outside the markers are kept.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402

GOLDEN = ROOT / "eval/datasets/golden.jsonl"
REPORTS = ROOT / "eval/reports"
REPORT = REPORTS / "RAGAS_REPORT.md"
BEGIN, END = "<!-- BEGIN GENERATED (run_ragas.py) -->", "<!-- END GENERATED -->"

CONFIGS = {  # §B14.2 ablations
    "A": {"label": "A: baseline (dense)", "USE_BM25": "false", "USE_GRAPH": "false", "USE_RERANK": "false"},
    "B": {"label": "B: + keyword (BM25)", "USE_BM25": "true", "USE_GRAPH": "false", "USE_RERANK": "false"},
    "C": {"label": "C: + graph", "USE_BM25": "true", "USE_GRAPH": "true", "USE_RERANK": "false"},
    "D": {"label": "D: full (+ rerank)", "USE_BM25": "true", "USE_GRAPH": "true", "USE_RERANK": "true"},
}
METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
TARGETS = {"faithfulness": 0.85, "answer_relevancy": 0.85, "context_precision": 0.75, "context_recall": 0.80}
TYPES = ["factual", "multi_hop", "structured", "faq", "unanswerable"]
ABSTAIN = re.compile(r"\bI (?:don'?t|do not) know\b", re.I)
NAN = float("nan")


# --------------------------------------------------------------------------- API process

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_api(config: str, port: int, log) -> subprocess.Popen:
    flags = {k: v for k, v in CONFIGS[config].items() if k.startswith("USE_")}
    env = os.environ | flags | {"USE_CACHE": "false"}  # measure retrieval + generation, never a cache hit
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"]
    return subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)


def wait_ready(proc: subprocess.Popen, base: str, timeout_s: float = 300) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"API exited with code {proc.returncode} during startup")
        try:
            return httpx.get(f"{base}/health", timeout=15).json()  # 503 still means it is up
        except httpx.HTTPError:
            time.sleep(2)
    raise RuntimeError("API did not start in time")


def stop_api(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)  # graceful: the lifespan flushes Langfuse
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def ask_all(rows: list[dict], base: str, config: str) -> list[dict]:
    out = []
    with httpx.Client(base_url=base, timeout=120) as client:
        for i, row in enumerate(rows, 1):
            sid = f"ragas-{config}-{i:02d}-{uuid.uuid4().hex[:8]}"
            r = client.post("/chat", json={"message": row["user_input"], "session_id": sid})
            r.raise_for_status()
            body = r.json()
            out.append({"answer": body["answer"], "citations": body["citations"],
                        "flags": body["guardrail_flags"], "latency_ms": body["latency_ms"],
                        "usage": body["usage"], "trace_id": body["trace_id"]})
            print(f"  {i:>2}/{len(rows)} {row['query_type']:<12} {body['latency_ms']:>5} ms "
                  f"{len(body['citations'])} cit  {body['answer'][:70]!r}")
    return out


# --------------------------------------------------------------------------- scoring

def make_metrics():
    from openai import AsyncOpenAI
    from ragas.embeddings import OpenAIEmbeddings
    from ragas.llms import llm_factory
    from ragas.metrics.collections import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness

    s = get_settings()
    client = AsyncOpenAI(api_key=s.openai_api_key.get_secret_value() if s.openai_api_key else None)
    llm = llm_factory(s.model_fast, client=client, temperature=0)
    emb = OpenAIEmbeddings(client=client, model="text-embedding-3-small")
    return {"faithfulness": Faithfulness(llm=llm),
            "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=emb),
            "context_precision": ContextPrecision(llm=llm), "context_recall": ContextRecall(llm=llm)}


async def score_row(m: dict, row: dict, res: dict, errors: list[str]) -> dict[str, float]:
    q, ref, answer = row["user_input"], row["reference"], res["response"]
    contexts = [c for c in res["retrieved_contexts"] if c]
    abstained = bool(ABSTAIN.search(answer))
    unanswerable = row["query_type"] == "unanswerable"

    async def run(name: str, **kw) -> float:
        try:
            return float((await m[name].ascore(**kw)).value)
        except Exception as e:  # noqa: BLE001 - one bad judge call shouldn't sink the run
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
            return NAN

    async def nan() -> float:
        return NAN

    async def const(v: float) -> float:
        return v

    if contexts:
        faith = run("faithfulness", user_input=q, response=answer, retrieved_contexts=contexts)
    else:
        faith = nan() if abstained else const(0.0)
    if unanswerable:
        relevancy, precision, recall = nan(), nan(), nan()
    else:
        relevancy = run("answer_relevancy", user_input=q, response=answer)
        if contexts:
            precision = run("context_precision", user_input=q, reference=ref, retrieved_contexts=contexts)
            recall = run("context_recall", user_input=q, reference=ref, retrieved_contexts=contexts)
        else:
            precision, recall = const(0.0), const(0.0)
    values = await asyncio.gather(faith, relevancy, precision, recall)
    return dict(zip(METRICS, values, strict=True))


async def score_all(rows: list[dict], results: list[dict], concurrency: int = 4) -> None:
    m = make_metrics()
    sem = asyncio.Semaphore(concurrency)

    async def one(row: dict, res: dict) -> None:
        async with sem:
            res["errors"] = []
            res["scores"] = await score_row(m, row, res, res["errors"])

    await asyncio.gather(*(one(r, res) for r, res in zip(rows, results, strict=True)))


# --------------------------------------------------------------------------- aggregation + report

def mean(xs: list[float]) -> float:
    xs = [x for x in xs if not math.isnan(x)]
    return statistics.fmean(xs) if xs else NAN


def summarise(rows: list[dict]) -> dict:
    def block(rs: list[dict]) -> dict:
        answerable = [r for r in rs if r["query_type"] != "unanswerable"]
        unanswerable = [r for r in rs if r["query_type"] == "unanswerable"]
        return {
            "n": len(rs),
            **{k: mean([r["scores"][k] for r in rs]) for k in METRICS},
            "false_abstention": mean([float(r["abstained"]) for r in answerable]),
            "correct_abstention": mean([float(r["abstained"]) for r in unanswerable]),
            "no_citations": sum(1 for r in rs if not r["retrieved_contexts"]),
        }

    out = {"overall": block(rows)}
    for t in TYPES:
        out[t] = block([r for r in rows if r["query_type"] == t])
    out["latency_ms_p50"] = statistics.median(r["latency_ms"] for r in rows)
    out["cost_usd"] = round(sum(r["usage"].get("est_cost_usd", 0.0) for r in rows), 5)
    return out


def fmt(x: float) -> str:
    return "n/a" if x is None or math.isnan(x) else f"{x:.3f}"


def load_results() -> dict[str, dict]:
    return {c: json.loads(p.read_text()) for c in CONFIGS if (p := REPORTS / f"ragas_{c}.json").exists()}


def generated_section(results: dict[str, dict]) -> str:
    cfgs = list(results)
    first = next(iter(results.values()))
    lines = [BEGIN, "",
             f"_Generated {date.today().isoformat()} by `eval/ragas/run_ragas.py` from "
             f"{', '.join(f'`ragas_{c}.json`' for c in cfgs)}. Golden set: {first['n_rows']} rows "
             f"(`eval/datasets/golden.jsonl`). Evaluator `{first['evaluator']}`; answer relevancy embeddings "
             f"`text-embedding-3-small`. Generator `{first['generator']}`._", "",
             "### Overall", "",
             "| Config | Faithfulness | Answer relevancy | Context precision | Context recall "
             "| Correct abstention (unanswerable) | False abstention (answerable) | p50 latency "
             "| /chat cost |",
             "|---|---|---|---|---|---|---|---|---|",
             "| *Target (§B14.2)* | *≥ 0.85* | *≥ 0.85* | *≥ 0.75* | *≥ 0.80* | | | | |"]
    for c, res in results.items():
        o = res["summary"]["overall"]
        lines.append(f"| {CONFIGS[c]['label']} | " + " | ".join(fmt(o[k]) for k in METRICS)
                     + f" | {fmt(o['correct_abstention'])} | {fmt(o['false_abstention'])} "
                     f"| {res['summary']['latency_ms_p50']:.0f} ms | ${res['summary']['cost_usd']:.4f} |")
    lines += ["", "### By query type", "",
              "Each cell lists the configs in order (" + " / ".join(cfgs) + "). Context metrics and answer "
              "relevancy are n/a for unanswerable rows, which are scored on abstention instead.", "",
              "| Query type | n | Faithfulness | Answer relevancy | Context precision | Context recall "
              "| Abstained |", "|---|---|---|---|---|---|---|"]
    for t in TYPES:
        cells = []
        for k in [*METRICS, "abstain"]:
            vals = []
            for res in results.values():
                b = res["summary"][t]
                v = (b["correct_abstention"] if t == "unanswerable" else b["false_abstention"]) \
                    if k == "abstain" else b[k]
                vals.append(fmt(v))
            cells.append(" / ".join(vals))
        lines.append(f"| {t} | {first['summary'][t]['n']} | " + " | ".join(cells) + " |")

    if "A" in results and "D" in results:
        a = results["A"]["summary"]["multi_hop"]["context_recall"]
        d = results["D"]["summary"]["multi_hop"]["context_recall"]
        ok = not math.isnan(d) and (math.isnan(a) or d >= a)
        lines += ["", f"**Gate (Phase 6): D ≥ A on multi-hop context recall: {fmt(d)} vs {fmt(a)} "
                      f"{'✅' if ok else '❌'}**"]

    lines += ["", "### Per row", "",
              "F = faithfulness, AR = answer relevancy, CP = context precision, CR = context recall, "
              "cit = number of citations, ✋ = abstained.", "",
              "| # | Type | Question | " + " | ".join(f"{c}: F / AR / CP / CR (cit)" for c in cfgs) + " |",
              "|---|---|---|" + "---|" * len(cfgs)]
    for i, row in enumerate(first["rows"]):
        cells = []
        for res in results.values():
            r = res["rows"][i]
            s = " / ".join(fmt(r["scores"][k]).replace("n/a", "–") for k in METRICS)
            cells.append(f"{s} ({len(r['retrieved_contexts'])}){' ✋' if r['abstained'] else ''}")
        q = row["user_input"] if len(row["user_input"]) <= 70 else row["user_input"][:67] + "…"
        lines.append(f"| {i + 1} | {row['query_type']} | {q} | " + " | ".join(cells) + " |")
    errors = {c: sum(len(r["errors"]) for r in res["rows"]) for c, res in results.items()}
    lines += ["", f"Judge errors (scored n/a): {', '.join(f'{c} {n}' for c, n in errors.items())}.", "", END]
    return "\n".join(lines)


def write_report(results: dict[str, dict]) -> None:
    section = generated_section(results)
    if REPORT.exists() and BEGIN in (text := REPORT.read_text()) and END in text:
        head, rest = text.split(BEGIN, 1)
        text = head + section + rest.split(END, 1)[1]
    else:
        text = "# RAGAS Evaluation Report\n\n" + section + "\n"
    REPORT.write_text(text)


# --------------------------------------------------------------------------- main

def run(config: str, limit: int | None) -> dict:
    rows = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()][:limit]
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.NamedTemporaryFile("w+", prefix=f"ragas_api_{config}_", suffix=".log", delete=False) as log:
        proc = start_api(config, port, log)
        try:
            print(f"config {config} ({CONFIGS[config]['label']}): starting API on :{port} (log {log.name})")
            health = wait_ready(proc, base)
            print(f"  health: {health}")
            t0 = time.perf_counter()
            answers = ask_all(rows, base, config)
            chat_s = time.perf_counter() - t0
        except Exception:
            log.flush()
            log.seek(0)
            print("".join(log.readlines()[-25:]), file=sys.stderr)
            print("If Qdrant is locked, stop the running API (`make run`) and retry.", file=sys.stderr)
            raise
        finally:
            stop_api(proc)

    results = [{**row, "response": a["answer"], "retrieved_contexts": [c["snippet"] for c in a["citations"]],
                "citations": [c["id"] for c in a["citations"]],
                "abstained": bool(ABSTAIN.search(a["answer"])),
                **{k: a[k] for k in ("flags", "latency_ms", "usage", "trace_id")}}
               for row, a in zip(rows, answers, strict=True)]
    print(f"  scoring {len(results)} rows with RAGAS ...")
    asyncio.run(score_all(rows, results))
    s = get_settings()
    out = {"config": config, "label": CONFIGS[config]["label"],
           "flags": {k: v for k, v in CONFIGS[config].items() if k.startswith("USE_")},
           "date": date.today().isoformat(), "generator": s.model_fast, "evaluator": s.model_fast,
           "health": health, "chat_seconds": round(chat_s, 1), "n_rows": len(results),
           "rows": results, "summary": summarise(results)}
    REPORTS.mkdir(parents=True, exist_ok=True)
    # smoke runs (--limit) are saved separately and stay out of the report
    name = f"ragas_{config}_smoke.json" if limit else f"ragas_{config}.json"
    (REPORTS / name).write_text(json.dumps(out, indent=1, allow_nan=True) + "\n")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", choices=list(CONFIGS), help="feature-flag configuration (§B14.2)")
    ap.add_argument("--limit", type=int, help="only the first N golden rows (smoke test, saved as "
                                              "ragas_<config>_smoke.json and left out of the report)")
    ap.add_argument("--report-only", action="store_true", help="rebuild RAGAS_REPORT.md from saved results")
    args = ap.parse_args()
    if not args.report_only:
        if not args.config:
            ap.error("--config is required unless --report-only")
        res = run(args.config, args.limit)
        o = res["summary"]["overall"]
        print(f"config {args.config}: " + "  ".join(f"{k}={fmt(o[k])}" for k in METRICS)
              + f"  multi_hop context_recall={fmt(res['summary']['multi_hop']['context_recall'])}")
    results = load_results()
    if results:
        write_report(results)
        print(f"report -> {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
