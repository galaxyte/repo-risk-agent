"""The fair baseline: one direct LLM call, zero execution tools.

This is the "basic way to handle the task before using your solution" the
hackathon doc asks for -- a competent freelancer's first instinct (dump the
repo, ask an LLM to eyeball it) with no ability to actually verify build,
test, or dependency claims. Same model as the agent, so any measured
difference isolates tool-use/verification rather than model capability.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from agent.prompts import BASELINE_SYSTEM_PROMPT, BASELINE_USER_TEMPLATE
from shared.schema import RiskReport, structured_output_text_format

CHAR_BUDGET = 160_000  # ~40K tokens at ~4 chars/token
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}


def _collect_file_dump(repo_path: Path) -> str:
    sections: list[tuple[str, str]] = []

    for readme in sorted(repo_path.glob("README*")):
        sections.append((str(readme.relative_to(repo_path)), readme.read_text(errors="ignore")))

    manifest_names = [
        "package.json", "requirements.txt", "pyproject.toml", "Cargo.toml", "go.mod",
    ]
    for name in manifest_names:
        f = repo_path / name
        if f.exists():
            sections.append((name, f.read_text(errors="ignore")))

    for ci_dir in [".github/workflows", ".gitlab-ci.yml", ".circleci"]:
        p = repo_path / ci_dir
        if p.is_file():
            sections.append((ci_dir, p.read_text(errors="ignore")))
        elif p.is_dir():
            for f in sorted(p.glob("*")):
                if f.is_file():
                    sections.append((str(f.relative_to(repo_path)), f.read_text(errors="ignore")))

    source_files = [
        p for p in repo_path.rglob("*")
        if p.is_file()
        and not any(part in SKIP_DIRS for part in p.parts)
        and p.suffix in {".py", ".js", ".ts", ".go", ".rs", ".java"}
    ]
    source_files.sort(key=lambda p: -(p.stat().st_size if p.exists() else 0))
    for f in source_files[:10]:
        sections.append((str(f.relative_to(repo_path)), f.read_text(errors="ignore")))

    test_files = [
        p for p in repo_path.rglob("*")
        if p.is_file()
        and not any(part in SKIP_DIRS for part in p.parts)
        and ("test" in p.name.lower() or "test" in [x.lower() for x in p.parts])
    ]
    for f in test_files[:5]:
        sections.append((str(f.relative_to(repo_path)), f.read_text(errors="ignore")))

    dump_parts = []
    used = 0
    for path, content in sections:
        header = f"\n\n===== {path} =====\n"
        budget_left = CHAR_BUDGET - used
        if budget_left <= 0:
            break
        chunk = content[:budget_left]
        dump_parts.append(header + chunk)
        used += len(header) + len(chunk)

    return "".join(dump_parts)


def assess_repo_baseline(repo_path: Path, repo_name: str, client: OpenAI, model: str, out_dir: Path) -> tuple[dict, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    file_dump = _collect_file_dump(repo_path)
    (out_dir / "baseline_input.txt").write_text(file_dump)

    start = time.monotonic()
    resp = client.responses.create(
        model=model,
        instructions=BASELINE_SYSTEM_PROMPT,
        input=[{"role": "user", "content": BASELINE_USER_TEMPLATE.format(repo_name=repo_name, file_dump=file_dump)}],
        text=structured_output_text_format(),
        reasoning={"effort": "medium"},
    )
    elapsed = time.monotonic() - start

    report = json.loads(resp.output_text)
    RiskReport.model_validate(report)

    meta = {
        "repo_name": repo_name,
        "model": model,
        "wall_clock_sec": round(elapsed, 1),
        "input_tokens": resp.usage.input_tokens if resp.usage else None,
        "output_tokens": resp.usage.output_tokens if resp.usage else None,
        "file_dump_chars": len(file_dump),
    }

    (out_dir / "baseline_report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "baseline_meta.json").write_text(json.dumps(meta, indent=2))
    return report, meta


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the zero-tool baseline on a single, already-cloned repo.")
    parser.add_argument("--repo-path", required=True, type=Path)
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5"))
    args = parser.parse_args()

    client = OpenAI()
    report, meta = assess_repo_baseline(args.repo_path, args.repo_name, client, args.model, args.out_dir)
    print(json.dumps({"report": report, "meta": meta}, indent=2))


if __name__ == "__main__":
    main()
