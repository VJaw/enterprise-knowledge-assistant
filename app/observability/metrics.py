"""`GET /metrics` (§B11.1): Prometheus text-format counters and histograms, kept in process.

One Uvicorn worker (§B12.3), so a module-level registry is the whole picture. Labels are bounded: route
templates (not raw paths), cache results, rail flags and status codes.
"""
from __future__ import annotations

import threading
from bisect import bisect_left
from collections import defaultdict

LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 45.0)


def _escape(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(names: tuple[str, ...], values: tuple[str, ...]) -> str:
    if not names:
        return ""
    return "{" + ",".join(f'{n}="{_escape(v)}"' for n, v in zip(names, values, strict=True)) + "}"


class Counter:
    def __init__(self, name: str, help_: str, labels: tuple[str, ...] = ()):
        self.name, self.help, self.label_names = name, help_, labels
        self.values: dict[tuple[str, ...], float] = defaultdict(float)

    def inc(self, *labels: str, value: float = 1.0) -> None:
        with _lock:
            self.values[tuple(labels)] += value

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        out += [f"{self.name}{_labels(self.label_names, k)} {v:g}" for k, v in sorted(self.values.items())]
        return out


class Histogram:
    def __init__(self, name: str, help_: str, labels: tuple[str, ...] = (), buckets=LATENCY_BUCKETS):
        self.name, self.help, self.label_names, self.buckets = name, help_, labels, buckets
        self.counts: dict[tuple[str, ...], list[int]] = {}
        self.sums: dict[tuple[str, ...], float] = defaultdict(float)

    def observe(self, value: float, *labels: str) -> None:
        key = tuple(labels)
        with _lock:
            counts = self.counts.setdefault(key, [0] * (len(self.buckets) + 1))
            counts[bisect_left(self.buckets, value)] += 1
            self.sums[key] += value

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for key, counts in sorted(self.counts.items()):
            running = 0
            for le, n in zip([*map(str, self.buckets), "+Inf"], counts, strict=True):
                running += n
                out.append(f"{self.name}_bucket{_labels((*self.label_names, 'le'), (*key, le))} {running}")
            out.append(f"{self.name}_sum{_labels(self.label_names, key)} {self.sums[key]:.6f}")
            out.append(f"{self.name}_count{_labels(self.label_names, key)} {running}")
        return out


_lock = threading.Lock()

HTTP_REQUESTS = Counter("eka_http_requests_total", "HTTP requests by route and status.",
                        ("method", "route", "status"))
HTTP_LATENCY = Histogram("eka_http_request_duration_seconds", "HTTP request latency by route.", ("route",))
CHAT_REQUESTS = Counter("eka_chat_requests_total", "Chat turns by cache result and model.",
                        ("cache", "model"))
CHAT_LATENCY = Histogram("eka_chat_latency_seconds", "End-to-end /chat latency by cache result.", ("cache",))
RAIL_TRIGGERS = Counter("eka_guardrail_triggers_total", "Guardrail and agent flags raised.", ("flag",))
BLOCKED = Counter("eka_guardrail_blocked_total", "Requests refused by a rail.")
ERRORS = Counter("eka_errors_total", "Error responses by code (§B11.3).", ("code",))
TOKENS = Counter("eka_llm_tokens_total", "LLM tokens by model and kind.", ("model", "kind"))
COST = Counter("eka_llm_cost_usd_total", "Estimated LLM cost in USD by model.", ("model",))

REGISTRY = (HTTP_REQUESTS, HTTP_LATENCY, CHAT_REQUESTS, CHAT_LATENCY, RAIL_TRIGGERS, BLOCKED, ERRORS, TOKENS,
            COST)


def record_chat(*, cache: str, model: str, latency_s: float, flags: list[str], blocked: bool,
                usage: dict) -> None:
    CHAT_REQUESTS.inc(cache, model)
    CHAT_LATENCY.observe(latency_s, cache)
    for f in flags:
        RAIL_TRIGGERS.inc(f)
    if blocked:
        BLOCKED.inc()
    for m, u in (usage.get("by_model") or {}).items():
        TOKENS.inc(m, "prompt", value=u.get("prompt_tokens", 0))
        TOKENS.inc(m, "completion", value=u.get("completion_tokens", 0))
        if "est_cost_usd" in u:
            COST.inc(m, value=u["est_cost_usd"])


def render() -> str:
    return "\n".join(line for metric in REGISTRY for line in metric.render()) + "\n"


def reset() -> None:
    """Tests only."""
    with _lock:
        for m in REGISTRY:
            for attr in ("values", "counts", "sums"):
                if hasattr(m, attr):
                    getattr(m, attr).clear()
