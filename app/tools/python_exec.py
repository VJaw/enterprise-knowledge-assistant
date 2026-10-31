"""`python_exec`: calculations on tool results in a separate, limited Python process (§B9.3, §B17).

`python -I sandbox_runner.py` in a fresh temp dir, with an empty environment (no API keys), CPU 5 s and memory
256 MB rlimits, an audit hook that blocks sockets, subprocesses, `os.system` and writes outside the temp dir,
an import allow-list, and a 10 s wall-clock timeout. Not a hardened sandbox: production would use E2B, gVisor
or Firecracker (§B17)."""
from __future__ import annotations

import asyncio
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.tools.common import error, guarded, truncate

RUNNER = Path(__file__).with_name("sandbox_runner.py")
CPU_S = 5
MEM_MB = 256
WALL_S = 10.0
OUTPUT_CHARS = 4000


class PythonArgs(BaseModel):
    code: str = Field(description="Python 3 code; print() the results you need")


def run_code(code: str, cpu_s: int = CPU_S, mem_mb: int = MEM_MB, wall_s: float = WALL_S) -> tuple[bool, str]:
    """(ok, output). Blocking: call it from a thread."""
    with tempfile.TemporaryDirectory(prefix="pyexec-") as tmp:
        try:
            p = subprocess.run([sys.executable, "-I", str(RUNNER), str(cpu_s), str(mem_mb)], input=code,
                               capture_output=True, text=True, cwd=tmp, env={}, timeout=wall_s)
        except subprocess.TimeoutExpired:
            return False, f"killed: wall-clock limit of {wall_s:g} s exceeded"
    if p.returncode == -signal.SIGXCPU or p.returncode == -signal.SIGKILL:
        return False, f"killed: CPU time limit of {cpu_s} s exceeded"
    if p.returncode != 0:
        lines = p.stderr.strip().splitlines()
        return False, (lines[-1] if lines else f"exit code {p.returncode}")
    return True, truncate(p.stdout, OUTPUT_CHARS) or "(no output; use print())"


def make_python_exec() -> StructuredTool:
    @guarded(timeout=WALL_S + 2)
    async def python_exec(code: str):
        ok, out = await asyncio.to_thread(run_code, code)
        return (out, []) if ok else error(out)

    description = ("Run a short Python 3 snippet for arithmetic, percentages, date maths or aggregating "
                   "numbers you already have from other tools, and return its stdout. No network, no files, "
                   "no subprocesses; standard-library maths/data modules only (math, statistics, datetime, "
                   "json, collections, itertools, re, ...). Limits: 5 s CPU, 256 MB, 10 s total.")
    return StructuredTool.from_function(coroutine=python_exec, name="python_exec", description=description,
                                        args_schema=PythonArgs, response_format="content_and_artifact")
