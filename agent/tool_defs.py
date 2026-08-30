"""OpenAI Responses API function-tool definitions for the agent.

All schemas use strict mode (every property required, additionalProperties
false) per OpenAI's Structured Outputs / strict function-calling requirements.
Tools with no meaningful arguments still declare an empty required object so
the schema is valid strict JSON Schema.
"""
from __future__ import annotations

from shared.schema import submit_report_tool

_EMPTY_STRICT_PARAMS = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

LIST_TREE = {
    "type": "function",
    "name": "list_tree",
    "description": (
        "List files under a path in the repo (skips .git/node_modules/__pycache__/.venv). "
        "Also reports structural signals: CI config presence, LICENSE presence, tests dir "
        "presence, README word count, and files over 50KB (god-file candidates)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path from repo root. Use '.' for the root."},
            "max_depth": {"type": "integer", "description": "Max directory depth to descend. Use 3 unless you need more."},
        },
        "required": ["path", "max_depth"],
        "additionalProperties": False,
    },
    "strict": True,
}

READ_FILE = {
    "type": "function",
    "name": "read_file",
    "description": "Read a text file's contents from the repo, up to max_bytes.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path from repo root."},
            "max_bytes": {"type": "integer", "description": "Byte cap. Use 20000 unless you have a reason for more."},
        },
        "required": ["path", "max_bytes"],
        "additionalProperties": False,
    },
    "strict": True,
}

RUN_COMMAND = {
    "type": "function",
    "name": "run_command",
    "description": (
        "Run a command inside the sandboxed container against the repo (e.g. install deps, "
        "build, run tests). cmd is an argv list, never a shell string (no pipes/semicolons/"
        "redirects -- pass each argument as a separate array element). Only an allowlisted set "
        "of executables (npm, node, pip, python, pytest, git, make, ls, cat, find, grep, etc.) "
        "will actually execute; anything else returns an error result rather than running. "
        "Always check exit_code and timed_out before treating a claim as verified."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "cmd": {"type": "array", "items": {"type": "string"}, "description": "Argv, e.g. [\"pytest\", \"-q\"]."},
            "cwd": {"type": "string", "description": "Working directory relative to repo root. Use '.' for root."},
            "timeout_sec": {
                "type": "integer",
                "description": (
                    "Timeout in seconds, max 300. Use 60 for most commands (tests, lint, quick "
                    "installs); only go higher (up to 180) for a dependency install you expect to "
                    "be slow. You have a limited overall time budget -- don't default to the max."
                ),
            },
        },
        "required": ["cmd", "cwd", "timeout_sec"],
        "additionalProperties": False,
    },
    "strict": True,
}

DEPENDENCY_AUDIT = {
    "type": "function",
    "name": "dependency_audit",
    "description": (
        "Auto-detects the dependency ecosystem (npm via package-lock.json, pip via "
        "requirements.txt) and runs the matching vulnerability audit tool inside the sandbox. "
        "Returns a parsed severity summary when possible plus raw tool output. If no supported "
        "lockfile is found, returns ecosystem='unsupported_or_undetected' -- treat dependency "
        "risk as unverified in that case, do not guess a vulnerability count."
    ),
    "parameters": _EMPTY_STRICT_PARAMS,
    "strict": True,
}

RUN_LINTER_OR_COMPLEXITY = {
    "type": "function",
    "name": "run_linter_or_complexity",
    "description": (
        "Auto-detects language (Python -> ruff+radon, JS/TS with an eslint config -> eslint) "
        "and runs static analysis inside the sandbox. Falls back to a line-count heuristic over "
        "the largest files if no supported linter applies -- the heuristic result is NOT a real "
        "lint/complexity finding and should be treated as weaker evidence."
    ),
    "parameters": _EMPTY_STRICT_PARAMS,
    "strict": True,
}

SCAN_SECRETS = {
    "type": "function",
    "name": "scan_secrets",
    "description": (
        "Regex-scans the working tree (AWS keys, private key headers, hardcoded "
        "password/API-key assignments) and the last 200 commits of git history for AWS key "
        "patterns that may have been committed and later removed. Runs directly on the host "
        "clone (no code execution), not inside the sandbox."
    ),
    "parameters": _EMPTY_STRICT_PARAMS,
    "strict": True,
}

ALL_TOOLS = [
    LIST_TREE,
    READ_FILE,
    RUN_COMMAND,
    DEPENDENCY_AUDIT,
    RUN_LINTER_OR_COMPLEXITY,
    SCAN_SECRETS,
    submit_report_tool(),
]
