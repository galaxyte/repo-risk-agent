"""Computes the full baseline-vs-agent-variants comparison from results/raw/
and emits results/summary.csv (per-repo detail) and results/summary.md (the
headline table, doubling as the changelog's evidence for each iteration:
baseline -> agent_no_contract -> agent_no_selfcheck -> agent_full).

Usage:
    python -m eval.score --repos-file data/repos.json --expert-file data/expert_ranking.json --raw-dir results/raw --out-dir results
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from scipy.stats import spearmanr

from agent.run_agent import VARIANTS
from eval.groundedness_checker import check_against_reference, check_trajectory_evidence

INPUT_PRICE_PER_MTOK = float(os.environ.get("OPENAI_INPUT_PRICE_PER_MTOK", "0") or 0)
OUTPUT_PRICE_PER_MTOK = float(os.environ.get("OPENAI_OUTPUT_PRICE_PER_MTOK", "0") or 0)

# System keys in display order: the fair baseline, then the ablation ladder.
SYSTEMS = ["baseline"] + list(VARIANTS.keys())


def _cost(meta: dict | None) -> float | None:
    if not meta or not INPUT_PRICE_PER_MTOK or not OUTPUT_PRICE_PER_MTOK:
        return None
    in_tok = meta.get("input_tokens") or 0
    out_tok = meta.get("output_tokens") or 0
    return (in_tok / 1_000_000) * INPUT_PRICE_PER_MTOK + (out_tok / 1_000_000) * OUTPUT_PRICE_PER_MTOK


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _system_paths(repo_dir: Path, system: str) -> tuple[Path, Path, Path]:
    """Returns (report_path, meta_path, trajectory_path) for a system key."""
    if system == "baseline":
        return repo_dir / "baseline_report.json", repo_dir / "baseline_meta.json", repo_dir / "__no_trajectory__"
    variant_dir = repo_dir / f"agent_{system}"
    return variant_dir / "agent_report.json", variant_dir / "agent_meta.json", variant_dir / "agent_trajectory.jsonl"


def score_all(repos_file: Path, expert_file: Path, raw_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    repos = json.loads(repos_file.read_text())
    expert = {e["name"]: e for e in json.loads(expert_file.read_text())}

    rows = []
    for entry in repos:
        name = entry["name"]
        repo_dir = raw_dir / name
        reference = _load_json(repo_dir / "reference.json")
        exp = expert.get(name, {})

        row = {"name": name, "ok": reference is not None}
        if reference is None:
            rows.append(row)
            continue

        row["expert_rank"] = exp.get("expert_rank")
        row["expert_risk_score"] = exp.get("expert_risk_score")
        row["expert_go_no_go"] = exp.get("expert_go_no_go")

        for system in SYSTEMS:
            report_path, meta_path, traj_path = _system_paths(repo_dir, system)
            report = _load_json(report_path)
            meta = _load_json(meta_path)
            if report is None:
                row[f"{system}_ok"] = False
                continue
            row[f"{system}_ok"] = True
            g2 = check_against_reference(report, reference)
            row[f"{system}_risk_score"] = report.get("risk_score")
            row[f"{system}_go_no_go"] = report.get("go_no_go")
            row[f"{system}_go_no_go_match"] = report.get("go_no_go") == exp.get("expert_go_no_go")
            row[f"{system}_groundedness"] = g2["layer2_groundedness_score"]
            row[f"{system}_abstain_rate"] = g2["abstained"] / len(g2["checks"])
            row[f"{system}_cost_usd"] = _cost(meta)
            row[f"{system}_wall_sec"] = meta.get("wall_clock_sec") if meta else None
            if system != "baseline":
                g1 = check_trajectory_evidence(report, traj_path)
                row[f"{system}_layer1_evidence_score"] = g1.get("layer1_score")

        rows.append(row)

    ok_rows = [r for r in rows if r["ok"]]
    with open(out_dir / "summary.csv", "w", newline="") as f:
        fieldnames = sorted({k for r in rows for k in r.keys()})
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    def _mean(vals: list) -> float | None:
        clean = [v for v in vals if v is not None]
        return sum(clean) / len(clean) if clean else None

    def _corr(system: str) -> float | None:
        pairs = [
            (r[f"{system}_risk_score"], r["expert_risk_score"])
            for r in ok_rows
            if r.get(f"{system}_risk_score") is not None and r.get("expert_risk_score") is not None
        ]
        if len(pairs) < 3:
            return None
        xs, ys = zip(*pairs)
        rho, _ = spearmanr(xs, ys)
        return rho

    def _fmt(v, pct: bool = False, money: bool = False, dp: int = 2) -> str:
        if v is None:
            return "N/A"
        if pct:
            return f"{v * 100:.0f}%"
        if money:
            return f"${v:.3f}"
        return f"{v:.{dp}f}"

    metrics = {}
    for system in SYSTEMS:
        metrics[system] = {
            "corr": _corr(system),
            "groundedness": _mean([r.get(f"{system}_groundedness") for r in ok_rows]),
            "abstain_rate": _mean([r.get(f"{system}_abstain_rate") for r in ok_rows]),
            "gng": _mean([1.0 if r.get(f"{system}_go_no_go_match") else 0.0 for r in ok_rows if f"{system}_go_no_go_match" in r]),
            "cost": _mean([r.get(f"{system}_cost_usd") for r in ok_rows]),
            "time": _mean([r.get(f"{system}_wall_sec") for r in ok_rows]),
        }
    agent_layer1 = {
        system: _mean([r.get(f"{system}_layer1_evidence_score") for r in ok_rows])
        for system in SYSTEMS if system != "baseline"
    }

    display_names = {
        "baseline": "Simple Baseline",
        "no_contract": "Agent — tools, no verification contract",
        "no_selfcheck": "Agent — verification contract, no self-check",
        "full": "Agent — full (contract + self-check)",
    }
    header = "| Metric | " + " | ".join(display_names[s] for s in SYSTEMS) + " |"
    sep = "|" + "---|" * (len(SYSTEMS) + 1)

    def _row(label: str, key: str, **fmt_kwargs) -> str:
        cells = [_fmt(metrics[s][key], **fmt_kwargs) for s in SYSTEMS]
        return f"| {label} | " + " | ".join(cells) + " |"

    lines = [
        "# Baseline vs. Agent Ablation Ladder — Results",
        "",
        f"n = {len(ok_rows)} repos scored ({len(rows) - len(ok_rows)} failed, see `_run_status.json`)",
        "",
        header,
        sep,
        _row("Spearman rank corr. vs. expert risk ranking", "corr"),
        _row("Groundedness (Layer 2, % of *non-abstained* claims verified against reference pass)", "groundedness", pct=True),
        _row("Abstain rate (% of the 4 checkable claims left as \"unknown\"/null instead of asserted)", "abstain_rate", pct=True),
        _row("Go/No-Go agreement with expert", "gng", pct=True),
        _row("Avg cost / repo", "cost", money=True),
        _row("Avg wall time / repo (sec)", "time"),
        "",
        "Agent-only Layer 1 evidence traceability (fraction of tool_output red flags whose "
        "cited tool_call_id/evidence actually appear in that run's own trajectory — no "
        "baseline analog, since baseline has no tools):",
        "",
        "| " + " | ".join(display_names[s] for s in agent_layer1) + " |",
        "|" + "---|" * len(agent_layer1),
        "| " + " | ".join(_fmt(v, pct=True) for v in agent_layer1.values()) + " |",
        "",
        "See `summary.csv` for per-repo detail.",
    ]
    (out_dir / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos-file", type=Path, default=Path("data/repos.json"))
    parser.add_argument("--expert-file", type=Path, default=Path("data/expert_ranking.json"))
    parser.add_argument("--raw-dir", type=Path, default=Path("results/raw"))
    parser.add_argument("--out-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    score_all(args.repos_file, args.expert_file, args.raw_dir, args.out_dir)


if __name__ == "__main__":
    main()
