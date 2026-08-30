# Baseline vs. Agent Ablation Ladder — Results

n = 8 repos scored (0 failed, see `_run_status.json`)

| Metric | Simple Baseline | Agent — tools, no verification contract | Agent — verification contract, no self-check | Agent — full (contract + self-check) |
|---|---|---|---|---|
| Spearman rank corr. vs. expert risk ranking | 0.71 | 0.97 | 0.85 | 0.99 |
| Groundedness (Layer 2, % of *non-abstained* claims verified against reference pass) | 62% | 69% | 74% | 72% |
| Abstain rate (% of the 4 checkable claims left as "unknown"/null instead of asserted) | 75% | 53% | 25% | 31% |
| Go/No-Go agreement with expert | 38% | 50% | 50% | 50% |
| Avg cost / repo | N/A | N/A | N/A | N/A |
| Avg wall time / repo (sec) | 50.80 | 109.21 | 134.36 | 131.06 |

Agent-only Layer 1 evidence traceability (fraction of tool_output red flags whose cited tool_call_id/evidence actually appear in that run's own trajectory — no baseline analog, since baseline has no tools):

| Agent — tools, no verification contract | Agent — verification contract, no self-check | Agent — full (contract + self-check) |
|---|---|---|
| 11% | 53% | 100% |

See `summary.csv` for per-repo detail.