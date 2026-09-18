#!/usr/bin/env bash
# E5 — fleet sweep with the queue (blocking) model (HVLM, queue-model version of paper Figure 3)
#
# Purpose:
#   1. Fleet-sweep figures with the queue (blocking) model, the representative model.
#   2. Sanity check of forced deadlock resolution (deadlock_forced) in the saturated
#      range (5-20 vehicles).
#
#   HVLM: OHT {5,10,15,20,25,50,100,200,300} × seed{0,1,2} = 27 runs
#   Same 180d design as E1/E3 (static 120d + settling 10d + measurement 50d).
#
# Heavily saturated runs can grow large transport/blocking queues, so parallelism defaults to 4.
# Usage: bash scripts/exp5_queue_fleet_sweep.sh [parallelism=4]
set -uo pipefail

JOBS="${1:-4}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
LOGDIR="$ROOT/logs/exp5_queue"
mkdir -p "$LOGDIR"
CMDS="$LOGDIR/commands.txt"
: > "$CMDS"

DAYS=180
WARMUP="--static-warmup-days 120 --amhs-settling-days 10"
SEEDS="0 1 2"
i=0
add() {
    local tag="$1"; shift
    printf 'cd %q && PYTHONPATH=src %q -u -m ufast.cosim.run %s > %q 2>&1\n' \
        "$ROOT" "$PY" "$*" "$LOGDIR/$(printf '%03d' "$i")_$tag.log" >> "$CMDS"
    i=$((i + 1))
}

for s in $SEEDS; do
    base="dataset/HVLM dataset/SMAT2022.rail --days $DAYS $WARMUP --seed $s --congestion queue"
    for oht in 5 10 15 20 25 50 100 200 300; do
        add "HVLM_oht${oht}_queue_s${s}" "$base --oht $oht"
    done
done

total=$(wc -l < "$CMDS")
echo "[exp5] $total runs, parallel=$JOBS, days=$DAYS, congestion=queue"
start=$(date +%s)
xargs -d '\n' -P "$JOBS" -I{} sh -c '{}' < "$CMDS"
rc=$?
echo "[exp5] finished rc=$rc after $((($(date +%s) - start) / 60)) min"
fails=$(grep -l Traceback "$LOGDIR"/*.log 2>/dev/null | wc -l)
echo "[exp5] logs with Traceback: $fails"
# Summary of forced deadlock resolutions — sanity at a glance
echo "[exp5] deadlock_forced by run:"
grep -h '"deadlock_forced"' "$ROOT"/results/*/HVLM_180d_*queue*.json 2>/dev/null | sort | uniq -c | head
