"""
run.py — co-simulation runner.

Runs the integrated production (PySCFabSim) + logistics (U-FAST AMHS) simulation.

Usage (from the repository root):
  python3 -m ufast.cosim.run [dataset_dir] [rail_file] [--days N] [--oht N] [--seed N]
Defaults: dataset/HVLM, dataset/SMAT2022.rail, days=1, oht=200, seed=0
"""
from __future__ import annotations
import os
import statistics
import sys
import time

# Support running the file directly (python src/ufast/cosim/run.py) — add src to sys.path
_SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from ufast.paths import DATASET_DIR
from ufast.production.read import read_all
from ufast.production.plugins.cost_plugin import CostPlugin
from ufast.production.dispatching.dispatcher import dispatcher_map
from ufast.production.greedy import get_lots_to_dispatch_by_machine
from ufast.production.randomizer import Randomizer
from ufast.route import RouteManager, SectionNodeBridge
from ufast.route.logistics_logger import get_logistics_logger
from ufast.cosim.instance import UFastInstance
from ufast.cosim.amhs import AMHSExecutor
from ufast.cosim.equipment_mapping import infer_layout_dir, load_machine_equipment
from ufast.cosim.results import new_result_run_id
from ufast.cosim.warmup import WarmupPolicy
from ufast.cosim.vehicle import VehicleSpec, load_vehicle_spec, add_vehicle_args, spec_from_args


def _inject_custom_strategies(amhs, rm, bridge, *, routing='', assignment='',
                              idle_positioning='', routing_cost='', tag='U-FAST'):
    """Load user strategy files (.py/.pkl) and inject them into the AMHS/route layers.

    routing / assignment / idle_positioning go into AMHSExecutor hooks; routing_cost
    becomes the pathfinder edge-cost function of RouteManager (when set, the
    C-accelerated path search is disabled and falls back to the pure-Python path —
    slower by design). Returns the actually applied {kind: file name} (for the
    result meta).
    """
    used = {}
    if not (routing or assignment or idle_positioning or routing_cost):
        return used
    from ufast.common.strategy_loader import load_strategy, StrategyLoadError
    kwargs = {}
    for kind, path in (('routing', routing), ('assignment', assignment),
                       ('idle_positioning', idle_positioning),
                       ('routing_cost', routing_cost)):
        if not path:
            continue
        try:
            obj = load_strategy(path, kind)
        except StrategyLoadError as e:
            print(f"[{tag}] ⚠️  failed to load custom {kind} ({path}): {e}")
            continue
        if kind == 'routing_cost':
            rm.set_custom_cost_function(obj)
            amhs._route_cache.clear()
            bridge.clear_route_cost_cache()
        else:
            kwargs[kind] = obj
        used[kind] = os.path.basename(path)
        print(f"[{tag}] custom {kind}: {os.path.basename(path)}")
    if kwargs:
        amhs.set_custom_strategies(**kwargs)
    return used


def run_ufast(dataset_dir: str, rail_file: str, days: int = 1,
              num_oht: int = 200, dispatcher: str = 'fifo', seed: int = 0,
              vehicle_spec: VehicleSpec = None, congestion_alpha: float = 0.05,
              viz: bool = False, amhs_strategy: str = 'fifo',
              congestion_model: str = 'queue',
              idle_positioning: str = 'off',
              routing_model: str = 'off',
              custom_routing_path: str = '',
              custom_assignment_path: str = '',
              custom_idle_path: str = '',
              custom_routing_cost_path: str = '',
              machine_selection: str = 'exact',
              static_warmup_days: float = 0.0,
              amhs_settling_days: float = 0.0,
              strict: bool = False):
    run_id = new_result_run_id()
    Randomizer().random.seed(seed)
    run_to = 3600 * 24 * days
    warmup_policy = WarmupPolicy(static_warmup_days, amhs_settling_days)
    if vehicle_spec is None:
        vehicle_spec = load_vehicle_spec()

    print(f"[U-FAST] dataset={dataset_dir}")
    print(f"[U-FAST] rail={rail_file} | days={days} | OHT={num_oht} | dispatcher={dispatcher}")

    files = read_all(dataset_dir)

    # ── AMHS layer ──
    rm = RouteManager()
    rm.load_from_rail(rail_file)
    machine_equipment = load_machine_equipment(infer_layout_dir(rail_file), rm.network)

    # ── Input consistency: dataset families ↔ rail destinations ──
    # Transports of unresolved families are silently skipped as skipped_transport,
    # so check up front.
    # STNFAMLOC='Delay' is a virtual station without physical equipment — not a rail-mapping target.
    from ufast.common.consistency import check_production_families
    families = {d['STNFAM'] for d in files['tool.txt.1l']
                if d.get('STNFAMLOC') != 'Delay'}
    report = check_production_families(families, rm.network.eq_to_node,
                                       machine_equipment)
    print(f"[U-FAST] {report.summary()}")
    if not report.ok:
        print("[U-FAST] ⚠️  transports of the families above will be skipped during the run "
              "(counted as skipped_transport in the results). Use --strict to abort instead.")
    if strict:
        report.raise_if_invalid()
    # Routing is distance-based (line=curve=1 → move_in_time=distance) — same as
    # LogiFabSim's length-shortest path. Travel times are computed separately by the
    # acceleration/deceleration kinematics.
    rm.initialize(line_speed=1.0, curve_speed=1.0)
    # U-FAST novelty — section-level routing. ufast also runs on top of
    # SectionNodeBridge, just like fromto mode. With ds=None the entry/exit inference
    # falls back to node_list[0]/[-1], which does not affect ufast routing accuracy.
    bridge = SectionNodeBridge(rm)
    bridge.build_mapping()
    # F-perf — under static ufast routing the result of estimate_section_route_cost
    # does not change (no dynamic traffic_penalty variation), so caching is safe and
    # essential. Compresses the dispatcher cost of the nearest/congestion strategies
    # to single-digit ms.
    # Phase 2 — under static routing (routing_model != 'dynamic') the free-flow route
    # is immutable, so the section route cache is made permanent (TTL=∞) to remove
    # repeated Dijkstra runs. Dynamic routing changes routes with congestion, so it
    # keeps the existing TTL (5 s) plus clear_route_cost_cache() invalidation on
    # section changes. The congestion→transport-time effect is applied separately by
    # _congestion_multiplier while moving, so it is independent of route caching
    # (core model unaffected).
    _route_cache_ttl = 5.0 if routing_model == 'dynamic' else float('inf')
    bridge.enable_route_cost_cache(True, ttl=_route_cache_ttl)
    # ufast runs on the production time scale (days), so the dispatcher is called tens
    # of thousands to hundreds of thousands of times. logistics_logger is for legacy
    # analysis, so it is disabled in ufast.
    get_logistics_logger().enabled = False

    nodes = list(rm.network.nodes.keys())
    kin = vehicle_spec.kinematics()
    amhs = AMHSExecutor(rm, kin, num_oht, nodes,
                        bridge=bridge,
                        congestion_alpha=congestion_alpha,
                        record_trajectory=viz,
                        dispatch_strategy=amhs_strategy,
                        congestion_model=congestion_model,
                        idle_positioning=idle_positioning,
                        routing_model=routing_model,
                        oht_footprint_mm=vehicle_spec.footprint_mm,
                        line_speed_mm_s=vehicle_spec.line_speed_mm_s,
                        curve_speed_mm_s=vehicle_spec.curve_speed_mm_s)
    amhs.measurement_start_time = warmup_policy.measurement_start_s
    print(f"[U-FAST] rail: nodes {len(nodes)} / links {rm.network.link_count} / "
          f"family {len(rm.network.eq_to_node)} / sections {len(bridge.section_to_nodes)}")
    print(f"[U-FAST] vehicle: {vehicle_spec.describe()}")
    print(f"[U-FAST] AMHS dispatch strategy: {amhs_strategy} (section-aware)")
    if congestion_model == 'queue':
        caps = amhs.section_capacity.values()
        print(f"[U-FAST] congestion model: queue (blocking) — footprint "
              f"{amhs.oht_footprint_mm:.0f}mm, section capacity "
              f"min {min(caps)} / max {max(caps)} (α unused)")
    else:
        print(f"[U-FAST] congestion model: {congestion_model} (α={congestion_alpha})")
    print(f"[U-FAST] Idle positioning: {idle_positioning}")
    print(f"[U-FAST] Routing model: {routing_model}")
    if warmup_policy.measurement_start_s:
        print(f"[U-FAST] Warm-up: static {warmup_policy.static_warmup_days}d, "
              f"AMHS settling {warmup_policy.amhs_settling_days}d, "
              f"KPI from {warmup_policy.measurement_start_days}d")

    # F17 — Custom strategy plugins (optional). Loaded and injected when a path is non-empty.
    custom_used = _inject_custom_strategies(
        amhs, rm, bridge, routing=custom_routing_path,
        assignment=custom_assignment_path, idle_positioning=custom_idle_path,
        routing_cost=custom_routing_cost_path, tag='U-FAST')

    # ── Production layer + seam ──
    # Plugins: CostPlugin (KPI) + machine activity recording (when viz)
    plugins = [CostPlugin()]
    machine_plugin = None
    if viz:
        from ufast.cosim.machine_activity import MachineActivityPlugin
        machine_plugin = MachineActivityPlugin()
        plugins.append(machine_plugin)
    instance = UFastInstance(files, run_to, True, plugins, rm, amhs,
                            machine_equipment=machine_equipment,
                            machine_selection=machine_selection,
                            warmup_policy=warmup_policy)
    # F10 — schedule the first idle reposition tick (no-op when idle_positioning='off').
    amhs.schedule_first_reposition(0.0)
    disp = dispatcher_map[dispatcher]
    print(f"[U-FAST] production: machines {len(instance.machines)} / lots {len(instance.dispatchable_lots)}")

    # ── Integrated loop ──
    t0 = time.time()
    steps = 0
    while not instance.done:
        done = instance.next_decision_point()
        if done or instance.current_time > run_to:
            break
        machine, lots = get_lots_to_dispatch_by_machine(instance, disp)
        if lots is None:
            instance.usable_machines.remove(machine)
        else:
            instance.dispatch(machine, lots)
        steps += 1
    instance.finalize()
    elapsed = time.time() - t0

    # ── Save results + print analysis ──
    from ufast.cosim.results import (collect_results, save_results, auto_result_path,
                                DEFAULT_RESULTS_DIR, save_csv_exports)
    from ufast.cosim.analyze import analyze_one
    meta = {
        'mode': 'production',
        'run_id': run_id,
        'dataset_dir': dataset_dir,
        'rail_file': rail_file,
        'dataset_short': os.path.basename(os.path.normpath(dataset_dir)),
        'rail_short': os.path.basename(rail_file),
        'days': days,
        'num_oht': num_oht,
        'alpha': congestion_alpha,
        'seed': seed,
        'dispatcher': dispatcher,
        'amhs_strategy': amhs_strategy,
        'congestion_model': congestion_model,
        'idle_positioning': idle_positioning,
        'routing_model': routing_model,
        'machine_selection': machine_selection,
        'custom_strategies': custom_used,
        **warmup_policy.as_meta(),
        'vehicle': vehicle_spec.as_meta(),
        'wall_time_s': elapsed,
        'sim_time_days': instance.current_time_days,
        'dispatch_steps': steps,
    }
    results = collect_results(instance, amhs, meta)
    out_path = save_results(results, auto_result_path(DEFAULT_RESULTS_DIR, meta))
    # F16 — also export the 4 CSVs (skipped automatically when there is no data)
    csv_paths = save_csv_exports(
        out_path,
        trip_log=amhs.trip_log,
        kpi_snapshots=amhs.kpi_snapshots,
        done_lots=instance.done_lots,
        machine_activities=(machine_plugin.activities if machine_plugin else None))
    print(f"\n[U-FAST] results saved: {out_path}")
    if csv_paths:
        print(f"[U-FAST] CSV export: {len(csv_paths)} files — "
              f"{', '.join(os.path.basename(p) for p in csv_paths)}")
    analyze_one(out_path)

    # ── Visualisation (Phase 2 + 3a: layout + OHT trajectories + machine activity replay) ──
    if viz:
        traj_path = out_path.replace('.json', '_trajectories.json')
        try:
            from ufast.viz.trajectory import TrajectoryLog
            activities = machine_plugin.activities if machine_plugin else []
            # machines per family — used by the replay for the load-ratio colour gradient
            family_sizes = {fam: len(ms) for fam, ms in instance.family_machines.items()}
            traj_log = TrajectoryLog.from_amhs_log(
                amhs.trip_log, amhs.initial_positions,
                machine_activities=activities,
                family_sizes=family_sizes,
                kpi_snapshots=amhs.kpi_snapshots,
                oht_total=len(amhs.ohts),
            )
            traj_log.save(traj_path)
            print(f"\n[U-FAST] trajectories saved: {traj_path} "
                  f"(trip {len(traj_log.trips):,}, "
                  f"machine activities {len(activities):,}, "
                  f"family {len(family_sizes)})")

            from ufast.viz.rerun_replay import show_run
            print("[U-FAST] starting Rerun visualisation (Phase 2 + 3a)...")
            show_run(rail_file, traj_path)
        except Exception as e:
            print(f"[U-FAST] ⚠️  visualisation failed: {e}")

    # traj_path: the saved path when viz=True, otherwise an empty string
    saved_traj = traj_path if viz else ''
    return instance, amhs, saved_traj


def _build_arg_parser():
    """CLI argument definition — organised with argparse for the SoftwareX release (provides --help)."""
    import argparse
    p = argparse.ArgumentParser(
        prog='ufast-run',
        description='U-FAST — integrated production (PySCFabSim fork) + '
                    'AMHS logistics simulation.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('dataset', nargs='?',
                   default=os.path.join(DATASET_DIR, 'HVLM'),
                   help='SMT2020-format production dataset folder')
    p.add_argument('rail', nargs='?',
                   default=os.path.join(DATASET_DIR, 'SMAT2022.rail'),
                   help='.rail layout file')

    g = p.add_argument_group('simulation')
    g.add_argument('--days', type=int, default=1, help='simulated days')
    g.add_argument('--oht', type=int, default=200, help='OHT fleet size')
    g.add_argument('--seed', type=int, default=0, help='random seed')
    g.add_argument('--dispatcher', choices=('fifo', 'cr', 'random'),
                   default='fifo', help='production lot-dispatch rule')
    g.add_argument('--static-warmup-days', type=float, default=0.0,
                   help='static-transport warm-up period before co-simulation')
    g.add_argument('--amhs-settling-days', type=float, default=0.0,
                   help='exclude this initial period from AMHS KPIs')

    g = p.add_argument_group('logistics (AMHS)')
    g.add_argument('--strategy',
                   choices=('fifo', 'nearest', 'same_section', 'congestion'),
                   default='fifo', help='OHT assignment strategy')
    g.add_argument('--congestion',
                   choices=('off', 'global_tip', 'section_local', 'queue'),
                   default='queue',
                   help='congestion model. Default queue = capacity-'
                        'constrained blocking with FIFO section buffers and '
                        'deadlock resolution (alpha unused). Delay-based '
                        'alternatives: section_local (occupancy-scaled '
                        'traversal times), global_tip (LogiFabSim '
                        'reproduction), off (free-flow)')
    g.add_argument('--alpha', type=float, default=0.05,
                   help='congestion factor alpha')
    g.add_argument('--idle', choices=('off', 'on'), default='off',
                   help='idle-vehicle repositioning')
    g.add_argument('--routing', choices=('off', 'dynamic'), default='off',
                   help='congestion-reactive rerouting')
    g.add_argument('--machine-selection', choices=('exact', 'nearest'),
                   default='exact',
                   help='machine-to-equipment resolution for transport targets')

    g = p.add_argument_group('custom strategies (.py / .pkl plugin files; a bare '
                             'file name is also looked up in strategies/)')
    g.add_argument('--custom-routing', metavar='PATH', default='',
                   help='custom routing strategy file')
    g.add_argument('--custom-assignment', metavar='PATH', default='',
                   help='custom OHT assignment strategy file')
    g.add_argument('--custom-idle', metavar='PATH', default='',
                   help='custom idle-positioning strategy file')
    g.add_argument('--custom-routing-cost', metavar='PATH', default='',
                   help='custom edge-cost function for route search '
                        '(disables the C-accelerated pathfinder)')

    add_vehicle_args(p)

    g = p.add_argument_group('output')
    g.add_argument('--viz', action='store_true',
                   help='record trajectories and open the Rerun viewer')

    p.add_argument('--strict', action='store_true',
                   help='abort if dataset families cannot be resolved to rail '
                        'destinations (default: warn and skip their transports)')
    return p


def main():
    a = _build_arg_parser().parse_args()
    from ufast.common.consistency import ConsistencyError
    try:
        _run_cli(a)
    except ConsistencyError as e:
        sys.exit(f"[U-FAST] ❌ --strict: {e}")


def _run_cli(a):
    run_ufast(a.dataset, a.rail, days=a.days, num_oht=a.oht, seed=a.seed,
              dispatcher=a.dispatcher,
              vehicle_spec=spec_from_args(a),
              congestion_alpha=a.alpha, viz=a.viz, amhs_strategy=a.strategy,
              congestion_model=a.congestion,
              idle_positioning=a.idle,
              routing_model=a.routing,
              custom_routing_path=a.custom_routing,
              custom_assignment_path=a.custom_assignment,
              custom_idle_path=a.custom_idle,
              custom_routing_cost_path=a.custom_routing_cost,
              machine_selection=a.machine_selection,
              static_warmup_days=a.static_warmup_days,
              amhs_settling_days=a.amhs_settling_days,
              strict=a.strict)


if __name__ == '__main__':
    main()
