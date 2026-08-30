"""The agent solution: a tool-using OpenAI Responses API loop that assesses a
repo's risk by actually running verification commands, not just reading files.

CLI:
    python -m agent.run_agent --repo-path workspace/requests --repo-name requests \
        --out-dir results/raw/requests

Library:
    assess_repo(repo_path, repo_name, client, model, out_dir) -> (report_dict, meta)
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from agent.prompts import NO_CONTRACT_SYSTEM_PROMPT, SYSTEM_PROMPT
from agent.sandbox import NullSandbox, Sandbox, docker_available, ensure_image_built
from agent.tool_defs import ALL_TOOLS
from agent.tools import ToolExecutor
from agent.verify import self_check_and_repair
from shared.schema import RiskReport, submit_report_tool

MAX_TURNS = 15
WALL_CLOCK_BUDGET_SEC = 480
FORCE_SUBMIT_FRACTION = 0.75

# Ablation variants used to build genuine changelog evidence cheaply (same tool
# infra, different system prompt / post-processing). See CHANGELOG.md.
#   no_contract  -- tools available, no evidence/tool_call_id mandate, no self-check.
#   no_selfcheck -- full verification-contract prompt, but the self-check repair pass is skipped.
#   full         -- verification-contract prompt + self-check repair pass (the shipped agent).
VARIANTS = {
    "no_contract": (NO_CONTRACT_SYSTEM_PROMPT, False),
    "no_selfcheck": (SYSTEM_PROMPT, False),
    "full": (SYSTEM_PROMPT, True),
}


def _dispatch(executor: ToolExecutor, name: str, args: dict) -> dict:
    try:
        if name == "list_tree":
            return executor.list_tree(**args)
        if name == "read_file":
            return executor.read_file(**args)
        if name == "run_command":
            return executor.run_command(**args)
        if name == "dependency_audit":
            return executor.dependency_audit()
        if name == "run_linter_or_complexity":
            return executor.run_linter_or_complexity()
        if name == "scan_secrets":
            return executor.scan_secrets()
        return {"error": f"unknown tool: {name}"}
    except Exception as e:  # noqa: BLE001 -- must never crash the loop on a bad tool call
        return {"error": f"tool execution raised: {type(e).__name__}: {e}"}


def assess_repo(
    repo_path: Path,
    repo_name: str,
    client: OpenAI,
    model: str,
    out_dir: Path,
    max_turns: int = MAX_TURNS,
    wall_clock_budget_sec: int = WALL_CLOCK_BUDGET_SEC,
    variant: str = "full",
) -> tuple[dict, dict]:
    system_prompt, apply_self_check = VARIANTS[variant]
    out_dir.mkdir(parents=True, exist_ok=True)
    trajectory_path = out_dir / "agent_trajectory.jsonl"

    if docker_available():
        try:
            ensure_image_built(Path(__file__).resolve().parent.parent / "docker" / "Dockerfile.sandbox")
            sandbox = Sandbox(repo_path)
        except Exception:
            sandbox = NullSandbox()
    else:
        sandbox = NullSandbox()

    total_input_tokens = 0
    total_output_tokens = 0
    final_report_args: dict | None = None
    start = time.monotonic()

    with sandbox:
        executor = ToolExecutor(repo_path, sandbox)
        input_items = [{
            "role": "user",
            "content": (
                f"Assess repo: {repo_name}. The repo's files are available to you via "
                f"list_tree/read_file, and its sandbox working directory is /workspace for "
                f"run_command. Begin investigating."
            ),
        }]
        previous_response_id = None

        with open(trajectory_path, "w") as traj_f:
            for turn in range(1, max_turns + 1):
                elapsed = time.monotonic() - start
                force_submit = elapsed > FORCE_SUBMIT_FRACTION * wall_clock_budget_sec or turn == max_turns
                call_tools = [submit_report_tool()] if force_submit else ALL_TOOLS
                tool_choice = {"type": "function", "name": "submit_report"} if force_submit else "required"

                resp = client.responses.create(
                    model=model,
                    instructions=system_prompt,
                    input=input_items,
                    tools=call_tools,
                    tool_choice=tool_choice,
                    parallel_tool_calls=not force_submit,
                    reasoning={"effort": "medium"},
                    previous_response_id=previous_response_id,
                )
                previous_response_id = resp.id
                if resp.usage:
                    total_input_tokens += resp.usage.input_tokens
                    total_output_tokens += resp.usage.output_tokens

                function_calls = [item for item in resp.output if item.type == "function_call"]

                turn_record = {
                    "turn": turn,
                    "elapsed_sec": round(elapsed, 1),
                    "response_id": resp.id,
                    "forced_submit": force_submit,
                    "function_calls": [
                        {"call_id": fc.call_id, "name": fc.name, "arguments": fc.arguments}
                        for fc in function_calls
                    ],
                    "tool_results": [],
                }

                if not function_calls:
                    traj_f.write(json.dumps(turn_record) + "\n")
                    input_items = [{
                        "role": "user",
                        "content": "You must call a tool on every turn. Call submit_report if you have enough evidence, otherwise continue investigating.",
                    }]
                    continue

                next_input = []
                done = False
                for fc in function_calls:
                    try:
                        args = json.loads(fc.arguments) if fc.arguments else {}
                    except json.JSONDecodeError:
                        args = {}

                    if fc.name == "submit_report":
                        final_report_args = args
                        done = True
                        turn_record["tool_results"].append({"call_id": fc.call_id, "name": fc.name, "result": "TERMINAL"})
                        continue

                    result = _dispatch(executor, fc.name, args)
                    turn_record["tool_results"].append({"call_id": fc.call_id, "name": fc.name, "result": result})
                    next_input.append({
                        "type": "function_call_output",
                        "call_id": fc.call_id,
                        "output": json.dumps(result)[:20000],
                    })

                traj_f.write(json.dumps(turn_record) + "\n")

                if done:
                    break
                input_items = next_input

    meta = {
        "repo_name": repo_name,
        "model": model,
        "variant": variant,
        "turns_used": turn,
        "wall_clock_sec": round(time.monotonic() - start, 1),
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "sandbox_available": not isinstance(sandbox, NullSandbox),
        "reached_submit_report": final_report_args is not None,
    }

    if final_report_args is None:
        final_report_args = {
            "repo_name": repo_name, "risk_score": 100, "effort_multiplier": 1.0,
            "summary": "Agent exhausted its turn/time budget without calling submit_report.",
            "red_flags": [], "clarifying_questions": ["Assessment incomplete -- rerun with a larger budget."],
            "go_no_go": "no_go", "rationale": "Incomplete assessment.",
            "build_status": "unknown", "test_status": "unknown",
            "test_pass_rate": None, "vulnerability_summary": None,
        }
    else:
        RiskReport.model_validate(final_report_args)  # raises if the model's final args don't match schema
        if apply_self_check:
            final_report_args = self_check_and_repair(final_report_args, trajectory_path)

    (out_dir / "agent_report.json").write_text(json.dumps(final_report_args, indent=2))
    (out_dir / "agent_meta.json").write_text(json.dumps(meta, indent=2))
    return final_report_args, meta


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the repo-risk agent on a single, already-cloned repo.")
    parser.add_argument("--repo-path", required=True, type=Path)
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5"))
    parser.add_argument("--variant", default="full", choices=list(VARIANTS.keys()))
    args = parser.parse_args()

    client = OpenAI()
    report, meta = assess_repo(args.repo_path, args.repo_name, client, args.model, args.out_dir, variant=args.variant)
    print(json.dumps({"report": report, "meta": meta}, indent=2))


if __name__ == "__main__":
    main()
