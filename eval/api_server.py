"""Start and stop a private API process for an evaluation run (RAGAS, Promptfoo, safety).

Each harness runs `uvicorn app.main:app` with its own feature flags in the environment, so the numbers come
from a known configuration rather than whatever `make run` happens to have. Embedded Qdrant takes a folder
lock, so stop `make run` first.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def clear_cache() -> int:
    """Empty the `semantic_cache` collection (the API must not be running); returns the entries removed."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FilterSelector

    from app.cache.semantic_cache import COLLECTION
    from app.config import get_settings

    client = QdrantClient(path=get_settings().qdrant_path)
    try:
        if not client.collection_exists(COLLECTION):
            return 0
        n = client.count(COLLECTION, exact=True).count
        client.delete(COLLECTION, points_selector=FilterSelector(filter=Filter()))
        return n
    finally:
        client.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_api(env_flags: dict[str, str], port: int, log) -> subprocess.Popen:
    env = os.environ | env_flags
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


def tail(log, n: int = 25) -> str:
    log.flush()
    log.seek(0)
    return "".join(log.readlines()[-n:])
