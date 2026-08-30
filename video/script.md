# Solution Video — Shot List (target: under 5 minutes)

## 0:00–0:45 — Problem & baseline
- "I'm a freelance software engineer. Before I quote a price on a new client project, I have to figure out if their codebase is healthy or a trap — and today that means either hours of manual digging, or the shortcut everyone actually takes: paste some files into an LLM and ask if it looks okay."
- Show the baseline shortcut live on `django-dialogflow` (a real repo with a `.travis.yml` claiming CI + coverage, but pinned to Django 1.11 / Python 2.7-3.5): open `results/raw/django-dialogflow/baseline_report.json`, point at `build_status`/`test_status` — the baseline (correctly, since it was told not to guess) reports these as `"unknown"`. It looks safe but tells the freelancer nothing.

## 0:45–3:00 — One realistic execution, start to finish
- Run the agent on the same repo: `python -m agent.run_agent --repo-path workspace/django-dialogflow --repo-name django-dialogflow --out-dir /tmp/demo --variant full`.
- While it runs, narrate the trajectory live from `agent_trajectory.jsonl`: it installs the pinned deps, gets `Django-1.11.5 ... requests-2.7.0`, then tries to run tests and hits `ImportError: cannot import name 'Iterator' from 'collections'` — a real Python-3.11-vs-Django-1.11 incompatibility this repo's own (dead) Travis config never would have caught on a modern runner.
- Show the final `agent_report.json`: a `red_flag` with `source: "tool_output"`, `evidence: "ImportError: cannot import name 'Iterator'..."`, and `tool_call_id: "call_9zwEBzy..."` — then actually grep that exact call_id in `agent_trajectory.jsonl` on screen to prove the citation is real, not asserted.

## 3:00–4:00 — Final comparison
- Open `results/summary.md` / the table in `README.md`. Walk it: Spearman rank correlation (0.71 baseline → 0.99 full agent), groundedness, abstain rate, and the standout number — **Layer 1 evidence traceability: 11% → 53% → 100%** across the three agent variants.
- Call out that "100%" isn't the agent being perfect — it's the self-check pass demoting anything it can't verify, so what's left is guaranteed real. Contrast with baseline's 75% abstain rate: calibrated but largely silent on the questions that matter.

## 4:00–4:20 — Changelog walkthrough
- Open `CHANGELOG.md`. One sentence per stage: baseline (floor, mostly abstains) → tools/no contract (capability, 11% traceable) → contract/no self-check (discipline, 53% traceable) → full (self-check backstop, 100% traceable, best Spearman).
- Name the change that contributed most: **not** the prompt — it was Iteration 3's fix to the *checker itself* (fuzzy-match evidence against the whole trajectory instead of trusting the model's self-reported `tool_call_id`).

## 4:20–5:00 — The experiment that failed first, then the hot take
- Tell the real story: the first version of the self-check pass demoted **100% of tool-backed red flags across all 8 repos** on the very first run — not because the model was lying, but because it cited a made-up label like `"functions.run_command:pip_install"` instead of the real opaque `call_id` it had actually received.
- Close on the hot take: don't verify a model's citations by checking if it followed your citation *format* — verify by independently searching for whether the underlying claim is *true*. The fix that worked wasn't a better prompt; it was refusing to trust the model's transcription and having the harness re-derive the real reference deterministically.

## Recording notes
- Record a live terminal + editor session (no slides needed) — the whole point is showing real tool calls and real file contents, not narrating over static text.
- Have `results/summary.md` and the `django-dialogflow` `agent_full` trajectory pre-generated before recording so the walkthrough doesn't wait on live API latency; it's fine to *show* one short live agent run (0:45–3:00) for authenticity as long as the rest is pre-computed.
