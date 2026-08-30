#!/usr/bin/env bash
# Video recording walkthrough. Run this WHILE screen-recording and narrate
# each section per video/script.md. Press Enter to advance to the next step
# -- nothing runs until you're ready, so there's no dead air on camera.
#
# Usage: bash video/demo.sh          (uses pre-generated results -- safe, no API calls)
#        bash video/demo.sh --live   (also runs the agent live on django-dialogflow)
set -euo pipefail
cd "$(dirname "$0")/.."

LIVE=false
[[ "${1:-}" == "--live" ]] && LIVE=true

step() {
    echo
    echo "────────────────────────────────────────────────────────────"
    echo "  $1"
    echo "────────────────────────────────────────────────────────────"
    read -rp "  [Enter to continue] "
    clear
}

clear
step "SECTION 1 (0:00-0:45) -- Problem & baseline shortcut"

echo "\$ cat README.md | head -20"
echo
head -20 README.md
step "Narrate the problem. Now show the baseline's honest-but-unhelpful abstention:"

echo "\$ jq '{build_status, test_status, go_no_go, risk_score}' results/raw/django-dialogflow/baseline_report.json"
echo
jq '{build_status, test_status, go_no_go, risk_score}' results/raw/django-dialogflow/baseline_report.json
step "Point out: build_status/test_status are \"unknown\" -- calibrated, but tells the freelancer nothing."

if $LIVE; then
    step "SECTION 2 (0:45-3:00) -- LIVE agent run (this will take 1-3 min, narrate while it runs)"
    echo "\$ python -m agent.run_agent --repo-path workspace/django-dialogflow --repo-name django-dialogflow --out-dir /tmp/demo_live --variant full"
    echo
    python -m agent.run_agent --repo-path workspace/django-dialogflow --repo-name django-dialogflow --out-dir /tmp/demo_live --variant full | tail -30
    TRAJ=/tmp/demo_live/agent_trajectory.jsonl
    REPORT=/tmp/demo_live/agent_report.json
else
    step "SECTION 2 (0:45-3:00) -- Pre-generated agent trajectory (safe, no live API wait)"
    TRAJ=results/raw/django-dialogflow/agent_full/agent_trajectory.jsonl
    REPORT=results/raw/django-dialogflow/agent_full/agent_report.json
fi

echo "\$ cat trajectories/django-dialogflow.md | head -40"
echo
head -40 trajectories/django-dialogflow.md
step "Narrate: real install, real ImportError from Django 1.11 vs Python 3.11. Now the report:"

echo "\$ jq '.red_flags[] | select(.source==\"tool_output\")' \"$REPORT\""
echo
jq '.red_flags[] | select(.source=="tool_output")' "$REPORT"
step "Pick one tool_call_id from above, then prove it's real:"

echo "\$ grep -o 'call_[A-Za-z0-9]*' \"$TRAJ\" | sort -u | head -5"
echo
grep -o 'call_[A-Za-z0-9]*' "$TRAJ" | sort -u | head -5
step "Show that the cited call_id really appears in the trajectory (not asserted)."

step "SECTION 3 (3:00-4:00) -- Final comparison table"

echo "\$ cat results/summary.md"
echo
cat results/summary.md
step "Walk the table: Spearman 0.71 -> 0.99, Layer-1 traceability 11% -> 53% -> 100%."

step "SECTION 4 (4:00-4:20) -- Changelog walkthrough"

echo "\$ head -60 CHANGELOG.md"
echo
head -60 CHANGELOG.md
step "One sentence per stage. Name Iteration 3's checker fix as the biggest contributor."

step "SECTION 5 (4:20-5:00) -- The failed-first experiment + hot take"

echo "\$ sed -n '/## Iteration 3/,/## What was tried/p' CHANGELOG.md"
echo
sed -n '/## Iteration 3/,/## What was tried/p' CHANGELOG.md
step "Tell the story: self-check demoted 100% of flags on the first run -- fabricated labels, not fabricated evidence."

echo "\$ tail -12 CHANGELOG.md"
echo
tail -12 CHANGELOG.md
step "Close on the hot take. Done -- stop recording."

echo "Recording walkthrough complete."
