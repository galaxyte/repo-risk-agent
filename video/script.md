# Solution Video — Shot List (target: under 5 minutes)

**Format:** screen recording + voice narration (no webcam needed). One take is fine — QuickTime
(macOS: File → New Screen Recording, select your mic first) or OBS both work.

**Before recording:** start the web UI (`uvicorn webapp.app:app --reload --port 8000`), open
`http://localhost:8000` in one window, and have `README.md` / `CHANGELOG.md` / `EDGE_CASES.md`
ready in a second window/tab to switch to.

---

## 0:00–0:40 — Problem

- "I'm a freelance software engineer. Before I quote a price on a new client project, I have to
  figure out if their codebase is healthy or a trap. Today that means either hours of manual
  digging, or the shortcut everyone actually takes: paste some files into an LLM and ask if it
  looks okay — which sounds thorough but hasn't actually verified anything."
- Briefly show `README.md`'s "Who this is for" / "The bottleneck" section on screen while saying this.

## 0:40–1:00 — Meet the demo target

- Switch to `EDGE_CASES.md` in `invoice-lite-legacy` (a separate repo you built specifically to
  showcase this): "So I built a small fake invoicing app with 15 real problems planted in it on
  purpose — hardcoded secrets, a secret that only exists in git history, vulnerable dependencies,
  a Python 2 file that won't even parse, a 54KB god-file, a misleading README, a dead CI config.
  This is the answer key I'm testing both systems against."

## 1:00–3:00 — One realistic execution, start to finish (the web UI)

- Open `http://localhost:8000`. Paste `https://github.com/galaxyte/invoice-lite-legacy.git`,
  click **Analyze**.
- While the roadmap runs (Cloning → Baseline → Agent, dots going grey → pulsing teal → green),
  narrate: "Baseline runs first — one LLM call, no tools. Then the agent actually clones this,
  installs it, runs the real test suite, audits dependencies, scans for secrets, all inside a
  disposable Docker container."
- When Baseline finishes: point at its card — `build_status`/`test_status` showing `unknown`,
  `vulnerability_summary: null`. "It correctly refuses to guess — but that means it's told the
  freelancer nothing useful about the two things that actually matter."
- When Agent finishes: point at its card — `build_status: passed`, `test_status: failed` with a
  real pass rate, `vulnerability_summary` with a real count. Click into 2-3 red flags and show the
  **`📍 file:line` badge** on each one — the private key at `certs/dev.key:1`, the hardcoded
  password at `config.py:3`. "Every one of these points at an exact line, not a vibe."

## 3:00–3:40 — Final comparison (the numbers)

- Switch to `README.md`'s results table or `results/summary.md`. Walk it in one breath: "Across 8
  real public repos, baseline correlates 0.71 with an expert risk ranking; the full agent hits
  0.99. The number I actually care about is this one: Layer 1 evidence traceability — can you
  trust what it cites — goes from 11% with tools-but-no-discipline, to 53% with a verification
  contract, to 100% once there's a deterministic self-check backstop."

## 3:40–4:15 — The changelog, and the bug that made it real

- Open `CHANGELOG.md`, scroll to Iteration 3. "The honest story: the first version of my
  self-check demoted *every single tool-backed red flag across all 8 repos* on its first run. Not
  because the model was lying — because it cited a made-up label like
  `functions.run_command:pip_install` instead of the real id it was actually given. The fix
  wasn't a better prompt. It was refusing to trust the model's transcription at all, and having
  the code search the real trajectory for the evidence instead."

## 4:15–4:45 — What was tried and kept as a live comparison

- "All three agent variants — no verification contract, contract without a self-check, and the
  full version — are still in the repo, not thrown away, so this comparison is something a judge
  can rerun, not just something I'm asserting." (Optionally flash `agent/run_agent.py`'s
  `VARIANTS` dict for two seconds.)

## 4:45–5:00 — Hot take, close

- "Don't verify a model's citations by checking if it followed your format — verify by
  independently searching for whether the claim is true. That's the one sentence this whole
  project is built around." Cut.

---

## Recording notes

- Pre-warm the web UI once before recording (first request after starting uvicorn is a little
  slower while Python imports settle) so the on-camera run feels snappy.
- If the live agent run (1:00–3:00) is running long/flaky on the day, it's fine to have a second
  browser tab with a completed run already loaded and switch to narrating over that instead —
  just say so, don't fake it as live.
- Keep zoom/font size large in both the browser and any code windows — assume judges watch at
  less than full screen.
