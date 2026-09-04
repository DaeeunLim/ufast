"""
viz/rerun_replay.py — replay the static layout + OHT trajectories in Rerun.

Standalone:
    python3 -m ufast.viz.rerun_replay <trajectory.json>
    python3 -m ufast.viz.rerun_replay --rail dataset/SMAT2022.rail \\
            --traj results/HVLM_1d_100oht_a0.05_s0_trajectories.json

As a library:
    from ufast.viz.rerun_replay import show_run
    show_run(rail_file, trajectory_path)

Design:
  - The static layout reuses rerun_layout.log_layout_entities.
  - Each OHT position at time t is computed with trajectory.interpolate_trip_position.
  - Every frame is logged on Rerun's 'sim_time' timeline -> scrub/play in the viewer.
  - Colour per state: IDLE grey / EMPTY blue / LOADED orange / AT_DEST green.

Performance notes:
  - Frame count = total sim time / time_step_s.
  - 1 day (86400 s) / step 60 s = 1,440 frames — smooth.
  - 30 days / step 60 s = 43,200 frames — replay logging takes a few minutes, memory OK.
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


# ── OHT state colours ───────────────────────────────────────
_STATE_COLORS: Dict[str, List[int]] = {
    STATE_IDLE:    [160, 160, 160, 200],   # grey
    STATE_EMPTY:   [ 80, 160, 240, 255],   # blue (heading to pickup)
    STATE_LOADED:  [255, 140,  40, 255],   # orange (loaded)
    STATE_AT_DEST: [ 80, 200, 100, 255],   # green (just arrived)
}
_OHT_RADIUS_MM_DEFAULT = 600.0

# ── Family activity halo — 3-level gradient based on load ratio (active/total) ──
# IDLE: fully transparent (only the static marker is visible)
# PARTIAL (0 < load <= 0.5): orange — some machines busy
# HEAVY   (0.5 < load <= 1.0): red — most machines busy
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
    Log the fab layout + OHT trajectory timeline replay to Rerun.

    Args:
        rail_file: path to the .rail file
        trajectory_path: path to the TrajectoryLog JSON
        time_step_s: sim seconds per frame (default 60 = 1 sim-minute per frame)
        oht_radius_mm: radius of the OHT points
        spawn: launch the Rerun viewer automatically
        save_rrd: if given, save a .rrd file (no viewer is launched)
    """
    _ensure_rerun()
    if save_rrd:
        rr.init(app_id, spawn=False)
    else:
        rr.init(app_id, spawn=spawn)

    # Disable the log_time (wall-clock) timeline — use only sim_time so the
    # viewer automatically picks the sim timeline.
    try:
        rr.disable_timeline("log_time")
    except Exception:
        pass

    # ── Static layout log (reused) ──
    log_layout_entities(rail_file)
    nodes_xy, _line, _curve, fam_positions, fam_labels = load_layout_geometry(rail_file)
    fam_xy: Dict[str, Tuple[float, float]] = dict(zip(fam_labels, fam_positions))

    # ── Load trajectories ──
    traj = TrajectoryLog.load(trajectory_path)
    print(f"[viz.rerun_replay] Trajectories loaded — trips {len(traj.trips):,} / "
          f"OHT {len(traj.oht_initial_positions)} / "
          f"machine activities {len(traj.machine_activities):,}")

    if not traj.trips:
        print("[viz.rerun_replay] No trips; ending replay.")
        return

    # ── Machine activity event sweep (busy count per family) ──
    mach_events: List[Tuple[float, int, str]] = []
    for a in traj.machine_activities:
        mach_events.append((float(a['start']), +1, a['family']))
        mach_events.append((float(a['end']), -1, a['family']))
    mach_events.sort(key=lambda e: e[0])
    mach_active: Dict[str, int] = {}
    mach_event_idx = 0

    # ── Sort trips per OHT + pointers ──
    trips_by_oht: Dict[str, List[Trip]] = defaultdict(list)
    for trip in traj.trips:
        trips_by_oht[trip.oht_id].append(trip)
    for oht_id in trips_by_oht:
        trips_by_oht[oht_id].sort(key=lambda t: t.assignment_time)

    # ── OHT initial positions / states ──
    all_ohts = sorted(traj.oht_initial_positions.keys())
    pos_by_oht: Dict[str, Tuple[float, float]] = {
        o: nodes_xy.get(traj.oht_initial_positions[o], (0.0, 0.0))
        for o in all_ohts
    }
    state_by_oht: Dict[str, str] = {o: STATE_IDLE for o in all_ohts}
    trip_idx: Dict[str, int] = {o: 0 for o in all_ohts}

    # ── Time range — start at 0 so the OHT initial state is visible too ──
    t_min = 0.0
    t_max = max(t.delivery_time for t in traj.trips)
    n_frames = int((t_max - t_min) / time_step_s) + 1
    print(f"[viz.rerun_replay] sim {t_min:.0f}s → {t_max:.0f}s, "
          f"step {time_step_s:.0f}s, frames {n_frames:,}")

    # ── Frame streaming ──
    started = time.time()
    t = t_min
    frame = 0
    progress_every = max(1, n_frames // 20)

    while t <= t_max + time_step_s:
        # Update each OHT's state
        for oht_id in all_ohts:
            trips = trips_by_oht.get(oht_id, [])
            i = trip_idx[oht_id]

            # Handle trips already finished: position = final node, state = IDLE
            while i < len(trips) and trips[i].delivery_time <= t:
                last = trips[i].loaded_path[-1] if trips[i].loaded_path else None
                if last and last in nodes_xy:
                    pos_by_oht[oht_id] = nodes_xy[last]
                state_by_oht[oht_id] = STATE_IDLE
                i += 1
            trip_idx[oht_id] = i

            # Is the current trip in progress?
            if i < len(trips):
                trip = trips[i]
                if trip.assignment_time <= t < trip.delivery_time:
                    interp = interpolate_trip_position(trip, t, nodes_xy)
                    if interp is not None:
                        pos_by_oht[oht_id] = (interp[0], interp[1])
                        state_by_oht[oht_id] = interp[2]
                # else: before the next trip starts — stay IDLE

        # Machine activity sweep — active count per family at time t
        while mach_event_idx < len(mach_events) and mach_events[mach_event_idx][0] <= t:
            _ts, delta, fam = mach_events[mach_event_idx]
            mach_active[fam] = mach_active.get(fam, 0) + delta
            mach_event_idx += 1

        # Rerun log (rerun-sdk 0.32+ unified API)
        rr.set_time("sim_time", duration=t)
        # 1) family activity halo — 3-level colour based on load ratio (active/total)
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
        # 2) OHT points
        positions = [pos_by_oht[o] for o in all_ohts]
        colors = [_STATE_COLORS[state_by_oht[o]] for o in all_ohts]
        rr.log("ohts", rr.Points2D(positions, colors=colors, radii=oht_radius_mm))

        frame += 1
        if frame % progress_every == 0:
            pct = 100 * frame / n_frames
            print(f"  ... frame {frame:,}/{n_frames:,} ({pct:.0f}%)")
        t += time_step_s

    elapsed = time.time() - started
    print(f"[viz.rerun_replay] Replay logging complete — {frame:,} frames, {elapsed:.1f}s")

    # ── F12: KPI time-series scalar plot ──
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
        print(f"[viz.rerun_replay] KPI time series — {len(snapshots):,} snapshots logged")

    if save_rrd:
        rr.save(save_rrd)
        print(f"[viz.rerun_replay] .rrd saved: {save_rrd}")
    elif spawn:
        print("[viz.rerun_replay] 💡 How to use the Rerun viewer:")
        print("[viz.rerun_replay]   • Select 'sim_time' in the bottom timeline panel")
        print("[viz.rerun_replay]   • ▶️ Play to replay OHT movement / drag the slider to scrub")
        print("[viz.rerun_replay]   • The 'ohts' points in the 2D view move, coloured by state (grey IDLE / blue EMPTY / orange LOADED)")
        print("[viz.rerun_replay]   • The 'kpi/' tree on the left has time-series charts: OHT busy ratio, section_inflight, pending queue, etc.")


def _main():
    p = argparse.ArgumentParser(description="Replay ufast results in Rerun")
    p.add_argument('traj', nargs='?',
                   help='path to the trajectory JSON (results/<run_id>/*_trajectories.json)')
    p.add_argument('--rail',
                   default=os.path.join(DATASET_DIR, 'SMAT2022.rail'))
    p.add_argument('--step', type=float, default=60.0,
                   help='sim seconds per frame (default 60)')
    p.add_argument('--oht-size', type=float, default=_OHT_RADIUS_MM_DEFAULT,
                   help='OHT point radius (mm)')
    p.add_argument('--no-spawn', action='store_true')
    p.add_argument('--save', metavar='PATH', help='save as a .rrd file')
    a = p.parse_args()

    if not a.traj:
        # auto-select the most recent trajectory under results/
        import glob
        files = glob.glob(
            os.path.join(os.path.dirname(_BASE), 'results', '**',
                         '*_trajectories.json'),
            recursive=True)
        if not files:
            print("No trajectory file found. Generate one first with ufast/cosim/run.py --viz.")
            sys.exit(1)
        a.traj = max(files, key=os.path.getmtime)
        print(f"(auto-selected latest trajectory: {os.path.basename(a.traj)})")

    show_run(a.rail, a.traj, time_step_s=a.step, oht_radius_mm=a.oht_size,
             spawn=not a.no_spawn, save_rrd=a.save)


if __name__ == '__main__':
    _main()
