#!/usr/bin/env bash
# Run one agent across the guard matrix and build the comparison page.
#   scripts/run_matrix.sh scripted          # ~45 min, no API key
#   scripts/run_matrix.sh claude            # needs ANTHROPIC_API_KEY
# Configs run 3 at a time on separate ports.
set -euo pipefail
cd "$(dirname "$0")/.."
AGENT=${1:-scripted}; RUNS=${RUNS:-5}; EXTRA=${EXTRA:-}
run() { python3 runner/run.py --agent "$AGENT" --runs "$RUNS" --out "runs/$AGENT-$1" --port "$2" ${3:-} $EXTRA > "runs/$AGENT-$1.log" 2>&1; }
mkdir -p runs
run none 5101 &  run all 5102 "--guards all" &  run settle 5103 "--guards settle" &  wait
run reresolve 5104 "--guards reresolve" &  run overlay 5105 "--guards overlay" &  run follow_tab 5106 "--guards follow_tab" &  wait
run nochaos 5107 "--no-chaos" & wait
python3 report/compare.py runs/$AGENT-none runs/$AGENT-settle runs/$AGENT-reresolve runs/$AGENT-overlay \
  runs/$AGENT-follow_tab runs/$AGENT-all runs/$AGENT-nochaos --out docs/$AGENT
