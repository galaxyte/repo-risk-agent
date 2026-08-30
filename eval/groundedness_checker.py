"""Scores whether a report's claims are actually grounded in evidence.

Two independent layers:

Layer 1 (agent only, structural, no ground truth needed): does every
tool_output-sourced red_flag cite a tool_call_id that really exists in the
trajectory, with evidence text that really appears in that call's result?
Baseline has no trajectory and cannot produce tool_output flags at all, so
this layer is agent-only by construction.

Layer 2 (both baseline and agent, the headline metric): checks a fixed set of
structured, objectively-checkable claims against the independent reference
pass -- not against each other, so scoring isn't circular.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def check_trajectory_evidence(report: dict, trajectory_path: Path) -> dict:
    if not trajectory_path.exists():
        return {"applicable": False, "reason": "no trajectory (baseline)"}

    call_results: dict[str, str] = {}
    for line in trajectory_path.read_text().splitlines():
        if not line.strip():
            continue
        turn = json.loads(line)
        for tr in turn.get("tool_results", []):
            if tr["result"] != "TERMINAL":
                call_results[tr["call_id"]] = json.dumps(tr["result"])

    checked = 0
    verified = 0
    unverifiable = []
    for flag in report.get("red_flags", []):
        if flag.get("source") != "tool_output":
            continue
        checked += 1
        evidence = _normalize(flag.get("evidence", ""))
        cited_id = flag.get("tool_call_id")
        found = bool(evidence) and cited_id in call_results and evidence[:80] in _normalize(call_results[cited_id])
        if not found:
            # Same fallback as agent/verify.py: models often cite a descriptive
            # pseudo-id instead of the real call_id even when the evidence is
            # genuine, so search every tool result before calling it unverifiable.
            found = any(evidence and evidence[:80] in _normalize(result_text) for result_text in call_results.values())
        if found:
            verified += 1
        else:
            unverifiable.append(flag.get("title"))

    return {
        "applicable": True,
        "tool_output_flags_checked": checked,
        "verified": verified,
        "unverifiable_flags": unverifiable,
        "layer1_score": (verified / checked) if checked else None,
    }


def _vuln_nonzero(summary: dict | None) -> bool | None:
    if summary is None:
        return None
    return any(summary.get(k, 0) > 0 for k in ("critical", "high", "medium", "low"))


def _mentions_secret(report: dict) -> bool:
    for flag in report.get("red_flags", []):
        if "secret" in flag.get("title", "").lower() or "credential" in flag.get("title", "").lower():
            return True
    return False


def _compare(name: str, claimed, actual) -> dict:
    if claimed in (None, "unknown", "not_attempted"):
        return {"claim": name, "verdict": "abstained", "claimed": claimed, "actual": actual}
    verdict = "correct" if claimed == actual else "incorrect"
    return {"claim": name, "verdict": verdict, "claimed": claimed, "actual": actual}


def _compare_bool(name: str, claimed: bool | None, actual: bool | None) -> dict:
    if claimed is None or actual is None:
        return {"claim": name, "verdict": "abstained", "claimed": claimed, "actual": actual}
    verdict = "correct" if claimed == actual else "incorrect"
    return {"claim": name, "verdict": verdict, "claimed": claimed, "actual": actual}


def check_against_reference(report: dict, reference: dict) -> dict:
    ref_vuln_nonzero = _vuln_nonzero((reference.get("dependency_audit") or {}).get("summary"))
    checks = [
        _compare("build_status", report.get("build_status"), reference.get("build_status")),
        _compare("test_status", report.get("test_status"), reference.get("test_status")),
        _compare_bool("vulnerability_presence", _vuln_nonzero(report.get("vulnerability_summary")), ref_vuln_nonzero),
        _compare_bool("secret_presence", _mentions_secret(report), reference.get("secrets_found")),
    ]
    correct = sum(1 for c in checks if c["verdict"] == "correct")
    incorrect = sum(1 for c in checks if c["verdict"] == "incorrect")
    abstained = sum(1 for c in checks if c["verdict"] == "abstained")
    denom = correct + incorrect
    return {
        "checks": checks,
        "correct": correct,
        "incorrect": incorrect,
        "abstained": abstained,
        "layer2_groundedness_score": (correct / denom) if denom else None,
    }


def score_report(report: dict, reference: dict, trajectory_path: Path) -> dict:
    return {
        "layer1_evidence_traceability": check_trajectory_evidence(report, trajectory_path),
        "layer2_reference_groundedness": check_against_reference(report, reference),
    }
