"""Shared structured-output schema for baseline and agent.

The JSON schema dicts are hand-written (not derived from the pydantic models)
because OpenAI Structured Outputs "strict" mode requires every property to be
listed in "required" and every object to set "additionalProperties": false,
including nested objects -- pydantic's auto-generated schema does not follow
that shape by default. The pydantic models below are used only for parsing
and validating the JSON the model returns, not for generating the schema.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class FlagSource(str, Enum):
    tool_output = "tool_output"
    file_reference = "file_reference"
    static_manifest = "static_manifest"


class GoNoGo(str, Enum):
    go = "go"
    go_with_conditions = "go_with_conditions"
    no_go = "no_go"


class RunStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    not_attempted = "not_attempted"
    unknown = "unknown"


class VulnerabilitySummary(BaseModel):
    critical: int
    high: int
    medium: int
    low: int


class RedFlag(BaseModel):
    title: str
    severity: Severity
    evidence: str
    source: FlagSource
    tool_call_id: Optional[str] = None
    file_ref: Optional[str] = None


class RiskReport(BaseModel):
    repo_name: str
    risk_score: int
    effort_multiplier: float
    summary: str
    red_flags: list[RedFlag]
    clarifying_questions: list[str]
    go_no_go: GoNoGo
    rationale: str
    build_status: RunStatus
    test_status: RunStatus
    test_pass_rate: Optional[float] = None
    vulnerability_summary: Optional[VulnerabilitySummary] = None


_RED_FLAG_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "severity": {"type": "string", "enum": [s.value for s in Severity]},
        "evidence": {
            "type": "string",
            "description": (
                "Verbatim snippet from a tool result, or a file:line reference. "
                "Never a paraphrase or a guess."
            ),
        },
        "source": {"type": "string", "enum": [s.value for s in FlagSource]},
        "tool_call_id": {
            "type": ["string", "null"],
            "description": "Required (non-null) when source == tool_output.",
        },
        "file_ref": {"type": ["string", "null"], "description": "e.g. 'src/app.py:120'"},
    },
    "required": ["title", "severity", "evidence", "source", "tool_call_id", "file_ref"],
    "additionalProperties": False,
}

_VULN_SUMMARY_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "critical": {"type": "integer"},
        "high": {"type": "integer"},
        "medium": {"type": "integer"},
        "low": {"type": "integer"},
    },
    "required": ["critical", "high", "medium", "low"],
    "additionalProperties": False,
}

# Full RiskReport schema, usable both as a Structured Output `text.format`
# schema (baseline) and, wrapped with a name/description, as a `submit_report`
# function-tool `parameters` schema (agent).
RISK_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "repo_name": {"type": "string"},
        "risk_score": {
            "type": "integer",
            "description": "0-100, higher = riskier to take on without conditions.",
        },
        "effort_multiplier": {
            "type": "number",
            "description": "Multiply a naive time/price quote by this to account for risk.",
        },
        "summary": {"type": "string"},
        "red_flags": {"type": "array", "items": _RED_FLAG_SCHEMA},
        "clarifying_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Things you could not verify -- ask the client instead of guessing.",
        },
        "go_no_go": {"type": "string", "enum": [g.value for g in GoNoGo]},
        "rationale": {"type": "string"},
        "build_status": {"type": "string", "enum": [s.value for s in RunStatus]},
        "test_status": {"type": "string", "enum": [s.value for s in RunStatus]},
        "test_pass_rate": {"type": ["number", "null"]},
        "vulnerability_summary": _VULN_SUMMARY_SCHEMA,
    },
    "required": [
        "repo_name",
        "risk_score",
        "effort_multiplier",
        "summary",
        "red_flags",
        "clarifying_questions",
        "go_no_go",
        "rationale",
        "build_status",
        "test_status",
        "test_pass_rate",
        "vulnerability_summary",
    ],
    "additionalProperties": False,
}


def structured_output_text_format() -> dict:
    """`text` param value for a plain (no-tools) Responses API call -- note the
    `format` wrapper: `text` is `{"format": {...}, "verbosity": ...}`, not the
    json_schema dict directly."""
    return {
        "format": {
            "type": "json_schema",
            "name": "risk_report",
            "schema": RISK_REPORT_SCHEMA,
            "strict": True,
        }
    }


def submit_report_tool() -> dict:
    """`submit_report` function-tool definition for the agent's tool list."""
    return {
        "type": "function",
        "name": "submit_report",
        "description": (
            "Submit the final risk report and end the assessment. Call this exactly once, "
            "after you have gathered enough tool evidence. Every red_flag must be traceable "
            "to a tool_call_id or file_ref you actually produced."
        ),
        "parameters": RISK_REPORT_SCHEMA,
        "strict": True,
    }
