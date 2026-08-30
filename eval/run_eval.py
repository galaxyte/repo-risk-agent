"""Orchestrates the full evaluation: for each repo in data/repos.json,
clone once, then run (1) the independent reference pass, (2) the baseline,
(3) the agent -- all against that same pristine clone (the sandbox only ever
touches a COPY of it inside a container, so reuse across the three passes is
safe and keeps the eval to one clone per repo).

Usage:
    python -m eval.run_eval --repos-file data/repos.json --out-dir results/raw
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from agent.run_agent import VARIANTS, assess_repo
from baseline.baseline_assess import assess_repo_baseline
from eval.reference_pass import run_reference_pass

# Order matters here: it's the ablation ladder used for the changelog
# (no_contract -> no_selfcheck -> full), so keep it in step with VARIANTS.
AGENT_VARIANTS = list(VARIANTS.keys())


def _clone_if_needed(clone_url: str, workspace_dir: Path) -> None:
    if workspace_dir.exists() and any(workspace_dir.iterdir()):
        return
    workspace_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", clone_url, str(workspace_dir)], check=True, capture_output=True, text=True)


def run_all(repos_file: Path, out_dir: Path, workspace_root: Path, model: str) -> None:
    load_dotenv()
    client = OpenAI()
    repos = json.loads(repos_file.read_text())

    status: list[dict] = []
    for entry in repos:
        name = entry["name"]
        clone_url = entry["clone_url"]
        repo_out = out_dir / name
        repo_workspace = workspace_root / name
        print(f"\n=== {name} ===")

        try:
            print(f"[{name}] cloning...")
            _clone_if_needed(clone_url, repo_workspace)

            print(f"[{name}] reference pass...")
            t0 = time.monotonic()
            run_reference_pass(repo_workspace, name, repo_out)
            print(f"[{name}] reference pass done in {time.monotonic() - t0:.1f}s")

            print(f"[{name}] baseline...")
            t0 = time.monotonic()
            assess_repo_baseline(repo_workspace, name, client, model, repo_out)
            print(f"[{name}] baseline done in {time.monotonic() - t0:.1f}s")

            for variant in AGENT_VARIANTS:
                print(f"[{name}] agent ({variant})...")
                t0 = time.monotonic()
                assess_repo(repo_workspace, name, client, model, repo_out / f"agent_{variant}", variant=variant)
                print(f"[{name}] agent ({variant}) done in {time.monotonic() - t0:.1f}s")

            status.append({"name": name, "ok": True})
        except Exception as e:  # noqa: BLE001 -- one bad repo must not abort the whole eval run
            print(f"[{name}] FAILED: {type(e).__name__}: {e}")
            (repo_out).mkdir(parents=True, exist_ok=True)
            (repo_out / "error.txt").write_text(f"{type(e).__name__}: {e}")
            status.append({"name": name, "ok": False, "error": str(e)})

    (out_dir / "_run_status.json").write_text(json.dumps(status, indent=2))
    print("\n=== eval run complete ===")
    for s in status:
        print(f"  {s['name']}: {'OK' if s['ok'] else 'FAILED - ' + s.get('error', '')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos-file", type=Path, default=Path("data/repos.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("results/raw"))
    parser.add_argument("--workspace-root", type=Path, default=Path("workspace"))
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5"))
    args = parser.parse_args()
    run_all(args.repos_file, args.out_dir, args.workspace_root, args.model)


if __name__ == "__main__":
    main()
