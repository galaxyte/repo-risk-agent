"""Docker-backed sandbox for running untrusted repo build/test/audit commands.

Design (see plan / README for the full justification):
- One throwaway container per repo, started from docker/Dockerfile.sandbox.
- The repo is COPIED into the container (docker cp), never bind-mounted live,
  so nothing the untrusted repo's build/install scripts do can touch the host
  filesystem or persist after the container is destroyed.
- Every command is executed as an argv list via `docker exec` -- never through
  a shell string -- and only if its first token is in COMMAND_ALLOWLIST. This
  closes off shell-injection ("; rm -rf /", "curl | sh") structurally rather
  than via a denylist regex.
- Resource limits (memory/cpu/pids) and a non-root user bound the blast radius
  of a misbehaving or malicious build script. See README "Known limitations"
  for the residual risk this does NOT cover (a malicious postinstall/pip
  install script phoning out over the network during dependency install).
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

IMAGE_NAME = "repo-risk-sandbox:latest"

COMMAND_ALLOWLIST = {
    "npm", "npx", "node",
    "pip", "pip3", "pip-audit", "python", "python3", "pytest", "ruff", "radon",
    "git", "make",
    "ls", "cat", "find", "grep", "wc", "head", "tail", "test", "which", "echo",
}


class SandboxUnavailable(RuntimeError):
    """Raised when Docker is not usable on this machine."""


@dataclass
class CommandResult:
    argv: list[str]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_sec: float


def docker_available() -> bool:
    return shutil.which("docker") is not None


def _image_exists() -> bool:
    result = subprocess.run(["docker", "image", "inspect", IMAGE_NAME], capture_output=True, text=True)
    return result.returncode == 0


def ensure_image_built(dockerfile: Path) -> None:
    # Every assess_repo() call used to re-invoke `docker build` unconditionally --
    # even with layer caching that's a wasted daemon round-trip on every single
    # repo. Skip it entirely once the image already exists; rebuild only when it
    # doesn't (first run, or after `docker rmi`).
    if _image_exists():
        return
    subprocess.run(
        ["docker", "build", "-q", "-t", IMAGE_NAME, "-f", str(dockerfile), str(dockerfile.parent)],
        check=True,
        capture_output=True,
        text=True,
    )


class Sandbox:
    """One Docker container scoped to a single repo assessment session."""

    def __init__(self, repo_source: Path, *, memory: str = "1g", cpus: str = "1.0", pids_limit: int = 256):
        if not docker_available():
            raise SandboxUnavailable("docker binary not found on PATH")
        self.repo_source = repo_source
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.container_name = f"repo-risk-{uuid.uuid4().hex[:12]}"
        self._started = False

    def start(self) -> None:
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", self.container_name,
                "--memory", self.memory,
                "--cpus", self.cpus,
                "--pids-limit", str(self.pids_limit),
                "--cap-drop", "ALL",
                "--cap-add", "CHOWN",  # only exception: needed to chown the copied-in repo to sandboxuser
                "--security-opt", "no-new-privileges",
                "--network", "bridge",
                IMAGE_NAME,
                "sleep", "infinity",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self._started = True
        # Copy the repo in (not a live bind mount) so nothing run inside can
        # write back to the host's clone.
        subprocess.run(
            ["docker", "exec", "-u", "root", self.container_name, "mkdir", "-p", "/workspace"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["docker", "cp", f"{self.repo_source}/.", f"{self.container_name}:/workspace"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["docker", "exec", "-u", "root", self.container_name, "chown", "-R", "sandboxuser:sandboxuser", "/workspace"],
            check=True, capture_output=True, text=True,
        )

    def run(self, argv: list[str], *, cwd: str = ".", timeout_sec: int = 60) -> CommandResult:
        if not self._started:
            raise RuntimeError("Sandbox.start() must be called before run()")
        if not argv:
            raise ValueError("empty command")
        if argv[0] not in COMMAND_ALLOWLIST:
            return CommandResult(
                argv=argv, cwd=cwd, exit_code=None, stdout="",
                stderr=f"command '{argv[0]}' is not in the sandbox allowlist: {sorted(COMMAND_ALLOWLIST)}",
                timed_out=False, duration_sec=0.0,
            )
        # Cap stays generous (unlike the agent's own default suggestion below) --
        # eval/reference_pass.py legitimately needs longer install/test timeouts
        # for larger repos and calls this with explicit values up to 240s.
        timeout_sec = min(timeout_sec, 300)
        full_cmd = ["docker", "exec", "-w", f"/workspace/{cwd}".replace("//", "/"), self.container_name, *argv]
        start = time.monotonic()
        try:
            # Deliberately NOT truncated here: dependency_audit/run_linter_or_complexity need the
            # full JSON output to parse correctly (audit reports on real repos regularly exceed
            # 20KB). Truncation for anything sent back to the model happens at the call site
            # instead (ToolExecutor.run_command truncates its own returned dict; run_agent.py
            # truncates the final serialized tool result before it goes over the wire).
            proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout_sec)
            return CommandResult(
                argv=argv, cwd=cwd, exit_code=proc.returncode,
                stdout=proc.stdout, stderr=proc.stderr,
                timed_out=False, duration_sec=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired as e:
            return CommandResult(
                argv=argv, cwd=cwd, exit_code=None,
                stdout=(e.stdout or ""), stderr=f"command timed out after {timeout_sec}s",
                timed_out=True, duration_sec=time.monotonic() - start,
            )

    def stop(self) -> None:
        if self._started:
            subprocess.run(["docker", "rm", "-f", self.container_name], capture_output=True, text=True)
            self._started = False

    def __enter__(self) -> "Sandbox":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


class NullSandbox:
    """Fallback used when Docker isn't available (e.g. a judge's machine).

    Any tool that needs real command execution gets a clear, honest error
    result instead of a crash. Static-only tools (read_file/list_tree/
    scan_secrets) still work since they don't go through the sandbox at all.
    This degrades the agent to something close to the baseline for
    build/test/dependency claims, which is itself a legitimate (if weaker)
    data point rather than a hard failure.
    """

    def start(self) -> None:
        return None

    def run(self, argv: list[str], *, cwd: str = ".", timeout_sec: int = 120) -> CommandResult:
        return CommandResult(
            argv=argv, cwd=cwd, exit_code=None, stdout="",
            stderr="sandbox unavailable on this machine (Docker not found) -- command not run",
            timed_out=False, duration_sec=0.0,
        )

    def stop(self) -> None:
        return None

    def __enter__(self) -> "NullSandbox":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None
