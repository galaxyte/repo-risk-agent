"""Minimal local demo UI for the repo risk agent.

Not part of the scored eval pipeline -- this just wraps the existing
baseline/agent library functions (agent/run_agent.py, baseline/baseline_assess.py)
behind a single-page web form so a repo URL or local path can be tested
without the CLI. One request at a time, no auth, no database: a demo tool,
not a service.

Run: uvicorn webapp.app:app --reload --port 8000
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

from agent.run_agent import assess_repo
from baseline.baseline_assess import assess_repo_baseline

load_dotenv()

app = FastAPI(title="Repo Risk Agent — Demo")
STATIC_DIR = Path(__file__).parent / "static"
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent / "workspace" / "_webapp"

# Only plain https:// GitHub-style URLs are accepted for cloning. git supports
# dangerous transport helpers (ext::, fd::, file://) that can run arbitrary
# commands if a raw user string were passed straight to `git clone` -- so
# anything that isn't a plain https URL is rejected outright.
HTTPS_URL_RE = re.compile(r"^https://[a-zA-Z0-9._-]+/[\w.\-/]+?(?:\.git)?/?$")


class AnalyzeRequest(BaseModel):
    repo: str


def _resolve_repo(repo_input: str) -> tuple[Path, str]:
    repo_input = repo_input.strip()

    local_path = Path(repo_input).expanduser()
    if local_path.is_dir():
        return local_path.resolve(), local_path.resolve().name

    if not HTTPS_URL_RE.match(repo_input):
        raise HTTPException(
            status_code=400,
            detail="Repo must be an existing local directory path, or a plain https:// git URL "
                   "(e.g. https://github.com/owner/name.git). Other git transports are rejected for safety.",
        )

    name = repo_input.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    dest = WORKSPACE_ROOT / f"{name}-{hashlib.sha256(repo_input.encode()).hexdigest()[:8]}"
    if not dest.exists():
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_input, str(dest)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=400, detail=f"git clone failed: {result.stderr[-500:]}")
    return dest, name


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    repo_path, repo_name = _resolve_repo(req.repo)
    client = OpenAI()
    model = "gpt-5"

    baseline_report, baseline_meta = assess_repo_baseline(
        repo_path, repo_name, client, model, WORKSPACE_ROOT / "_out" / repo_name / "baseline"
    )
    agent_report, agent_meta = assess_repo(
        repo_path, repo_name, client, model, WORKSPACE_ROOT / "_out" / repo_name / "agent", variant="full"
    )

    return {
        "repo_name": repo_name,
        "baseline": {"report": baseline_report, "meta": baseline_meta},
        "agent": {"report": agent_report, "meta": agent_meta},
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
