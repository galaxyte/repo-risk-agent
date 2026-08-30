# Improvement Changelog

Every stage below was run on the same 8 repos (`data/repos.json`), scored against the same
frozen expert ranking (`data/expert_ranking.json`, written before any system saw the repos) and
the same independent reference pass (`eval/reference_pass.py`), using the same model
(`gpt-5`) for every system. Full numbers: `results/summary.md` / `results/summary.csv`. This
file is the narrative; that file is the evidence.

| Metric | Simple Baseline | Agent — tools, no contract | Agent — contract, no self-check | Agent — full |
|---|---|---|---|---|
| Spearman rank corr. vs. expert risk ranking | 0.71 | 0.97 | 0.85 | **0.99** |
| Groundedness (Layer 2, % of non-abstained claims verified against reference) | 62% | 69% | 74% | 72% |
| Abstain rate (build/test/vuln/secret claims left unresolved) | 75% | 53% | 25% | 31% |
| Go/No-Go agreement with expert | 38% | 50% | 50% | 50% |
| Layer 1 evidence traceability (tool_output red flags actually verifiable) | n/a | 11% | 53% | **100%** |
| Avg wall time / repo | 51s | 109s | 134s | 131s |

## Baseline — one LLM call, zero tools

**What & why:** `baseline/baseline_assess.py`. A bounded file dump (README, manifests, CI
config, largest source files, sample tests) handed to the model in a single call, explicitly
told not to assert build/test/vulnerability status unless a file states it. This is the
realistic shortcut a freelancer under time pressure actually takes.

**Evidence:** Spearman 0.71, groundedness 62%, but a 75% abstain rate — the model mostly
(correctly) refuses to assert build/test/vulnerability status it can't see, because it was told
not to guess. It is calibrated but largely unhelpful on exactly the questions a freelancer needs
answered before quoting.

**Decision:** established the floor. The interesting number here isn't groundedness (which
abstaining inflates almost for free) — it's the abstain rate. A trustworthy-but-silent system
isn't the goal; the rest of this changelog is about resolving more of these 4 checkable
claims *without* sacrificing correctness.

## Iteration 1 — give the agent real execution tools, no verification contract

**What & why:** `agent/run_agent.py --variant no_contract`. Same tool set as the final agent
(clone/list/read, `run_command` in a Docker sandbox, `dependency_audit`,
`run_linter_or_complexity`, `scan_secrets`, `submit_report`) but the system prompt only says
"use these tools, then submit a report" — no requirement to cite which tool call backs which
claim.

**Evidence:** abstain rate drops 75% → 53% and Spearman jumps to 0.97 — tool access alone
resolves a lot of real uncertainty. But Layer 1 evidence traceability is only **11%**: when this
variant does cite a `tool_output` red flag, it's verifiable only 1 time in 9. It has capability
without discipline — it's usually right, but its citations can't be trusted without checking.

**Decision:** kept the tool set, moved on to add the discipline the numbers show is missing.

## Iteration 2 — mandatory evidence + `tool_call_id`, no self-check

**What & why:** `agent/run_agent.py --variant no_selfcheck`. Same tools, full verification
contract in the system prompt: every `red_flag` must carry `evidence` and a `tool_call_id`;
unverified claims go to `clarifying_questions`; a failed/timed-out tool call must be reported as
itself. No post-hoc check that the model actually honored the contract.

**Evidence:** Layer 1 traceability jumps 11% → **53%**, abstain rate drops further to 25% and
groundedness rises to 74% — the best groundedness of any system. But 53% still means nearly
half of this variant's tool-backed claims don't check out. Digging into *why* (see next section)
was the most useful thing this project found.

**Decision:** kept the contract, but its imperfect compliance meant it needed a backstop, not
just a better prompt.

## Iteration 3 (the real one) — self-check pass, and the bug it found

**What was tried first:** `agent/verify.py::self_check_and_repair` re-checks every
`tool_output`-sourced red flag after `submit_report`, demoting anything whose cited
`tool_call_id`/`evidence` doesn't match the trajectory. First run: **every single tool_output red
flag across all 8 repos got demoted** — Layer 1 went to "N/A" (zero flags left standing), not
100%.

**What that revealed:** the model wasn't fabricating evidence — the evidence text itself was
usually genuine. It was fabricating the *label*: instead of copying the real opaque `call_id`
(e.g. `call_9f2ac...`) it received in its own tool results, it invented a readable pseudo-id like
`"functions.run_command:pip_install_e"`. The strict contract in Iteration 2 was checking for
exact `tool_call_id` matches, so genuine, verifiable findings were being thrown away purely
because of how they were labeled — a false negative in the checker, not a false claim by the
model.

**The actual fix:** both `agent/verify.py` and `eval/groundedness_checker.py` now fall back to
searching *every* tool result in the trajectory for the cited evidence text when the exact
`tool_call_id` doesn't match, and repair the citation to the real id if found — rather than
discarding a real finding over a mislabeled reference. Only evidence that appears nowhere in the
trajectory gets demoted.

**Evidence after the fix:** Layer 1 evidence traceability: no_contract 11% → no_selfcheck 53% →
**full 100%**, and Spearman rises to the best of any system (0.99). Groundedness (72%) and
abstain rate (31%) land close to Iteration 2's, which makes sense — self-check only touches
`red_flags`, not the `build_status`/`test_status`/`vulnerability_summary` fields Layer 2 actually
scores, so it wasn't going to move that number much regardless. The two verification layers
measure genuinely different things, and this project's biggest single fix (self-check) shows up
almost entirely in the one metric built to detect exactly that class of problem.

**Decision:** kept as the shipped configuration (`--variant full`, the default).

## What was tried and removed

Nothing was deleted outright, but the first version of the self-check pass (exact-match-only,
described above) was effectively "tried and immediately replaced" within the same iteration once
its 0%-survival-rate result made the underlying bug visible. It's worth keeping in this
changelog rather than editing away: the failure of the naive fix is what pointed at the real
problem. All three agent variants (`no_contract`/`no_selfcheck`/`full`) stay in the codebase as
live ablations rather than being thrown away once "full" was chosen — the whole comparison above
would otherwise be an assertion instead of something a judge can rerun and check.

## Main failure mode

**Models cite tool results by an invented, readable label instead of the real opaque
identifier they were actually given — even under an explicit contract that requires exact
citation.** This is not a "the model is lying" failure; the underlying evidence was usually
real. It's a "the model doesn't treat an opaque system identifier as sacred text to transcribe
exactly" failure, and it silently breaks any verification scheme that trusts the model's
self-reported citation instead of independently searching for the evidence. The fix that
actually worked wasn't a better prompt — it was giving up on requiring perfect self-citation and
having the harness re-derive the correct reference deterministically.

## Hot take

**Don't verify a model's citations by checking if it followed your citation format — verify by
independently searching for whether the underlying claim is true.** This project's cleanest
number (Layer 1 evidence traceability, 11% → 53% → 100%) looks like a story about prompting
discipline, but the real fix in Iteration 3 was the opposite of a better prompt: it was
deciding not to trust the model's transcription of its own tool-call ids at all, and instead
having deterministic code find the evidence wherever it actually lived in the trajectory. The
practical lesson for building reliable agents: when you ask a model to cite its sources, budget
for the citation mechanism itself to be unreliable in a way that has nothing to do with whether
the underlying claim is true — and design your verification to survive that, not just to reward
compliance with the citation format.
