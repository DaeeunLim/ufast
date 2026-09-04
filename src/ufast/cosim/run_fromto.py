"""
ufast/cosim/run_fromto.py — logistics-only mode runner (`ufast-fromto`).

Drives the AMHS layer from a rail layout (.rail) + FromTo demand table (.dat) alone,
without production data. It uses the **same AMHSExecutor** as co-simulation mode
(run.py), so the kinematic free-flow times, capacity-constrained blocking (queue) +
deadlock resolution, delay variants, assignment/routing/idle strategies, custom
plugins, KPI JSON/CSV and Rerun replay are identical. The event queue is handled
by HeapInstance (a lightweight shim) instead of UFastInstance.

`--engine legacy` invokes the former GUI-controller-based runner (run_legacy.py:
constant speed 1 m/s, no blocking) — kept only for comparison and regression.
"""
from __future__ import annotations
import os
import sys
import time

from ufast.paths import DATASET_DIR
from ufast.route import RouteManager, SectionNodeBridge
from ufast.route.logistics_logger import get_logistics_logger
from ufast.common.fromto_parser import load_fromto
from ufast.cosim.fromto_amhs import setup_fromto_amhs
from ufast.cosim.results import new_result_run_id, DEFAULT_RESULTS_DIR
from ufast.cosim.vehicle import (VehicleSpec, load_vehicle_spec,
                                 add_vehicle_args, spec_from_args)


def run_fromto(rail_file: str, fromto_file: str, *,
               num_oht: int = 50, sim_duration_s: float = 3600.0,
               seed: int = 0, amhs_strategy: str = 'nearest',
               congestion_model: str = 'queue', congestion_alpha: float = 0.05,
               idle_positioning: str = 'off', routing_model: str = 'off',
               custom_routing_path: str = '', custom_assignment_path: str = '',
               custom_idle_path: str = '', custom_routing_cost_path: str = '',
               vehicle_spec: VehicleSpec = None,
               viz: bool = False, strict: bool = False,
               verbose: bool = True) -> dict:
    """Run FromTo demand headlessly with AMHSExecutor (blocking engine).

    Returns: result summary dict (including result_path). The result JSON is saved
    under results/<run_id>/.
    """
    run_id = new_result_run_id()
    if vehicle_spec is None:
        vehicle_spec = load_vehicle_spec()
    log = print if verbose else (lambda *a, **k: None)
    log(f"[fromto] rail={rail_file}")
    log(f"[fromto] fromto={fromto_file}")
    log(f"[fromto] duration={sim_duration_s:.0f}s | OHT={num_oht} | seed={seed}")

    # ── Layout + routing (same as run.py: distance-based shortest path, times from kinematics) ──
    rm = RouteManager()
    rm.load_from_rail(rail_file)
    rm.initialize(line_speed=1.0, curve_speed=1.0)
    bridge = SectionNodeBridge(rm)
    bridge.build_mapping()
    bridge.enable_route_cost_cache(
        True, ttl=5.0 if routing_model == 'dynamic' else float('inf'))
    get_logistics_logger().enabled = False
    log(f"[fromto] rail: nodes {rm.network.node_count:,} / links "
        f"{rm.network.link_count:,} / EQ {len(rm.network.eq_to_node):,} / "
        f"sections {len(bridge.section_to_nodes):,}")

    # ── FromTo demand + consistency check ──
    fromto_data = load_fromto(fromto_file)
    from ufast.common.consistency import check_fromto
    report = check_fromto(fromto_data, rm.network.eq_to_node.keys())
    if verbose or not report.ok:
        print(f"[fromto] {report.summary()}")
    if not report.ok and not strict:
        print("[fromto] ⚠️  unmatched records are excluded from event generation. "
              "Use --strict to abort instead.")
    if strict:
        report.raise_if_invalid()

    # ── AMHS layer (same engine as co-simulation) ──
    amhs, heap = setup_fromto_amhs(
        rm, bridge, vehicle_spec.kinematics(), fromto_data, num_oht,
        sim_duration_s, seed=seed, congestion_alpha=congestion_alpha,
        amhs_strategy=amhs_strategy, congestion_model=congestion_model,
        idle_positioning=idle_positioning, routing_model=routing_model,
        record_trajectory=viz, oht_footprint_mm=vehicle_spec.footprint_mm,
        line_speed_mm_s=vehicle_spec.line_speed_mm_s,
        curve_speed_mm_s=vehicle_spec.curve_speed_mm_s)
    log(f"[fromto] vehicle: {vehicle_spec.describe()}")
    log(f"[fromto] AMHS dispatch strategy: {amhs_strategy}")
    if congestion_model == 'queue':
        caps = amhs.section_capacity.values()
        log(f"[fromto] congestion model: queue (blocking) — section capacity "
            f"min {min(caps)} / max {max(caps)}")
    else:
        log(f"[fromto] congestion model: {congestion_model} (α={congestion_alpha})")
    log(f"[fromto] transport requests scheduled: {heap.pending_count():,}")

    from ufast.cosim.run import _inject_custom_strategies
    custom_used = _inject_custom_strategies(
        amhs, rm, bridge, routing=custom_routing_path,
        assignment=custom_assignment_path, idle_positioning=custom_idle_path,
        routing_cost=custom_routing_cost_path, tag='fromto')

    # ── next-event loop ──
    t0 = time.time()
    heap.pop_until(sim_duration_s)
    elapsed = time.time() - t0
    log(f"\n[fromto] simulation finished — wall {elapsed:.1f}s, "
        f"sim {heap.current_time:.1f}s, transports completed {amhs.total_jobs:,} / "
        f"requested {amhs.total_requested_jobs:,}")

    # ── Save results + analysis (same flow as production) ──
    from ufast.cosim.results import (collect_fromto_amhs_results, save_results,
                                     auto_result_path, save_csv_exports)
    from ufast.cosim.analyze import analyze_one
    meta = {
        'mode': 'fromto',
        'engine': 'amhs',
        'run_id': run_id,
        'rail_file': rail_file,
        'fromto_file': fromto_file,
        'rail_short': os.path.basename(rail_file),
        'fromto_short': os.path.basename(fromto_file),
        'num_oht': num_oht,
        'sim_duration_s': sim_duration_s,
        'seed': seed,
        'strategy': amhs_strategy,
        'congestion_model': congestion_model,
        'alpha': congestion_alpha,
        'idle_positioning': idle_positioning,
        'routing_model': routing_model,
        'custom_strategies': custom_used,
        'vehicle': vehicle_spec.as_meta(),
        'wall_time_s': round(elapsed, 2),
        'sim_time_s': heap.current_time,
        'events_processed': heap.processed,
    }
    results = collect_fromto_amhs_results(meta, amhs, sim_duration_s)
    out_path = save_results(results, auto_result_path(DEFAULT_RESULTS_DIR, meta))
    csv_paths = save_csv_exports(out_path, trip_log=amhs.trip_log,
                                 kpi_snapshots=amhs.kpi_snapshots)
    log(f"[fromto] results saved: {out_path}")
    if csv_paths:
        log(f"[fromto] CSV export: {len(csv_paths)} files — "
            f"{', '.join(os.path.basename(p) for p in csv_paths)}")
    if verbose:
        analyze_one(out_path)

    traj_path = ''
    if viz:
        traj_path = out_path.replace('.json', '_trajectories.json')
        try:
            from ufast.viz.trajectory import TrajectoryLog
            traj_log = TrajectoryLog.from_amhs_log(
                amhs.trip_log, amhs.initial_positions,
                kpi_snapshots=amhs.kpi_snapshots, oht_total=len(amhs.ohts))
            traj_log.save(traj_path)
            log(f"[fromto] trajectories saved: {traj_path} (trips {len(traj_log.trips):,})")
            from ufast.viz.rerun_replay import show_run
            log("[fromto] starting Rerun visualisation...")
            show_run(rail_file, traj_path)
        except Exception as e:
            print(f"[fromto] ⚠️  visualisation failed: {e}")

    return {**meta, 'jobs_requested': amhs.total_requested_jobs,
            'jobs_completed': amhs.total_jobs, 'result_path': out_path,
            'trajectory_path': traj_path}


def _build_arg_parser():
    import argparse
    p = argparse.ArgumentParser(
        prog='ufast-fromto',
        description='U-FAST logistics-only mode — rail layout + FromTo demand '
                    'table, driven by the same AMHS layer as ufast-run '
                    '(no production layer).',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('rail', nargs='?',
                   default=os.path.join(DATASET_DIR, 'case1.rail'),
                   help='.rail layout file')
    p.add_argument('fromto', nargs='?',
                   default=os.path.join(DATASET_DIR, 'case1_Fromto.dat'),
                   help='FromTo .dat (tab-separated: from_eq, to_eq, '
                        'demand rate [jobs/h]; one row per pair and hour)')

    g = p.add_argument_group('simulation')
    g.add_argument('--oht', type=int, default=50, help='OHT fleet size')
    g.add_argument('--duration', type=float, default=3600.0,
                   help='simulated seconds')
    g.add_argument('--seed', type=int, default=0, help='random seed')

    g = p.add_argument_group('logistics (AMHS) — same options as ufast-run')
    g.add_argument('--strategy',
                   choices=('fifo', 'nearest', 'same_section', 'congestion'),
                   default='nearest', help='OHT assignment strategy')
    g.add_argument('--congestion',
                   choices=('off', 'global_tip', 'section_local', 'queue'),
                   default='queue',
                   help='congestion model (queue = capacity-constrained '
                        'blocking with deadlock resolution)')
    g.add_argument('--alpha', type=float, default=0.05,
                   help='congestion factor alpha (delay models)')
    g.add_argument('--idle', choices=('off', 'on'), default='off',
                   help='idle-vehicle repositioning')
    g.add_argument('--routing', choices=('off', 'dynamic'), default='off',
                   help='congestion-reactive rerouting')

    g = p.add_argument_group('custom strategies (.py / .pkl plugin files; a bare '
                             'file name is also looked up in strategies/)')
    g.add_argument('--custom-routing', metavar='PATH', default='')
    g.add_argument('--custom-assignment', metavar='PATH', default='')
    g.add_argument('--custom-idle', metavar='PATH', default='')
    g.add_argument('--custom-routing-cost', metavar='PATH', default='',
                   help='custom edge-cost function for route search')

    add_vehicle_args(p)

    g = p.add_argument_group('output')
    g.add_argument('--viz', action='store_true',
                   help='record trajectories and open the Rerun viewer')
    p.add_argument('--strict', action='store_true',
                   help='abort if the FromTo table references equipment '
                        'missing from the rail layout (default: warn and '
                        'drop those records)')
    p.add_argument('--engine', choices=('amhs', 'legacy'), default='amhs',
                   help='amhs = shared blocking engine (default); legacy = '
                        'former GUI controller (constant 1 m/s, no blocking)')
    return p


def main():
    a = _build_arg_parser().parse_args()
    from ufast.common.consistency import ConsistencyError
    try:
        if a.engine == 'legacy':
            from ufast.cosim.run_legacy import run_legacy_fromto
            strategy = a.strategy if a.strategy != 'fifo' else 'nearest'
            run_legacy_fromto(a.rail, a.fromto, num_oht=a.oht,
                              sim_duration_s=a.duration, seed=a.seed,
                              viz=a.viz, strategy=strategy, strict=a.strict)
            return
        run_fromto(a.rail, a.fromto, num_oht=a.oht, sim_duration_s=a.duration,
                   seed=a.seed, amhs_strategy=a.strategy,
                   congestion_model=a.congestion, congestion_alpha=a.alpha,
                   idle_positioning=a.idle, routing_model=a.routing,
                   custom_routing_path=a.custom_routing,
                   custom_assignment_path=a.custom_assignment,
                   custom_idle_path=a.custom_idle,
                   custom_routing_cost_path=a.custom_routing_cost,
                   vehicle_spec=spec_from_args(a), viz=a.viz, strict=a.strict)
    except ConsistencyError as e:
        sys.exit(f"[fromto] ❌ --strict: {e}")


if __name__ == '__main__':
    main()
