"""Self-check pass (changelog Iteration 4): before the agent's report is
persisted, re-validate every tool_output-sourced red_flag against the raw
tool results actually produced during this run. Anything that doesn't check
out is demoted to a clarifying question instead of silently shipping a
red flag whose evidence turns out to be paraphrased or missing.

This runs unconditionally as part of the agent (not the eval harness) so the
agent's own output is more trustworthy even outside of scored eval runs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def self_check_and_repair(report: dict, trajectory_path: Path) -> dict:
    if not trajectory_path.exists():
        return report

    call_results: dict[str, str] = {}
    for line in trajectory_path.read_text().splitlines():
        if not line.strip():
            continue
        turn = json.loads(line)
        for tr in turn.get("tool_results", []):
            if tr["result"] != "TERMINAL":
                call_results[tr["call_id"]] = json.dumps(tr["result"])

    kept_flags = []
    demoted = []
    for flag in report.get("red_flags", []):
        if flag.get("source") == "tool_output":
            evidence = _normalize(flag.get("evidence", ""))
            cited_id = flag.get("tool_call_id")
            matched_id = None
            # Fast path: the cited call_id is real and really contains this evidence.
            if evidence and cited_id in call_results and evidence[:80] in _normalize(call_results[cited_id]):
                matched_id = cited_id
            else:
                # Fallback: models frequently invent a descriptive pseudo-id (e.g.
                # "functions.run_command:pip_install") instead of the real opaque
                # call_id, even when the evidence itself is genuine. Search every
                # tool result actually produced in this run before giving up --
                # repair the citation rather than punishing a real finding for a
                # mislabeled id.
                for cid, result_text in call_results.items():
                    if evidence and evidence[:80] in _normalize(result_text):
                        matched_id = cid
                        break
            if matched_id:
                kept_flags.append({**flag, "tool_call_id": matched_id})
            else:
                demoted.append(flag)
        else:
            kept_flags.append(flag)

    repaired = dict(report)
    repaired["red_flags"] = kept_flags
    if demoted:
        repaired["clarifying_questions"] = list(report.get("clarifying_questions", [])) + [
            f"(self-check demoted -- evidence unverifiable) {f.get('title')}" for f in demoted
        ]
    return repaired
