"""
ufast/cosim/run_legacy.py — headless wrapper for fromto-driven simulation.

Used for input scenarios that have no production data (SMT2020 route/order/tool)
and only a fromto.dat. Reuses the legacy U-FAST components as they are, removing
only the PyQt timer dependency, and runs them in a pure next-event loop.

Components used (all existing U-FAST — nothing reimplemented):
  - common/rail_io.py        legacy .rail file reader (integer node IDs)
  - common/fromto_parser.py  fromto.dat → [(from_eq, to_eq, interval_s), ...]
  - core/data_set.py           SimulatorDataSet singleton
  - route/                     RouteManager + SectionNodeBridge + Dispatcher
  - control/controllers.py     VehicleController + EventHandler

Reconstructs the start_simulation + _run_step flow of the former main_ui.py headlessly.
Trajectory / KPI / Rerun replay are possible just like production mode
(ufast/cosim/run.py) — this v1 goes as far as KPI output; trajectory recording follows.
"""
from __future__ import annotations
import heapq
import os
import sys
import time
from typing import Optional

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from ufast.core.data_set import SimulatorDataSet
from ufast.control.controllers import VehicleController, EventHandler
from ufast.route import (RouteManager, SectionNodeBridge, Dispatcher,
                   NearestIdleStrategy, SameSectionFirstStrategy,
                   CongestionAwareStrategy)
from ufast.common.rail_io import load_rail_file
from ufast.common.fromto_parser import load_fromto
from ufast.paths import DATASET_DIR
from ufast.cosim.results import new_result_run_id, DEFAULT_RESULTS_DIR


# periodic idle repositioning interval (sim seconds)
_REPO_INTERVAL_S = 0.5


# legacy-mode strategy name → instance factory
_LEGACY_STRATEGIES = {
    'same_section': SameSectionFirstStrategy,
    'nearest':      NearestIdleStrategy,
    'congestion':   CongestionAwareStrategy,
}


def _make_strategy(name: str):
    if name not in _LEGACY_STRATEGIES:
        raise ValueError(
            f"unknown legacy strategy: {name!r} "
            f"(expected one of {list(_LEGACY_STRATEGIES)})")
    return _LEGACY_STRATEGIES[name]()


def run_legacy_fromto(
    rail_file: str,
    fromto_file: str,
    *,
    num_oht: int = 100,
    sim_duration_s: float = 3600.0,
    seed: int = 0,
    enable_idle_repo: bool = True,
    verbose: bool = True,
    viz: bool = False,
    strategy: str = 'nearest',
    strict: bool = False,
) -> dict:
    """
    Run a fromto-driven simulation headlessly.

    Args:
        rail_file: legacy .rail file (integer node IDs + RAILLIST required)
        fromto_file: fromto.dat (tab-separated, from_eq/to_eq/interval_seconds)
        num_oht: number of OHTs
        sim_duration_s: simulation end time (sim seconds)
        seed: random seed
        enable_idle_repo: whether to call IDLE OHT repositioning periodically
        verbose: print progress
        strict: abort the run if fromto equipment is missing from the rail EQ list

    Returns:
        result summary dict
    """
    run_id = new_result_run_id()
    import random
    random.seed(seed)

    # ── Reset the SimulatorDataSet singleton (clear leftovers from a previous ufast run) ──
    ds = SimulatorDataSet.get_instance()
    ds.clear()
    ds.main_clock = 0.0

    if verbose:
        print(f"[run_legacy] rail={rail_file}")
        print(f"[run_legacy] fromto={fromto_file}")
        print(f"[run_legacy] duration={sim_duration_s:.0f}s | OHT={num_oht} | seed={seed}")

    # ── Layout + routing (legacy path) ──
    load_rail_file(rail_file)         # fills ds.sections / ds.eq_list
    rm = RouteManager()
    rm.load_from_rail(rail_file)
    rm.initialize()
    bridge = SectionNodeBridge(rm, ds)
    bridge.build_mapping()
    if verbose:
        print(f"[run_legacy] layout: sections {len(ds.sections):,} / "
              f"EQs {len(ds.eq_list):,} / nodes {rm.network.node_count:,}")

    # ── Fromto demand data + consistency check ──
    # Records referencing equipment absent from the rail are silently filtered out by
    # init_lot_events, so print a summary up front (abort if strict).
    fromto_data = load_fromto(fromto_file)
    from ufast.common.consistency import check_fromto
    report = check_fromto(fromto_data, ds.eq_list)
    if verbose or not report.ok:
        print(f"[run_legacy] {report.summary()}")
    if not report.ok and not strict:
        print("[run_legacy] ⚠️  unmatched records are excluded from event generation. "
              "Use --strict to abort instead.")
    if strict:
        report.raise_if_invalid()

    # ── Vehicle / Dispatcher / Event Handler (legacy as is) ──
    vc = VehicleController(num_oht, rm, bridge)
    vc.init()
    vc.dispatcher = Dispatcher(rm, bridge, _make_strategy(strategy))
    if verbose:
        print(f"[run_legacy] dispatch strategy: {strategy} "
              f"({type(vc.dispatcher.strategy).__name__})")

    eh = EventHandler(vc)
    eh.init_lot_events(fromto_data, sim_duration_s)
    if verbose:
        print(f"[run_legacy] LOT events scheduled: {ds.lot_count:,}")

    # ── Trajectory recording (when viz) ──
    recorder = None
    if viz:
        from ufast.cosim.legacy_trajectory import LegacyTrajectoryRecorder
        recorder = LegacyTrajectoryRecorder(vc, bridge)
        recorder.snapshot_initial()
        if verbose:
            print(f"[run_legacy] trajectory recording enabled (viz)")

    # ── Headless next-event loop (replaces the Qt timer) ──
    t0 = time.time()
    last_repo = 0.0
    processed = 0

    while ds.event_queue:
        evt = ds.event_queue[0]
        if evt.time_scheduled > sim_duration_s:
            break
        heapq.heappop(ds.event_queue)
        if evt.time_scheduled > ds.main_clock:
            ds.main_clock = evt.time_scheduled
        eh.process_event(evt)
        processed += 1

        # observe OHT states
        if recorder is not None:
            recorder.observe(ds.main_clock)

        # periodic IDLE repositioning
        if enable_idle_repo and ds.main_clock - last_repo >= _REPO_INTERVAL_S:
            eh.reposition_idle_ohts(ds.main_clock)
            last_repo = ds.main_clock
            # repositioning also changes OHT positions — observe
            if recorder is not None:
                recorder.observe(ds.main_clock)

    elapsed = time.time() - t0

    if verbose:
        print(f"\n[run_legacy] simulation finished — wall {elapsed:.1f}s, "
              f"sim {ds.main_clock:.1f}s, events {processed:,}")

    # ── meta + save results + print analysis (same flow as production) ──
    from ufast.cosim.results import (collect_fromto_results, save_results,
                               auto_result_path, save_csv_exports)
    from ufast.cosim.analyze import analyze_one

    meta = {
        'mode': 'fromto',
        'run_id': run_id,
        'rail_file': rail_file,
        'fromto_file': fromto_file,
        'rail_short': os.path.basename(rail_file),
        'fromto_short': os.path.basename(fromto_file),
        'num_oht': num_oht,
        'sim_duration_s': sim_duration_s,
        'seed': seed,
        'strategy': strategy,
        'wall_time_s': round(elapsed, 2),
        'sim_time_s': ds.main_clock,
        'events_processed': processed,
    }

    # ── Save trajectory (when viz) ──
    trajectory_data = None
    traj_path = None
    if viz and recorder is not None:
        import json
        traj_path = _trajectory_out_path(rail_file, fromto_file, num_oht,
                                         sim_duration_s, seed, strategy, run_id)
        os.makedirs(os.path.dirname(traj_path), exist_ok=True)
        trajectory_data = recorder.build_log_data()
        with open(traj_path, 'w', encoding='utf-8') as f:
            json.dump(trajectory_data, f, ensure_ascii=False)
        if verbose:
            print(f"\n[run_legacy] trajectories saved: {traj_path} "
                  f"(trip {len(recorder.trips):,})")

    # ── Unified results / analyze ──
    results = collect_fromto_results(meta, vc, ds, trajectory_data)
    out_path = save_results(results, auto_result_path(DEFAULT_RESULTS_DIR, meta))
    # Fromto mode can only export the trip CSV (kpi/lots/machines are production-only)
    if recorder is not None and recorder.trips:
        csv_paths = save_csv_exports(out_path, trip_log=recorder.trips)
        if verbose and csv_paths:
            print(f"[run_legacy] CSV export: {len(csv_paths)} file — "
                  f"{', '.join(os.path.basename(p) for p in csv_paths)}")
    if verbose:
        print(f"[run_legacy] results saved: {out_path}")
        analyze_one(out_path)

    # raw result for compatibility (referenced by CosimWorker etc.)
    raw_result = {
        **meta,
        'lot_count': ds.lot_count,
        'lot_processed': ds.num_of_processed_lot,
        'completion_rate': (ds.num_of_processed_lot / ds.lot_count
                            if ds.lot_count else 0.0),
        'oht_status': {s: sum(1 for o in vc.oht_list.values() if o.status == s)
                       for s in ('IDLE', 'ASSIGNED', 'LOADED', 'REPOSITIONING')},
        'dispatch_stats': vc.dispatcher.get_stats() if vc.dispatcher else None,
        'result_path': out_path,
    }
    if traj_path:
        raw_result['trajectory_path'] = traj_path
        raw_result['recorded_trips'] = len(recorder.trips)

    # ── Rerun replay (when viz) ──
    if viz and traj_path:
        try:
            from ufast.viz.rerun_replay import show_run
            if verbose:
                print("[run_legacy] starting Rerun visualisation (Fromto mode)...")
            show_run(rail_file, traj_path)
        except Exception as e:
            print(f"[run_legacy] ⚠️  visualisation failed: {e}")

    return raw_result


def _trajectory_out_path(rail_file, fromto_file, num_oht, duration, seed, strategy,
                         run_id):
    rail_name = os.path.splitext(os.path.basename(rail_file))[0]
    fromto_name = os.path.splitext(os.path.basename(fromto_file))[0]
    return os.path.join(DEFAULT_RESULTS_DIR, run_id,
                        f"fromto_{rail_name}_{fromto_name}"
                        f"_{int(duration)}s_{num_oht}oht_{strategy}"
                        f"_s{seed}_trajectories.json")


def _build_arg_parser():
    """CLI argument definition — provided with argparse, same as production mode (run.py)."""
    import argparse
    p = argparse.ArgumentParser(
        prog='ufast-fromto',
        description='U-FAST — fromto-driven AMHS-only simulation '
                    '(no production layer; rail layout + fromto.dat).',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('rail', nargs='?',
                   default=os.path.join(DATASET_DIR, 'case1.rail'),
                   help='legacy .rail layout file (integer node IDs + RAILLIST)')
    p.add_argument('fromto', nargs='?',
                   default=os.path.join(DATASET_DIR, 'case1_Fromto.dat'),
                   help='fromto.dat (tab-separated: from_eq, to_eq, interval_s)')

    g = p.add_argument_group('simulation')
    g.add_argument('--oht', type=int, default=50, help='OHT fleet size')
    g.add_argument('--duration', type=float, default=3600.0,
                   help='simulated seconds')
    g.add_argument('--seed', type=int, default=0, help='random seed')
    g.add_argument('--strategy', choices=sorted(_LEGACY_STRATEGIES),
                   default='nearest', help='OHT assignment strategy')

    p.add_argument('--strict', action='store_true',
                   help='abort if fromto references equipment missing from the '
                        'rail layout (default: warn and drop those records)')
    p.add_argument('--viz', action='store_true',
                   help='record trajectories and open the Rerun viewer')
    return p


def main():
    a = _build_arg_parser().parse_args()
    from ufast.common.consistency import ConsistencyError
    try:
        run_legacy_fromto(a.rail, a.fromto, num_oht=a.oht,
                          sim_duration_s=a.duration, seed=a.seed, viz=a.viz,
                          strategy=a.strategy, strict=a.strict)
    except ConsistencyError as e:
        sys.exit(f"[run_legacy] ❌ --strict: {e}")


if __name__ == '__main__':
    main()
