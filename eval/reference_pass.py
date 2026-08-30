"""Independent ground-truth pass: no LLM involved at all.

Runs the same category of verification (install/build/test attempt,
dependency audit, secret scan, structural signals) directly via ToolExecutor,
kept completely separate from anything baseline or agent ever see. This is
what groundedness is scored against, so it must not be influenced by either
system's output (scored *before* either runs, from a fresh clone).
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.sandbox import NullSandbox, Sandbox, docker_available, ensure_image_built
from agent.tools import ToolExecutor


def _attempt_build_test(executor: ToolExecutor, repo_path: Path) -> dict:
    result = {"build_status": "not_attempted", "test_status": "not_attempted", "test_pass_rate": None, "log": []}

    if (repo_path / "package.json").exists():
        install = executor.run_command(cmd=["npm", "install", "--no-audit", "--no-fund"], cwd=".", timeout_sec=240)
        result["log"].append({"step": "npm_install", **install})
        result["build_status"] = "passed" if install["exit_code"] == 0 else "failed"
        if install["exit_code"] == 0:
            test = executor.run_command(cmd=["npm", "test", "--if-present"], cwd=".", timeout_sec=180)
            result["log"].append({"step": "npm_test", **test})
            result["test_status"] = "passed" if test["exit_code"] == 0 else "failed"
        return result

    if (repo_path / "requirements.txt").exists() or (repo_path / "setup.py").exists() or (repo_path / "pyproject.toml").exists():
        if (repo_path / "requirements.txt").exists():
            install_cmd = ["pip", "install", "--break-system-packages", "-q", "-r", "requirements.txt"]
        else:
            # setup.py / pyproject.toml only, e.g. a modern Flit/Hatch/setuptools package.
            install_cmd = ["pip", "install", "--break-system-packages", "-q", "."]
        install = executor.run_command(cmd=install_cmd, cwd=".", timeout_sec=240)
        result["log"].append({"step": "pip_install", **install})
        result["build_status"] = "passed" if install["exit_code"] == 0 else "failed"

        has_tests = (
            any(repo_path.rglob("test_*.py")) or any(repo_path.rglob("*_test.py"))
            or any(repo_path.rglob("tests.py")) or (repo_path / "tests").is_dir() or (repo_path / "test").is_dir()
        )
        if has_tests:
            test = executor.run_command(cmd=["pytest", "-q"], cwd=".", timeout_sec=180)
            result["log"].append({"step": "pytest", **test})
            result["test_status"] = "passed" if test["exit_code"] == 0 else "failed"
        return result

    result["build_status"] = "unknown"
    result["test_status"] = "unknown"
    return result


def run_reference_pass(repo_path: Path, repo_name: str, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    if docker_available():
        try:
            ensure_image_built(Path(__file__).resolve().parent.parent / "docker" / "Dockerfile.sandbox")
            sandbox = Sandbox(repo_path)
        except Exception:
            sandbox = NullSandbox()
    else:
        sandbox = NullSandbox()

    with sandbox:
        executor = ToolExecutor(repo_path, sandbox)
        build_test = _attempt_build_test(executor, repo_path)
        dependency_audit = executor.dependency_audit()
        lint = executor.run_linter_or_complexity()
        secrets = executor.scan_secrets()
        tree = executor.list_tree(path=".", max_depth=3)

    reference = {
        "repo_name": repo_name,
        "build_status": build_test["build_status"],
        "test_status": build_test["test_status"],
        "build_test_log": build_test["log"],
        "dependency_audit": dependency_audit,
        "lint": lint,
        "secrets_found": bool(secrets["working_tree_findings"] or secrets["git_history_findings"]),
        "secrets_raw": secrets,
        "signals": tree.get("signals", {}),
    }
    (out_dir / "reference.json").write_text(json.dumps(reference, indent=2))
    return reference
