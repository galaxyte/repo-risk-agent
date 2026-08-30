# Reproduction Guide

Written for a clean environment. Every command below is run from the `repo-risk-agent/` directory.

## Requirements

- Python 3.11+ (built/tested on 3.14)
- Docker (built/tested on Docker 29.7.2) — **optional but strongly recommended**. Without it, the agent automatically falls back to static-analysis-only tools (no real build/test/dependency-audit execution), which weakens its results but does not crash the run.
- Git
- An OpenAI API key with access to the model set in `OPENAI_MODEL` (default `gpt-5`)
- macOS/Linux shell (commands below use `bash`/`zsh` syntax)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set OPENAI_API_KEY=sk-... (and optionally OPENAI_MODEL, pricing vars)

docker build -q -t repo-risk-sandbox:latest -f docker/Dockerfile.sandbox docker/
```

**Safety note (read before running):** the agent and reference pass will clone the 8 public repos in `data/repos.json` and run their own build/install/test/lint/dependency-audit tooling **inside a disposable Docker container** (resource-limited, non-root, no host credentials, repo copied in rather than live-mounted — see `agent/sandbox.py`). This is "consequential" in the sense that it executes third-party code, which is why it's sandboxed; running the commands below is the one explicit decision to do that. No action outside the container touches your host filesystem beyond the `workspace/` and `results/` directories created by these scripts.

## Data

No manual data download needed — `data/repos.json` lists 8 public GitHub repos (cloned fresh by the scripts below into `workspace/`, gitignored, not redistributed) spanning well-maintained, borderline, abandoned, and deliberately-vulnerable categories. `data/expert_ranking.json` is the frozen ground-truth ranking (risk score, rank, go/no-go, and the rubric used), written **before** any baseline/agent run.

## Run the baseline (single repo)

```bash
python -m baseline.baseline_assess --repo-path workspace/click --repo-name click --out-dir results/raw/click
```

(First clone the repo if `workspace/click` doesn't exist yet: `git clone --depth 1 https://github.com/pallets/click.git workspace/click`.)

Expected output: `results/raw/click/baseline_report.json`, `baseline_meta.json`, `baseline_input.txt` (the exact file dump the model saw), plus a JSON summary printed to stdout. Runtime: ~10-20s. Cost: a few cents at most.

## Run the agent (single repo)

```bash
python -m agent.run_agent --repo-path workspace/click --repo-name click --out-dir results/raw/click/agent_full --variant full
```

`--variant` selects a point on the ablation ladder used for the changelog: `no_contract` (tools, no evidence mandate), `no_selfcheck` (evidence/tool_call_id contract, no self-check repair), `full` (the shipped agent — contract + self-check). Expected output: `agent_report.json`, `agent_meta.json`, `agent_trajectory.jsonl` (every tool call and result, one JSON object per line — this is the "agent trajectory" deliverable) in the `--out-dir`. Runtime: ~2-6 minutes per repo per variant. Cost: tens of cents per repo per variant, depending on model pricing.

## Run the full evaluation (all 8 repos × baseline + 3 agent variants + scoring)

```bash
python -m eval.run_eval --repos-file data/repos.json --out-dir results/raw
python -m eval.score --repos-file data/repos.json --expert-file data/expert_ranking.json --raw-dir results/raw --out-dir results
```

Expected output per repo: `results/raw/<repo>/reference.json`, `baseline_report.json` + `baseline_meta.json`, and `agent_<variant>/{agent_report.json, agent_meta.json, agent_trajectory.jsonl}` for each of `no_contract`/`no_selfcheck`/`full`. Plus `results/summary.csv` (per-repo detail across all 4 systems) and `results/summary.md` (the headline comparison table — this doubles as the evidence behind each CHANGELOG.md entry). `eval/run_eval.py` continues past any single repo that fails (network hiccup, repo removed upstream, etc.) and records the failure in `results/raw/_run_status.json` rather than aborting the whole run.

## Approximate runtime & cost

- Full run (8 repos × baseline + 3 agent variants): **~60-120 minutes wall time** (dominated by `npm install`/`pip install` attempts inside the sandbox, run once per variant), **roughly $10-15** in OpenAI API cost at typical `gpt-5` pricing (3 agent variants × 8 repos, baseline is near-free) — set `OPENAI_INPUT_PRICE_PER_MTOK` / `OPENAI_OUTPUT_PRICE_PER_MTOK` in `.env` from [platform.openai.com/pricing](https://platform.openai.com/docs/pricing) for your model to get a real cost number in `results/summary.md`; left blank, cost is reported as `N/A`.
- Re-running `eval/score.py` alone (no API calls, just re-scoring existing `results/raw/`) is near-instant and free.

## Troubleshooting

- **Docker not installed/running**: the agent and reference pass automatically fall back to static-only tools (`NullSandbox` in `agent/sandbox.py`) — you'll still get a full run, but `build_status`/`test_status`/`dependency_audit` will mostly come back `unknown` rather than verified, which will lower the agent's measured groundedness advantage. Install/start Docker for the full result.
- **OpenAI rate limits / transient errors**: `eval/run_eval.py` catches per-repo exceptions and continues; re-run it — it re-clones only if `workspace/<repo>` is missing, so a re-run mostly just retries the failed repo's LLM calls.
- **A repo listed in `data/repos.json` has been removed/renamed upstream**: this is a real risk of pinning to public GitHub repos; note it in your own run and substitute an equivalent repo from the same category if needed, updating `data/expert_ranking.json` accordingly before re-scoring.
