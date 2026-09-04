"""
run.py — co-simulation 실행기.

생산(PySCFabSim) + 물류(U-FAST AMHS) 통합 시뮬레이션을 실행한다.

사용 (저장소 루트에서):
  python3 -m ufast.cosim.run [dataset_dir] [rail_file] [--days N] [--oht N] [--seed N]
기본값: dataset/HVLM, dataset/SMAT2022.rail, days=1, oht=200, seed=0
"""
from __future__ import annotations
import os
import statistics
import sys
import time

# 파일 직접 실행(python src/ufast/cosim/run.py) 지원용 — src 를 sys.path 에 추가
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
    """사용자 전략 파일(.py/.pkl)을 로드해 AMHS/route 층에 주입한다.

    routing / assignment / idle_positioning 은 AMHSExecutor 훅으로, routing_cost 는
    RouteManager 의 pathfinder 엣지 비용 함수로 들어간다(설정 시 C 가속 경로탐색은
    비활성화되고 순정 파이썬 경로로 폴백 — 느려지는 것이 정상). 반환: 실제 적용된
    {kind: 파일명} (결과 meta 기록용).
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
            print(f"[{tag}] ⚠️  custom {kind} 로드 실패 ({path}): {e}")
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

    # ── 물류 레이어 ──
    rm = RouteManager()
    rm.load_from_rail(rail_file)
    machine_equipment = load_machine_equipment(infer_layout_dir(rail_file), rm.network)

    # ── 입력 정합성: 데이터셋 family ↔ 레일 목적지 ──
    # 미해석 family 의 이송은 skipped_transport 로 조용히 스킵되므로 사전 검사.
    # STNFAMLOC='Delay' 는 물리 장비가 없는 가상 스테이션 — rail 매핑 대상 아님.
    from ufast.common.consistency import check_production_families
    families = {d['STNFAM'] for d in files['tool.txt.1l']
                if d.get('STNFAMLOC') != 'Delay'}
    report = check_production_families(families, rm.network.eq_to_node,
                                       machine_equipment)
    print(f"[U-FAST] {report.summary()}")
    if not report.ok:
        print("[U-FAST] ⚠️  위 family 의 이송은 실행 중 스킵됩니다 "
              "(결과의 skipped_transport 로 집계). --strict 로 중단 가능.")
    if strict:
        report.raise_if_invalid()
    # 라우팅은 거리 기준(line=curve=1 → move_in_time=distance) — LogiFabSim 의
    # length-shortest path 와 동일. 이동시간은 가감속 운동학이 별도 계산.
    rm.initialize(line_speed=1.0, curve_speed=1.0)
    # U-FAST novelty — section 단위 라우팅. ufast 도 fromto 모드와 동일하게
    # SectionNodeBridge 위에서 동작한다. ds=None 이면 entry/exit 추론은
    # node_list[0]/[-1] 폴백을 쓰지만 ufast 라우팅 정확도엔 영향 없다.
    bridge = SectionNodeBridge(rm)
    bridge.build_mapping()
    # F-perf — ufast 정적 라우팅에서는 estimate_section_route_cost 의 결과가
    # 변하지 않으므로 (traffic_penalty 동적 변동 없음) 캐싱이 안전·필수.
    # nearest/congestion 전략의 dispatcher 비용을 한 자릿수 ms 로 압축.
    # Phase 2 — 정적 라우팅(routing_model != 'dynamic')에서는 free-flow 경로가
    # 불변이므로 section route 캐시를 영구화(TTL=∞)해 반복 Dijkstra 를 제거한다.
    # dynamic 라우팅은 혼잡에 따라 경로가 바뀌므로 기존 TTL(5s) + section 변동 시
    # clear_route_cost_cache() 무효화를 유지한다. 혼잡→이송시간 효과는 이동 중
    # _congestion_multiplier 가 별도 적용하므로 경로 캐싱과 무관(핵심모델 무손상).
    _route_cache_ttl = 5.0 if routing_model == 'dynamic' else float('inf')
    bridge.enable_route_cost_cache(True, ttl=_route_cache_ttl)
    # ufast 은 production 시간 척도(일 단위)라 dispatcher 호출이 수만~수십만
    # 발생. logistics_logger 는 legacy 분석 용도이므로 ufast 에서는 비활성화.
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
    print(f"[U-FAST] 레일: 노드 {len(nodes)} / 링크 {rm.network.link_count} / "
          f"family {len(rm.network.eq_to_node)} / sections {len(bridge.section_to_nodes)}")
    print(f"[U-FAST] 차량: {vehicle_spec.describe()}")
    print(f"[U-FAST] AMHS dispatch 전략: {amhs_strategy} (section-aware)")
    if congestion_model == 'queue':
        caps = amhs.section_capacity.values()
        print(f"[U-FAST] 혼잡 모델: queue (blocking) — footprint "
              f"{amhs.oht_footprint_mm:.0f}mm, section 용량 "
              f"min {min(caps)} / max {max(caps)} (α 미사용)")
    else:
        print(f"[U-FAST] 혼잡 모델: {congestion_model} (α={congestion_alpha})")
    print(f"[U-FAST] Idle positioning: {idle_positioning}")
    print(f"[U-FAST] Routing model: {routing_model}")
    if warmup_policy.measurement_start_s:
        print(f"[U-FAST] Warm-up: static {warmup_policy.static_warmup_days}d, "
              f"AMHS settling {warmup_policy.amhs_settling_days}d, "
              f"KPI from {warmup_policy.measurement_start_days}d")

    # F17 — Custom strategy plugin (선택). path 가 비어있지 않으면 로드 후 inject.
    custom_used = _inject_custom_strategies(
        amhs, rm, bridge, routing=custom_routing_path,
        assignment=custom_assignment_path, idle_positioning=custom_idle_path,
        routing_cost=custom_routing_cost_path, tag='U-FAST')

    # ── 생산 레이어 + seam ──
    # 플러그인: CostPlugin (KPI) + (viz 시) 머신 가동 기록
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
    # F10 — idle reposition tick 첫 schedule (idle_positioning='off' 면 no-op).
    amhs.schedule_first_reposition(0.0)
    disp = dispatcher_map[dispatcher]
    print(f"[U-FAST] 생산: machine {len(instance.machines)} / lot {len(instance.dispatchable_lots)}")

    # ── 통합 루프 ──
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

    # ── 결과 저장 + 분석 출력 ──
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
    # F16 — CSV 4종도 같이 export (데이터 없으면 자동 skip)
    csv_paths = save_csv_exports(
        out_path,
        trip_log=amhs.trip_log,
        kpi_snapshots=amhs.kpi_snapshots,
        done_lots=instance.done_lots,
        machine_activities=(machine_plugin.activities if machine_plugin else None))
    print(f"\n[U-FAST] 결과 저장: {out_path}")
    if csv_paths:
        print(f"[U-FAST] CSV export: {len(csv_paths)} files — "
              f"{', '.join(os.path.basename(p) for p in csv_paths)}")
    analyze_one(out_path)

    # ── 시각화 (Phase 2 + 3a: 레이아웃 + OHT 궤적 + 머신 활동 재생) ──
    if viz:
        traj_path = out_path.replace('.json', '_trajectories.json')
        try:
            from ufast.viz.trajectory import TrajectoryLog
            activities = machine_plugin.activities if machine_plugin else []
            # family 별 machine 수 — replay 에서 부하율 색상 gradient 에 사용
            family_sizes = {fam: len(ms) for fam, ms in instance.family_machines.items()}
            traj_log = TrajectoryLog.from_amhs_log(
                amhs.trip_log, amhs.initial_positions,
                machine_activities=activities,
                family_sizes=family_sizes,
                kpi_snapshots=amhs.kpi_snapshots,
                oht_total=len(amhs.ohts),
            )
            traj_log.save(traj_path)
            print(f"\n[U-FAST] 궤적 저장: {traj_path} "
                  f"(trip {len(traj_log.trips):,}, "
                  f"machine activities {len(activities):,}, "
                  f"family {len(family_sizes)})")

            from ufast.viz.rerun_replay import show_run
            print("[U-FAST] Rerun 시각화 시작 (Phase 2 + 3a)...")
            show_run(rail_file, traj_path)
        except Exception as e:
            print(f"[U-FAST] ⚠️  시각화 실패: {e}")

    # traj_path: viz=True 면 저장 경로, 아니면 빈 문자열
    saved_traj = traj_path if viz else ''
    return instance, amhs, saved_traj


def _build_arg_parser():
    """CLI 인자 정의 — SoftwareX 공개 대비 argparse 로 정리 (--help 제공)."""
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
