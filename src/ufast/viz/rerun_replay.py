"""
viz/rerun_replay.py — 정적 레이아웃 + OHT 궤적을 Rerun 에서 재생.

단독 실행:
    python3 -m ufast.viz.rerun_replay <trajectory.json>
    python3 -m ufast.viz.rerun_replay --rail dataset/SMAT2022.rail \\
            --traj results/HVLM_1d_100oht_a0.05_s0_trajectories.json

라이브러리:
    from ufast.viz.rerun_replay import show_run
    show_run(rail_file, trajectory_path)

설계:
  - 정적 레이아웃은 rerun_layout.log_layout_entities 재사용.
  - 시각 t 별로 각 OHT 위치를 trajectory.interpolate_trip_position 으로 계산.
  - Rerun 의 'sim_time' 타임라인에 매 프레임 로그 → 뷰어에서 스크럽·재생.
  - 상태별 색상: IDLE 회색 / EMPTY 파랑 / LOADED 주황 / AT_DEST 녹색.

성능 고려:
  - 프레임 수 = 총 sim 시간 / time_step_s.
  - 1일 (86400s) / step 60s = 1,440 프레임 — 부드러움.
  - 30일 / step 60s = 43,200 프레임 — replay 로깅 ~수분, 메모리 OK.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

try:
    import rerun as rr
except ImportError:
    rr = None

from ufast.paths import DATASET_DIR
from ufast.viz.rerun_layout import log_layout_entities, load_layout_geometry, _ensure_rerun
from ufast.viz.trajectory import (TrajectoryLog, Trip, interpolate_trip_position,
                            STATE_IDLE, STATE_EMPTY, STATE_LOADED, STATE_AT_DEST)


# ── OHT 상태 색상 ───────────────────────────────────────────
_STATE_COLORS: Dict[str, List[int]] = {
    STATE_IDLE:    [160, 160, 160, 200],   # 회색
    STATE_EMPTY:   [ 80, 160, 240, 255],   # 파랑 (픽업하러)
    STATE_LOADED:  [255, 140,  40, 255],   # 주황 (적재)
    STATE_AT_DEST: [ 80, 200, 100, 255],   # 녹색 (방금 도착)
}
_OHT_RADIUS_MM_DEFAULT = 600.0

# ── Family 가동 헤일로 — 부하율(active/total) 기반 3단 gradient ──
# IDLE: 완전 투명 (정적 마커만 보임)
# PARTIAL (0 < load <= 0.5): 주황 — 일부 머신 가동
# HEAVY   (0.5 < load <= 1.0): 빨강 — 대다수 머신 가동
_FAMILY_IDLE_COLOR    = [0, 0, 0, 0]
_FAMILY_PARTIAL_COLOR = [255, 160,  60, 220]
_FAMILY_HEAVY_COLOR   = [220,  60,  60, 230]
_FAMILY_BUSY_RADIUS_MM = 1800.0


def show_run(
    rail_file: str,
    trajectory_path: str,
    *,
    time_step_s: float = 60.0,
    oht_radius_mm: float = _OHT_RADIUS_MM_DEFAULT,
    app_id: str = "UFAST_replay",
    spawn: bool = True,
    save_rrd: Optional[str] = None,
) -> None:
    """
    Rerun 에 fab 레이아웃 + OHT 궤적 시간선 재생을 로그한다.

    Args:
        rail_file: .rail 파일 경로
        trajectory_path: TrajectoryLog JSON 경로
        time_step_s: 프레임당 sim 초 간격 (기본 60 = 1 sim-분/프레임)
        oht_radius_mm: OHT 점의 반경
        spawn: Rerun 뷰어 자동 실행
        save_rrd: 지정 시 .rrd 파일 저장 (viewer 안 띄움)
    """
    _ensure_rerun()
    if save_rrd:
        rr.init(app_id, spawn=False)
    else:
        rr.init(app_id, spawn=spawn)

    # log_time(wall clock) 타임라인은 비활성화 — sim_time 만 사용해
    # 뷰어가 자동으로 sim 시간선을 잡도록 함.
    try:
        rr.disable_timeline("log_time")
    except Exception:
        pass

    # ── 정적 레이아웃 로그 (재사용) ──
    log_layout_entities(rail_file)
    nodes_xy, _line, _curve, fam_positions, fam_labels = load_layout_geometry(rail_file)
    fam_xy: Dict[str, Tuple[float, float]] = dict(zip(fam_labels, fam_positions))

    # ── 궤적 로드 ──
    traj = TrajectoryLog.load(trajectory_path)
    print(f"[viz.rerun_replay] 궤적 로드 — trip {len(traj.trips):,} / "
          f"OHT {len(traj.oht_initial_positions)} / "
          f"machine activities {len(traj.machine_activities):,}")

    if not traj.trips:
        print("[viz.rerun_replay] trip 이 없어 재생 종료.")
        return

    # ── 머신 활동 event sweep (family 단위 busy 카운트) ──
    mach_events: List[Tuple[float, int, str]] = []
    for a in traj.machine_activities:
        mach_events.append((float(a['start']), +1, a['family']))
        mach_events.append((float(a['end']), -1, a['family']))
    mach_events.sort(key=lambda e: e[0])
    mach_active: Dict[str, int] = {}
    mach_event_idx = 0

    # ── OHT 별 trip 정렬 + 포인터 ──
    trips_by_oht: Dict[str, List[Trip]] = defaultdict(list)
    for trip in traj.trips:
        trips_by_oht[trip.oht_id].append(trip)
    for oht_id in trips_by_oht:
        trips_by_oht[oht_id].sort(key=lambda t: t.assignment_time)

    # ── OHT 초기 위치 / 상태 ──
    all_ohts = sorted(traj.oht_initial_positions.keys())
    pos_by_oht: Dict[str, Tuple[float, float]] = {
        o: nodes_xy.get(traj.oht_initial_positions[o], (0.0, 0.0))
        for o in all_ohts
    }
    state_by_oht: Dict[str, str] = {o: STATE_IDLE for o in all_ohts}
    trip_idx: Dict[str, int] = {o: 0 for o in all_ohts}

    # ── 시간 범위 — 0 부터 시작해 OHT 초기 상태도 보이게 함 ──
    t_min = 0.0
    t_max = max(t.delivery_time for t in traj.trips)
    n_frames = int((t_max - t_min) / time_step_s) + 1
    print(f"[viz.rerun_replay] sim {t_min:.0f}s → {t_max:.0f}s, "
          f"step {time_step_s:.0f}s, 프레임 {n_frames:,}")

    # ── 프레임 스트리밍 ──
    started = time.time()
    t = t_min
    frame = 0
    progress_every = max(1, n_frames // 20)

    while t <= t_max + time_step_s:
        # 각 OHT 상태 업데이트
        for oht_id in all_ohts:
            trips = trips_by_oht.get(oht_id, [])
            i = trip_idx[oht_id]

            # 이미 끝난 trip 들 처리: 위치 = 최종 노드, 상태 = IDLE
            while i < len(trips) and trips[i].delivery_time <= t:
                last = trips[i].loaded_path[-1] if trips[i].loaded_path else None
                if last and last in nodes_xy:
                    pos_by_oht[oht_id] = nodes_xy[last]
                state_by_oht[oht_id] = STATE_IDLE
                i += 1
            trip_idx[oht_id] = i

            # 현재 trip 진행 중?
            if i < len(trips):
                trip = trips[i]
                if trip.assignment_time <= t < trip.delivery_time:
                    interp = interpolate_trip_position(trip, t, nodes_xy)
                    if interp is not None:
                        pos_by_oht[oht_id] = (interp[0], interp[1])
                        state_by_oht[oht_id] = interp[2]
                # else: 다음 trip 시작 전 — IDLE 유지

        # 머신 활동 sweep — t 시점의 family 별 active count
        while mach_event_idx < len(mach_events) and mach_events[mach_event_idx][0] <= t:
            _ts, delta, fam = mach_events[mach_event_idx]
            mach_active[fam] = mach_active.get(fam, 0) + delta
            mach_event_idx += 1

        # Rerun 로그 (rerun-sdk 0.32+ 통합 API)
        rr.set_time("sim_time", duration=t)
        # 1) family 가동 헤일로 — 부하율(active/total) 기반 3단 색상
        fam_colors = []
        for f in fam_labels:
            active = mach_active.get(f, 0)
            size = traj.family_sizes.get(f, 0) or 1
            load = active / size if size > 0 else 0.0
            if active <= 0:
                fam_colors.append(_FAMILY_IDLE_COLOR)
            elif load <= 0.5:
                fam_colors.append(_FAMILY_PARTIAL_COLOR)
            else:
                fam_colors.append(_FAMILY_HEAVY_COLOR)
        rr.log("layout/family_busy",
               rr.Points2D(fam_positions, colors=fam_colors,
                           radii=_FAMILY_BUSY_RADIUS_MM))
        # 2) OHT 점
        positions = [pos_by_oht[o] for o in all_ohts]
        colors = [_STATE_COLORS[state_by_oht[o]] for o in all_ohts]
        rr.log("ohts", rr.Points2D(positions, colors=colors, radii=oht_radius_mm))

        frame += 1
        if frame % progress_every == 0:
            pct = 100 * frame / n_frames
            print(f"  ... frame {frame:,}/{n_frames:,} ({pct:.0f}%)")
        t += time_step_s

    elapsed = time.time() - started
    print(f"[viz.rerun_replay] 재생 로그 완료 — {frame:,} frames, {elapsed:.1f}s")

    # ── F12: KPI 시계열 scalar plot ──
    # snapshot: (sim_time, busy_count, inflight_sum, pending, delivered, max_inflight)
    snapshots = traj.kpi_snapshots
    if snapshots:
        oht_total = traj.oht_total or 1
        for snap in snapshots:
            t_kpi, busy, infl_sum, pending, delivered, max_infl = snap
            rr.set_time("sim_time", duration=float(t_kpi))
            rr.log("kpi/oht_busy_ratio",
                   rr.Scalars(busy / oht_total if oht_total else 0.0))
            rr.log("kpi/section_inflight_sum", rr.Scalars(float(infl_sum)))
            rr.log("kpi/pending_queue", rr.Scalars(float(pending)))
            rr.log("kpi/cumulative_delivered", rr.Scalars(float(delivered)))
            rr.log("kpi/max_section_inflight", rr.Scalars(float(max_infl)))
        print(f"[viz.rerun_replay] KPI 시계열 — {len(snapshots):,} snapshot 로그됨")

    if save_rrd:
        rr.save(save_rrd)
        print(f"[viz.rerun_replay] .rrd 저장: {save_rrd}")
    elif spawn:
        print("[viz.rerun_replay] 💡 Rerun 뷰어 사용법:")
        print("[viz.rerun_replay]   • 하단 타임라인 패널에서 'sim_time' 선택")
        print("[viz.rerun_replay]   • ▶️ Play 로 OHT 움직임 재생 / 슬라이더로 스크럽")
        print("[viz.rerun_replay]   • 2D 뷰의 'ohts' 점이 색깔별로 움직임 (회색 IDLE / 파랑 EMPTY / 주황 LOADED)")
        print("[viz.rerun_replay]   • 좌측 'kpi/' 트리에서 OHT busy ratio, section_inflight, pending queue 등 시계열 차트")


def _main():
    p = argparse.ArgumentParser(description="Rerun 으로 ufast 결과 재생")
    p.add_argument('traj', nargs='?',
                   help='trajectory JSON 경로 (results/<run_id>/*_trajectories.json)')
    p.add_argument('--rail',
                   default=os.path.join(DATASET_DIR, 'SMAT2022.rail'))
    p.add_argument('--step', type=float, default=60.0,
                   help='프레임당 sim 초 간격 (기본 60)')
    p.add_argument('--oht-size', type=float, default=_OHT_RADIUS_MM_DEFAULT,
                   help='OHT 점 반경 (mm)')
    p.add_argument('--no-spawn', action='store_true')
    p.add_argument('--save', metavar='PATH', help='.rrd 파일로 저장')
    a = p.parse_args()

    if not a.traj:
        # results/ 의 가장 최근 trajectory 자동
        import glob
        files = glob.glob(
            os.path.join(os.path.dirname(_BASE), 'results', '**',
                         '*_trajectories.json'),
            recursive=True)
        if not files:
            print("trajectory 파일 없음. ufast/cosim/run.py --viz 로 먼저 생성하세요.")
            sys.exit(1)
        a.traj = max(files, key=os.path.getmtime)
        print(f"(최신 궤적 자동: {os.path.basename(a.traj)})")

    show_run(a.rail, a.traj, time_step_s=a.step, oht_radius_mm=a.oht_size,
             spawn=not a.no_spawn, save_rrd=a.save)


if __name__ == '__main__':
    _main()
