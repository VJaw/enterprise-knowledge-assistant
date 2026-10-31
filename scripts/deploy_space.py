"""Deploy to the Hugging Face Space (Phase 12, §B4). Each step is a separate command, run on its own:

    python scripts/deploy_space.py create            # create the Docker Space (public) if it doesn't exist
    python scripts/deploy_space.py secrets           # set the Space secrets from .env (values never printed)
    python scripts/deploy_space.py push              # upload a clean snapshot of git HEAD
    python scripts/deploy_space.py status            # runtime stage + hardware

The Space's git server rejects binary files (the policy PDFs, screenshots) unless they are stored with
LFS/Xet, so `push` uploads `git archive HEAD` through the Hub API, which stores binaries with Xet, instead of
`git push hf main` (D-029). Only committed files are sent; `.env` is never committed. HF_TOKEN comes from .env
and stays local.
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
REPO_ID = "VijayaPJ/enterprise-knowledge-assistant"
SECRETS = ["OPENAI_API_KEY", "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "LANGFUSE_PUBLIC_KEY",
           "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "TAVILY_API_KEY", "ADMIN_TOKEN"]


def api(env: dict) -> HfApi:
    token = env.get("HF_TOKEN")
    if not token:
        sys.exit("HF_TOKEN is not set in .env")
    return HfApi(token=token)


def create(hf: HfApi, repo_id: str) -> None:
    url = hf.create_repo(repo_id, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    print(f"Space ready: {url}")


def secrets(hf: HfApi, repo_id: str, env: dict) -> None:
    missing = [k for k in SECRETS if not env.get(k)]
    if missing:
        sys.exit(f"missing in .env: {', '.join(missing)}")
    if env["ADMIN_TOKEN"] == "change-me":
        sys.exit("ADMIN_TOKEN is still the .env.example default; set a random value first")
    for key in SECRETS:
        hf.add_space_secret(repo_id, key, env[key])
        print(f"  secret {key} set (len {len(env[key])})")


def push(hf: HfApi, repo_id: str) -> None:
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, check=True,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("warning: uncommitted changes are NOT uploaded (only git HEAD is)")
    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT, check=True,
                             capture_output=True).stdout
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter="data")
        files = sum(1 for p in Path(tmp).rglob("*") if p.is_file())
        print(f"uploading {files} files from {head} ...")
        info = hf.upload_folder(repo_id=repo_id, repo_type="space", folder_path=tmp,
                                commit_message=f"Deploy {head}", delete_patterns="*")
    print(f"commit: {info.commit_url}")


def status(hf: HfApi, repo_id: str) -> None:
    rt = hf.get_space_runtime(repo_id)
    print(f"stage={rt.stage} hardware={rt.hardware} requested={rt.requested_hardware}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["create", "secrets", "push", "status"])
    parser.add_argument("--repo-id", default=REPO_ID)
    args = parser.parse_args()
    env = dotenv_values(ROOT / ".env")
    hf = api(env)
    if args.step == "create":
        create(hf, args.repo_id)
    elif args.step == "secrets":
        secrets(hf, args.repo_id, env)
    elif args.step == "push":
        push(hf, args.repo_id)
    else:
        status(hf, args.repo_id)


if __name__ == "__main__":
    main()
