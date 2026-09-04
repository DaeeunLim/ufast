#!/usr/bin/env bash
# E5 — queue(blocking) 모델 fleet sweep (HVLM, 논문 Figure 3 의 queue 판)
#
# 목적 (2026-08-09):
#   1. SoftwareX 재포지셔닝 — queue 모델을 대표 모델로 내세우려면 검증·fleet
#      sweep 그림도 queue 모드 결과여야 한다.
#   2. 포화 구간(5~20대) 데드락 강제해소(deadlock_forced) 건전성 검증 —
#      기본 모델 전환의 전제조건.
#
#   HVLM: OHT {5,10,15,20,25,50,100,200,300} × seed{0,1,2} = 27런
#   E1/E3 와 동일한 180d 설계 (static 120d + settling 10d + 측정 50d).
#
# 초포화 런은 이송 큐·차단 대기열이 커질 수 있어 병렬도 기본 4.
# 사용: bash scripts/exp5_queue_fleet_sweep.sh [병렬도=4]
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
# 데드락 강제해소 요약 — 건전성 한눈에
echo "[exp5] deadlock_forced by run:"
grep -h '"deadlock_forced"' "$ROOT"/results/*/HVLM_180d_*queue*.json 2>/dev/null | sort | uniq -c | head
