"""Minimal local demo UI for the repo risk agent.

Not part of the scored eval pipeline -- this just wraps the existing
baseline/agent library functions (agent/run_agent.py, baseline/baseline_assess.py)
behind a single-page web form so a repo URL or local path can be tested
without the CLI. One request at a time, no auth, no database: a demo tool,
not a service.

Analysis runs in a background thread per job so the frontend can poll
/api/status/{job_id} and render a live Cloning -> Baseline -> Agent roadmap
instead of staring at a single multi-minute spinner.

Run: uvicorn webapp.app:app --reload --port 8000
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import threading
import traceback
import uuid
from pathlib import Path
from typing import Literal

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

StageStatus = Literal["pending", "in_progress", "completed", "error"]
STAGES = ("cloning", "baseline", "agent")

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _new_job() -> str:
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "stages": {stage: {"status": "pending", "detail": None} for stage in STAGES},
            "overall": "in_progress",
            "repo_name": None,
            "baseline": None,
            "agent": None,
            "error": None,
        }
    return job_id


def _set_stage(job_id: str, stage: str, status: StageStatus, detail: str | None = None) -> None:
    with JOBS_LOCK:
        JOBS[job_id]["stages"][stage] = {"status": status, "detail": detail}


def _set_error(job_id: str, stage: str, message: str) -> None:
    with JOBS_LOCK:
        JOBS[job_id]["stages"][stage] = {"status": "error", "detail": message}
        JOBS[job_id]["overall"] = "error"
        JOBS[job_id]["error"] = message


class AnalyzeRequest(BaseModel):
    repo: str


def _resolve_repo(repo_input: str) -> tuple[Path, str]:
    repo_input = repo_input.strip()

    local_path = Path(repo_input).expanduser()
    if local_path.is_dir():
        return local_path.resolve(), local_path.resolve().name

    if not HTTPS_URL_RE.match(repo_input):
        raise ValueError(
            "Repo must be an existing local directory path, or a plain https:// git URL "
            "(e.g. https://github.com/owner/name.git). Other git transports are rejected for safety."
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
            raise ValueError(f"git clone failed: {result.stderr[-500:]}")
    return dest, name


def _run_job(job_id: str, repo_input: str) -> None:
    _set_stage(job_id, "cloning", "in_progress")
    try:
        repo_path, repo_name = _resolve_repo(repo_input)
    except Exception as e:  # noqa: BLE001
        _set_error(job_id, "cloning", str(e))
        return
    with JOBS_LOCK:
        JOBS[job_id]["repo_name"] = repo_name
    _set_stage(job_id, "cloning", "completed", f"Resolved to {repo_path}")

    client = OpenAI()
    model = "gpt-5"

    _set_stage(job_id, "baseline", "in_progress")
    try:
        baseline_report, baseline_meta = assess_repo_baseline(
            repo_path, repo_name, client, model, WORKSPACE_ROOT / "_out" / repo_name / "baseline"
        )
    except Exception as e:  # noqa: BLE001
        _set_error(job_id, "baseline", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
        return
    with JOBS_LOCK:
        JOBS[job_id]["baseline"] = {"report": baseline_report, "meta": baseline_meta}
    _set_stage(job_id, "baseline", "completed", f"risk_score={baseline_report.get('risk_score')}")

    _set_stage(job_id, "agent", "in_progress")
    try:
        agent_report, agent_meta = assess_repo(
            repo_path, repo_name, client, model, WORKSPACE_ROOT / "_out" / repo_name / "agent", variant="full"
        )
    except Exception as e:  # noqa: BLE001
        _set_error(job_id, "agent", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
        return
    with JOBS_LOCK:
        JOBS[job_id]["agent"] = {"report": agent_report, "meta": agent_meta}
        JOBS[job_id]["overall"] = "completed"
    _set_stage(job_id, "agent", "completed", f"risk_score={agent_report.get('risk_score')}")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    if not req.repo.strip():
        raise HTTPException(status_code=400, detail="repo is required")
    job_id = _new_job()
    thread = threading.Thread(target=_run_job, args=(job_id, req.repo), daemon=True)
    thread.start()
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def status(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job_id")
        return dict(job)  # shallow copy -- fine, values themselves aren't mutated in place


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
