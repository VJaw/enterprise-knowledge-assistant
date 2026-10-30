"""Phase 0: verify local tooling and every external credential before building anything."""
import os, shutil, subprocess, sys, time
from dotenv import load_dotenv

load_dotenv()
results: list[tuple[str, str, str]] = []  # (check, status, detail)

def record(name, fn):
    t0 = time.perf_counter()
    try:
        detail = fn() or ""
        results.append((name, "PASS", f"{detail} ({(time.perf_counter()-t0)*1000:.0f} ms)"))
    except Exception as e:  # noqa: BLE001
        results.append((name, "FAIL", f"{type(e).__name__}: {str(e)[:160]}"))

def mask(v: str | None) -> str:
    return "<missing>" if not v else f"{v[:4]}…{v[-2:]}"

# ---------- env vars present ----------
REQUIRED = ["OPENAI_API_KEY", "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD",
            "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST",
            "TAVILY_API_KEY", "HF_TOKEN"]
def env_present():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"missing: {', '.join(missing)}")
    if not os.getenv("NEO4J_URI", "").startswith("neo4j+s://"):
        raise RuntimeError("NEO4J_URI should start with neo4j+s:// for Aura")
    return ", ".join(f"{k}={mask(os.getenv(k))}" for k in ["OPENAI_API_KEY", "TAVILY_API_KEY"])
record("env vars present", env_present)

# ---------- local tooling ----------
def tool(cmd, args=("--version",)):
    def _check():
        if not shutil.which(cmd):
            raise RuntimeError(f"{cmd} not on PATH")
        out = subprocess.run([cmd, *args], capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[:120] or "non-zero exit")
        return (out.stdout or out.stderr).strip().splitlines()[0][:60]
    return _check
record("python 3.11", lambda: (_ for _ in ()).throw(RuntimeError(sys.version))
       if sys.version_info[:2] != (3, 11) else sys.version.split()[0])
record("uv", tool("uv"))
record("node >= 18", tool("node"))
record("npx", tool("npx"))
record("docker daemon", tool("docker", ("info", "--format", "{{.ServerVersion}}")))
record("git", tool("git"))

# ---------- OpenAI ----------
def openai_check():
    from openai import OpenAI
    c = OpenAI()
    ids = {m.id for m in c.models.list()}
    missing = [m for m in ("gpt-4o", "gpt-4o-mini") if m not in ids]
    if missing:
        raise RuntimeError(f"models not available: {missing}")
    r = c.chat.completions.create(model="gpt-4o-mini", max_completion_tokens=5,
                                  messages=[{"role": "user", "content": "Reply with OK"}])
    return f"gpt-4o + gpt-4o-mini available; reply={r.choices[0].message.content!r}"
record("openai", openai_check)

# ---------- Neo4j Aura ----------
def neo4j_check():
    from neo4j import GraphDatabase
    with GraphDatabase.driver(os.environ["NEO4J_URI"],
                              auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])) as d:
        d.verify_connectivity()
        d.execute_query("MERGE (n:_Phase0Check {id:'phase0'}) SET n.ts = timestamp()")
        recs, _, _ = d.execute_query("MATCH (n:_Phase0Check {id:'phase0'}) RETURN count(n) AS c")
        d.execute_query("MATCH (n:_Phase0Check) DETACH DELETE n")
        info = d.get_server_info()
        return f"read/write OK (count={recs[0]['c']}), server={info.agent}"
record("neo4j aura", neo4j_check)

# ---------- Langfuse ----------
def langfuse_check():
    from langfuse import Langfuse
    lf = Langfuse()
    if not lf.auth_check():
        raise RuntimeError("auth_check returned False")
    if hasattr(lf, "start_as_current_observation"):  # SDK v4+
        with lf.start_as_current_observation(name="phase0-env-check", as_type="span") as span:
            span.update(output="ok")
        trace_id = span.trace_id
    elif hasattr(lf, "start_as_current_span"):  # SDK v3
        with lf.start_as_current_span(name="phase0-env-check") as span:
            span.update(output="ok")
        trace_id = span.trace_id
    else:  # SDK v2
        trace_id = lf.trace(name="phase0-env-check", output="ok").id
    lf.flush()
    return f"auth OK; test trace 'phase0-env-check' sent (id={trace_id})"
record("langfuse", langfuse_check)

# ---------- Tavily ----------
def tavily_check():
    from tavily import TavilyClient
    r = TavilyClient(api_key=os.environ["TAVILY_API_KEY"]).search("Neo4j Aura", max_results=1)
    return f"{len(r.get('results', []))} result(s)"
record("tavily", tavily_check)

# ---------- Hugging Face ----------
def hf_check():
    from huggingface_hub import HfApi
    me = HfApi(token=os.environ["HF_TOKEN"]).whoami()
    role = me.get("auth", {}).get("accessToken", {}).get("role", "unknown")
    if role not in ("write", "fineGrained"):
        raise RuntimeError(f"token role is '{role}', need 'write'")
    return f"user={me.get('name')} role={role}"
record("huggingface", hf_check)

# ---------- report ----------
w = max(len(n) for n, _, _ in results)
print("\nPhase 0 environment check\n" + "-" * (w + 60))
for name, status, detail in results:
    print(f"{name:<{w}}  {'✅' if status == 'PASS' else '❌'} {status}  {detail}")
failed = [n for n, s, _ in results if s == "FAIL"]
print("-" * (w + 60))
print("ALL CHECKS PASSED" if not failed else f"{len(failed)} FAILED: {', '.join(failed)}")
sys.exit(1 if failed else 0)
