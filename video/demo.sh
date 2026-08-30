#!/usr/bin/env bash
# CLI-only alternative/backup to the web-UI walkthrough in video/script.md.
# The primary recording plan uses the web UI (uvicorn webapp.app:app) since it
# shows the live Cloning -> Baseline -> Agent roadmap -- use THIS script only
# if you'd rather record a pure terminal walkthrough instead, or to
# pre-generate a completed invoice-lite-legacy run as a fallback in case the
# live web UI run is slow/flaky on recording day.
#
# Press Enter to advance -- nothing runs until you're ready, so there's no
# dead air on camera.
#
# Usage: bash video/demo.sh          (uses pre-generated results -- safe, no API calls)
#        bash video/demo.sh --live   (also runs the agent live on invoice-lite-legacy)
set -euo pipefail
cd "$(dirname "$0")/.."

LIVE=false
[[ "${1:-}" == "--live" ]] && LIVE=true
REPO=invoice-lite-legacy

step() {
    echo
    echo "────────────────────────────────────────────────────────────"
    echo "  $1"
    echo "────────────────────────────────────────────────────────────"
    read -rp "  [Enter to continue] "
    clear
}

clear
step "SECTION 1 (0:00-0:40) -- Problem & baseline shortcut"

echo "\$ cat README.md | head -20"
echo
head -20 README.md
step "Narrate the problem. Now show the baseline's honest-but-unhelpful abstention:"

echo "\$ jq '{build_status, test_status, go_no_go, risk_score}' results/raw/$REPO/baseline_report.json"
echo
jq '{build_status, test_status, go_no_go, risk_score}' "results/raw/$REPO/baseline_report.json"
step "Point out: build_status/test_status/vulnerability_summary are unknown/null -- calibrated, but tells the freelancer nothing."

step "SECTION 2 (0:40-1:00) -- Meet the demo target"

echo "\$ cat ../invoice-lite-legacy/EDGE_CASES.md | head -25"
echo
head -25 "../invoice-lite-legacy/EDGE_CASES.md" 2>/dev/null || echo "(clone https://github.com/galaxyte/invoice-lite-legacy.git as a sibling dir first)"
step "Narrate: 15 planted problems, this file is the answer key."

if $LIVE; then
    step "SECTION 3 (1:00-3:00) -- LIVE agent run (1-3 min, narrate while it runs)"
    echo "\$ python -m agent.run_agent --repo-path workspace/$REPO --repo-name $REPO --out-dir /tmp/demo_live --variant full"
    echo
    python -m agent.run_agent --repo-path "workspace/$REPO" --repo-name "$REPO" --out-dir /tmp/demo_live --variant full | tail -30
    TRAJ=/tmp/demo_live/agent_trajectory.jsonl
    REPORT=/tmp/demo_live/agent_report.json
else
    step "SECTION 3 (1:00-3:00) -- Pre-generated agent trajectory (safe, no live API wait)"
    TRAJ="results/raw/$REPO/agent_full/agent_trajectory.jsonl"
    REPORT="results/raw/$REPO/agent_full/agent_report.json"
fi

echo "\$ jq '.red_flags[] | {title, severity, file_ref}' \"$REPORT\""
echo
jq '.red_flags[] | {title, severity, file_ref}' "$REPORT"
step "Point at the file_ref badges -- every flag traces to an exact file:line."

echo "\$ jq '.red_flags[] | select(.source==\"tool_output\")' \"$REPORT\""
echo
jq '.red_flags[] | select(.source=="tool_output")' "$REPORT"
step "Pick one tool_call_id from above, then prove it's real:"

echo "\$ grep -o 'call_[A-Za-z0-9]*' \"$TRAJ\" | sort -u | head -5"
echo
grep -o 'call_[A-Za-z0-9]*' "$TRAJ" | sort -u | head -5
step "Show that the cited call_id really appears in the trajectory (not asserted)."

step "SECTION 4 (3:00-3:40) -- Final comparison table"

echo "\$ cat results/summary.md"
echo
cat results/summary.md
step "Walk the table: Spearman 0.71 -> 0.99, Layer-1 traceability 11% -> 53% -> 100%."

step "SECTION 5 (3:40-4:15) -- Changelog: the bug that made it real"

echo "\$ sed -n '/## Iteration 3/,/## What was tried/p' CHANGELOG.md"
echo
sed -n '/## Iteration 3/,/## What was tried/p' CHANGELOG.md
step "Tell the story: self-check demoted 100% of flags on the first run -- fabricated labels, not fabricated evidence."

step "SECTION 6 (4:15-4:45) -- Variants kept as a live comparison"

echo "\$ sed -n '/^VARIANTS = /,/^}/p' agent/run_agent.py"
echo
sed -n '/^VARIANTS = /,/^}/p' agent/run_agent.py
step "All three agent variants stay in the repo so this comparison is rerunnable, not asserted."

echo "\$ tail -12 CHANGELOG.md"
echo
tail -12 CHANGELOG.md
step "SECTION 7 (4:45-5:00) -- Close on the hot take. Done -- stop recording."

echo "Recording walkthrough complete."
