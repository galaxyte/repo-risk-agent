"""Tool implementations. Each function takes parsed JSON args and returns a
JSON-serializable dict -- the exact thing that gets logged to the trajectory
and (as a compact JSON string) sent back to the model as the tool result.

`read_file` / `list_tree` / `scan_secrets` operate on the host-side pristine
clone directly (pure reads, no code execution -- safe without the sandbox).
`run_command` / `dependency_audit` / `run_linter_or_complexity` execute inside
the Docker sandbox because they run the untrusted repo's own tooling
(installers, linters, npm scripts).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

from agent.sandbox import CommandResult, Sandbox

SECRET_PATTERNS = [
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_header", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    # Anchored to line-start (optional leading whitespace/`const`/`let`/`var`) so this matches
    # real assignments like `password = "hunter2"`, not the word "password" appearing inside an
    # unrelated string literal or prose (e.g. an assert message).
    ("hardcoded_password_assignment", re.compile(
        r"(?im)^\s*(?:const\s+|let\s+|var\s+)?[\w.\[\]'\"]*(?:password|passwd|pwd)[\w]*\s*[:=]\s*['\"][^'\"\n]{4,}['\"]"
    )),
    ("generic_api_key_assignment", re.compile(
        r"(?im)^\s*(?:const\s+|let\s+|var\s+)?[\w.\[\]'\"]*(?:api[_-]?key|secret[_-]?key)[\w]*\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"
    )),
]


class ToolExecutor:
    def __init__(self, repo_path: Path, sandbox: Sandbox):
        self.repo_path = repo_path.resolve()
        self.sandbox = sandbox

    # -- helpers ----------------------------------------------------------

    def _resolve(self, rel_path: str) -> Path | None:
        candidate = (self.repo_path / rel_path).resolve()
        if self.repo_path not in candidate.parents and candidate != self.repo_path:
            return None
        return candidate

    def _cmd_result_dict(self, r: CommandResult) -> dict:
        d = asdict(r)
        return d

    # -- tools --------------------------------------------------------------

    def list_tree(self, path: str = ".", max_depth: int = 3) -> dict:
        root = self._resolve(path)
        if root is None or not root.exists():
            return {"error": f"path not found or outside repo: {path}"}

        entries = []
        base_depth = len(root.parts)
        for p in sorted(root.rglob("*")):
            if any(part in {".git", "node_modules", "__pycache__", ".venv"} for part in p.parts):
                continue
            depth = len(p.parts) - base_depth
            if depth > max_depth:
                continue
            entries.append({
                "path": str(p.relative_to(self.repo_path)),
                "is_dir": p.is_dir(),
                "size_bytes": p.stat().st_size if p.is_file() else None,
            })

        god_files = [
            e["path"] for e in entries
            if not e["is_dir"] and e["size_bytes"] and e["size_bytes"] > 50_000
        ]
        readme = next((p for p in self.repo_path.glob("README*")), None)
        signals = {
            "has_ci_config": (self.repo_path / ".github" / "workflows").exists()
                or (self.repo_path / ".gitlab-ci.yml").exists()
                or (self.repo_path / ".circleci").exists(),
            "has_license": any(self.repo_path.glob("LICENSE*")),
            "has_tests_dir": any(self.repo_path.glob("test*")) or any(self.repo_path.glob("**/tests")),
            "readme_word_count": len(readme.read_text(errors="ignore").split()) if readme else 0,
            "large_files_over_50kb": god_files,
        }
        return {"entries": entries[:500], "truncated": len(entries) > 500, "signals": signals}

    def read_file(self, path: str, max_bytes: int = 20000) -> dict:
        target = self._resolve(path)
        if target is None or not target.exists() or not target.is_file():
            return {"error": f"file not found or outside repo: {path}"}
        data = target.read_bytes()[:max_bytes]
        return {
            "path": path,
            "truncated": target.stat().st_size > max_bytes,
            "content": data.decode("utf-8", errors="replace"),
        }

    def run_command(self, cmd: list[str], cwd: str = ".", timeout_sec: int = 60) -> dict:
        result = self.sandbox.run(cmd, cwd=cwd, timeout_sec=timeout_sec)
        d = self._cmd_result_dict(result)
        # Truncate only here (the model-facing tool), not inside Sandbox.run itself -- other
        # callers (dependency_audit, run_linter_or_complexity) need the full output to parse.
        d["stdout"] = d["stdout"][-20000:]
        d["stderr"] = d["stderr"][-20000:]
        return d

    def dependency_audit(self) -> dict:
        if (self.repo_path / "package-lock.json").exists():
            r = self.sandbox.run(["npm", "audit", "--json"], timeout_sec=180)
            summary = None
            try:
                parsed = json.loads(r.stdout or "{}")
                meta = parsed.get("metadata", {}).get("vulnerabilities", {})
                summary = {
                    "critical": meta.get("critical", 0),
                    "high": meta.get("high", 0),
                    "medium": meta.get("moderate", 0),
                    "low": meta.get("low", 0),
                }
            except json.JSONDecodeError:
                pass
            return {"ecosystem": "npm", "command": ["npm", "audit", "--json"],
                     "exit_code": r.exit_code, "summary": summary,
                     "raw_stdout": r.stdout[-4000:], "raw_stderr": r.stderr[-2000:]}

        if (self.repo_path / "requirements.txt").exists():
            r = self.sandbox.run(["pip-audit", "-r", "requirements.txt", "-f", "json"], timeout_sec=180)
            summary = None
            try:
                parsed = json.loads(r.stdout or "[]")
                deps = parsed if isinstance(parsed, list) else parsed.get("dependencies", [])
                n_vulns = sum(len(d.get("vulns", [])) for d in deps)
                summary = {"critical": 0, "high": 0, "medium": 0, "low": n_vulns}
            except (json.JSONDecodeError, AttributeError):
                pass
            return {"ecosystem": "pip", "command": ["pip-audit", "-r", "requirements.txt", "-f", "json"],
                     "exit_code": r.exit_code, "summary": summary,
                     "raw_stdout": r.stdout[-4000:], "raw_stderr": r.stderr[-2000:]}

        if (self.repo_path / "pyproject.toml").exists() or (self.repo_path / "setup.py").exists():
            # No requirements.txt to hand pip-audit directly. This only finds anything if
            # `pip install .` already ran in this sandbox session (e.g. via run_command),
            # since it audits the environment's currently-installed packages.
            r = self.sandbox.run(["pip-audit", "-f", "json"], timeout_sec=180)
            summary = None
            try:
                parsed = json.loads(r.stdout or "[]")
                deps = parsed if isinstance(parsed, list) else parsed.get("dependencies", [])
                n_vulns = sum(len(d.get("vulns", [])) for d in deps)
                summary = {"critical": 0, "high": 0, "medium": 0, "low": n_vulns}
            except (json.JSONDecodeError, AttributeError):
                pass
            return {"ecosystem": "pip_installed_env", "command": ["pip-audit", "-f", "json"],
                     "exit_code": r.exit_code, "summary": summary,
                     "note": "No requirements.txt; audited whatever is currently installed in the sandbox. "
                             "Run an install step first for this to be meaningful.",
                     "raw_stdout": r.stdout[-4000:], "raw_stderr": r.stderr[-2000:]}

        return {"ecosystem": "unsupported_or_undetected",
                "note": "No package-lock.json, requirements.txt, pyproject.toml, or setup.py found at "
                        "repo root; dependency audit not attempted. Treat dependency risk as unverified."}

    def run_linter_or_complexity(self) -> dict:
        py_files = list(self.repo_path.rglob("*.py"))
        js_files = list(self.repo_path.rglob("*.js")) + list(self.repo_path.rglob("*.ts"))

        if py_files:
            lint = self.sandbox.run(["ruff", "check", "--output-format", "json", "."], timeout_sec=120)
            complexity = self.sandbox.run(["radon", "cc", "-s", "-j", "."], timeout_sec=120)
            return {
                "language": "python",
                "ruff_exit_code": lint.exit_code,
                "ruff_output": self._summarize_ruff(lint.stdout),
                "radon_exit_code": complexity.exit_code,
                "radon_output": self._summarize_radon(complexity.stdout),
            }

        if js_files:
            has_eslint_config = any(self.repo_path.glob(".eslintrc*")) or any(
                "eslintConfig" in (self.repo_path / "package.json").read_text(errors="ignore")
                for _ in [0] if (self.repo_path / "package.json").exists()
            )
            if has_eslint_config:
                lint = self.sandbox.run(["npx", "--yes", "eslint", ".", "-f", "json"], timeout_sec=180)
                return {"language": "javascript", "eslint_exit_code": lint.exit_code,
                        "eslint_output": lint.stdout[-4000:], "eslint_stderr": lint.stderr[-2000:]}
            return self._heuristic_complexity(js_files, "javascript_no_eslint_config")

        all_files = [p for p in self.repo_path.rglob("*") if p.is_file()]
        return self._heuristic_complexity(all_files, "unsupported_language_heuristic_fallback")

    def _summarize_ruff(self, raw_stdout: str) -> dict:
        # Naive tail-truncation can bury the most important entries (e.g. a
        # syntax error in a small file) behind thousands of style nits in a
        # large one. Parse instead, surface every syntax/error-severity finding
        # in full, and sample the rest -- so nothing critical falls off the edge.
        try:
            violations = json.loads(raw_stdout or "[]")
        except json.JSONDecodeError:
            return {"parse_error": True, "raw_tail": raw_stdout[-4000:]}

        # Note: ruff's JSON sets "severity": "error" on essentially every diagnostic
        # regardless of category, so it can't distinguish a real parse failure from
        # a style nit -- only "code" == "invalid-syntax" actually means that.
        is_critical = lambda v: v.get("code") == "invalid-syntax"
        critical = [v for v in violations if is_critical(v)]
        other = [v for v in violations if not is_critical(v)]

        def _compact(v: dict) -> dict:
            return {"file": v.get("filename"), "line": v.get("location", {}).get("row"),
                    "code": v.get("code"), "message": v.get("message")}

        return {
            "total_violations": len(violations),
            "syntax_or_error_severity_count": len(critical),
            "syntax_or_error_severity_findings": [_compact(v) for v in critical],
            "other_findings_sample": [_compact(v) for v in other[:20]],
            "other_findings_omitted": max(0, len(other) - 20),
        }

    def _summarize_radon(self, raw_stdout: str) -> dict:
        try:
            parsed = json.loads(raw_stdout or "{}")
        except json.JSONDecodeError:
            return {"parse_error": True, "raw_tail": raw_stdout[-4000:]}

        functions = []
        files_with_errors = []
        for filename, entries in parsed.items():
            if isinstance(entries, dict) and "error" in entries:
                # radon couldn't parse this file at all (e.g. Python 2-only syntax) --
                # that failure is itself a finding, not something to silently skip.
                files_with_errors.append({"file": filename, "error": entries["error"]})
                continue
            for e in entries:
                if e.get("type") in ("function", "method"):
                    functions.append({"file": filename, "name": e.get("name"), "line": e.get("lineno"),
                                       "complexity": e.get("complexity"), "rank": e.get("rank")})

        functions.sort(key=lambda f: -(f.get("complexity") or 0))
        rank_counts: dict[str, int] = {}
        for f in functions:
            rank_counts[f["rank"]] = rank_counts.get(f["rank"], 0) + 1

        return {
            "total_functions": len(functions),
            "rank_counts": rank_counts,
            "most_complex_functions": functions[:15],
            "files_radon_could_not_parse": files_with_errors,
        }

    def _heuristic_complexity(self, files: list[Path], label: str) -> dict:
        sizes = []
        for f in files:
            try:
                sizes.append((str(f.relative_to(self.repo_path)), len(f.read_text(errors="ignore").splitlines())))
            except OSError:
                continue
        sizes.sort(key=lambda t: -t[1])
        return {
            "language": label,
            "note": "No project-specific linter run; reporting line-count heuristic only.",
            "largest_files_by_line_count": sizes[:10],
        }

    def scan_secrets(self) -> dict:
        findings = []
        for p in self.repo_path.rglob("*"):
            if not p.is_file() or p.stat().st_size > 2_000_000:
                continue
            if any(part in {".git", "node_modules", ".venv"} for part in p.parts):
                continue
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            for label, pattern in SECRET_PATTERNS:
                m = pattern.search(text)
                if m:
                    findings.append({
                        "pattern": label,
                        "file": str(p.relative_to(self.repo_path)),
                        "evidence": text[max(0, m.start() - 20):m.end() + 20],
                    })

        git_history_hits = []
        try:
            proc = subprocess.run(
                ["git", "log", "--all", "-p", "-n", "200"],
                cwd=self.repo_path, capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace",  # history may contain non-UTF-8/binary diffs
            )
            for m in re.finditer(r"AKIA[0-9A-Z]{16}", proc.stdout or ""):
                git_history_hits.append({"pattern": "aws_access_key_id", "location": "git_history", "evidence": m.group(0)})
        except (subprocess.TimeoutExpired, OSError):
            pass

        return {"working_tree_findings": findings[:50], "git_history_findings": git_history_hits[:20]}
