"""Renders one agent_trajectory.jsonl + its final report into a human-readable
Markdown file for the "agent trajectories" deliverable.

Usage:
    python -m eval.render_trajectory --repo django-dialogflow --variant full --out trajectories/django-dialogflow.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def render(repo: str, variant: str, raw_dir: Path) -> str:
    variant_dir = raw_dir / repo / f"agent_{variant}"
    trajectory = [json.loads(l) for l in (variant_dir / "agent_trajectory.jsonl").read_text().splitlines() if l.strip()]
    report = json.loads((variant_dir / "agent_report.json").read_text())
    meta = json.loads((variant_dir / "agent_meta.json").read_text())

    lines = [
        f"# Agent trajectory — `{repo}` (variant: `{variant}`)",
        "",
        f"Model: `{meta['model']}` · Turns used: {meta['turns_used']} · Wall time: {meta['wall_clock_sec']}s · "
        f"Tokens: {meta['input_tokens']} in / {meta['output_tokens']} out · Sandbox available: {meta['sandbox_available']}",
        "",
        "## Instructions given to the agent",
        "",
        "See `agent/prompts.py` — `SYSTEM_PROMPT` (variant `full`/`no_selfcheck`) or `NO_CONTRACT_SYSTEM_PROMPT` (variant `no_contract`). "
        "In short: use the sandboxed tools to actually verify build/test/dependency/secret status; every red flag must cite real evidence.",
        "",
        "## Turn-by-turn",
        "",
    ]

    for turn in trajectory:
        lines.append(f"### Turn {turn['turn']} (t={turn['elapsed_sec']}s{', forced submit' if turn.get('forced_submit') else ''})")
        for fc in turn["function_calls"]:
            args_preview = fc["arguments"][:300] + ("..." if len(fc["arguments"]) > 300 else "")
            lines.append(f"- **Called** `{fc['name']}`(`{fc['call_id']}`) with args: `{args_preview}`")
        for tr in turn["tool_results"]:
            if tr["result"] == "TERMINAL":
                lines.append(f"  - → `{tr['call_id']}` was the terminal `submit_report` call (ends the run).")
                continue
            result_str = json.dumps(tr["result"])
            preview = result_str[:500] + ("..." if len(result_str) > 500 else "")
            lines.append(f"  - → result for `{tr['call_id']}`: `{preview}`")
        lines.append("")

    lines += [
        "## Final report",
        "",
        "```json",
        json.dumps(report, indent=2),
        "```",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--variant", default="full")
    parser.add_argument("--raw-dir", type=Path, default=Path("results/raw"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(args.repo, args.variant, args.raw_dir))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
