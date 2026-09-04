"""
ufast/cosim/run_legacy.py — Fromto-driven 시뮬레이션 헤드리스 wrapper.

생산 데이터(SMT2020 route/order/tool)가 없고 fromto.dat 만 있는 입력 시나리오에
사용. 기존 U-FAST 의 legacy 컴포넌트를 그대로 활용하되 PyQt timer 의존성만
제거해 순수 next-event 루프로 돌린다.

활용 컴포넌트 (모두 기존 U-FAST — 재구현 없음):
  - common/rail_io.py        legacy .rail 파일 reader (정수 노드 ID)
  - common/fromto_parser.py  fromto.dat → [(from_eq, to_eq, interval_s), ...]
  - core/data_set.py           SimulatorDataSet 싱글톤
  - route/                     RouteManager + SectionNodeBridge + Dispatcher
  - control/controllers.py     VehicleController + EventHandler

기존 main_ui.py 의 start_simulation + _run_step 흐름을 헤드리스로 재구성.
production 모드(ufast/cosim/run.py) 와 동일하게 trajectory / KPI / Rerun 재생 가능 —
단 본 v1 은 KPI 출력까지. trajectory 기록은 후속.
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


# 주기적 idle repositioning 간격 (sim 초)
_REPO_INTERVAL_S = 0.5


# legacy 모드 전략 이름 → 인스턴스 팩토리
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
    Fromto-driven 시뮬레이션을 헤드리스로 실행한다.

    Args:
        rail_file: legacy .rail 파일 (정수 노드 ID + RAILLIST 필요)
        fromto_file: fromto.dat (탭 구분, from_eq/to_eq/interval_seconds)
        num_oht: OHT 대수
        sim_duration_s: 시뮬레이션 종료 시각 (sim 초)
        seed: 랜덤 시드
        enable_idle_repo: IDLE OHT 재배치 주기 호출 여부
        verbose: 진행 출력
        strict: fromto 설비가 레일 EQ 에 없으면 실행 중단

    Returns:
        결과 요약 dict
    """
    run_id = new_result_run_id()
    import random
    random.seed(seed)

    # ── SimulatorDataSet 싱글톤 초기화 (이전 ufast 실행 잔재 제거) ──
    ds = SimulatorDataSet.get_instance()
    ds.clear()
    ds.main_clock = 0.0

    if verbose:
        print(f"[run_legacy] rail={rail_file}")
        print(f"[run_legacy] fromto={fromto_file}")
        print(f"[run_legacy] duration={sim_duration_s:.0f}s | OHT={num_oht} | seed={seed}")

    # ── 레이아웃 + 라우팅 (legacy 경로) ──
    load_rail_file(rail_file)         # ds.sections / ds.eq_list 채움
    rm = RouteManager()
    rm.load_from_rail(rail_file)
    rm.initialize()
    bridge = SectionNodeBridge(rm, ds)
    bridge.build_mapping()
    if verbose:
        print(f"[run_legacy] 레이아웃: sections {len(ds.sections):,} / "
              f"EQs {len(ds.eq_list):,} / nodes {rm.network.node_count:,}")

    # ── Fromto 수요 데이터 + 정합성 검사 ──
    # 레일에 없는 설비를 참조하는 레코드는 init_lot_events 가 조용히 걸러내므로
    # 사전에 요약을 출력한다 (strict 면 중단).
    fromto_data = load_fromto(fromto_file)
    from ufast.common.consistency import check_fromto
    report = check_fromto(fromto_data, ds.eq_list)
    if verbose or not report.ok:
        print(f"[run_legacy] {report.summary()}")
    if not report.ok and not strict:
        print("[run_legacy] ⚠️  미매칭 레코드는 이벤트 생성에서 제외됩니다. "
              "--strict 로 중단 가능.")
    if strict:
        report.raise_if_invalid()

    # ── Vehicle / Dispatcher / Event Handler (legacy 그대로) ──
    vc = VehicleController(num_oht, rm, bridge)
    vc.init()
    vc.dispatcher = Dispatcher(rm, bridge, _make_strategy(strategy))
    if verbose:
        print(f"[run_legacy] dispatch 전략: {strategy} "
              f"({type(vc.dispatcher.strategy).__name__})")

    eh = EventHandler(vc)
    eh.init_lot_events(fromto_data, sim_duration_s)
    if verbose:
        print(f"[run_legacy] LOT 이벤트 등록: {ds.lot_count:,}")

    # ── trajectory 기록 (viz 시) ──
    recorder = None
    if viz:
        from ufast.cosim.legacy_trajectory import LegacyTrajectoryRecorder
        recorder = LegacyTrajectoryRecorder(vc, bridge)
        recorder.snapshot_initial()
        if verbose:
            print(f"[run_legacy] trajectory 기록 활성 (viz)")

    # ── 헤드리스 next-event 루프 (Qt timer 대체) ──
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

        # OHT 상태 관찰
        if recorder is not None:
            recorder.observe(ds.main_clock)

        # 주기적 IDLE 재배치
        if enable_idle_repo and ds.main_clock - last_repo >= _REPO_INTERVAL_S:
            eh.reposition_idle_ohts(ds.main_clock)
            last_repo = ds.main_clock
            # repo 도 OHT 위치 변경 — 관찰
            if recorder is not None:
                recorder.observe(ds.main_clock)

    elapsed = time.time() - t0

    if verbose:
        print(f"\n[run_legacy] 시뮬레이션 완료 — wall {elapsed:.1f}s, "
              f"sim {ds.main_clock:.1f}s, events {processed:,}")

    # ── meta + 결과 저장 + 분석 출력 (production 과 동일 흐름) ──
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

    # ── trajectory 저장 (viz 시) ──
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
            print(f"\n[run_legacy] 궤적 저장: {traj_path} "
                  f"(trip {len(recorder.trips):,})")

    # ── 통합 results / analyze ──
    results = collect_fromto_results(meta, vc, ds, trajectory_data)
    out_path = save_results(results, auto_result_path(DEFAULT_RESULTS_DIR, meta))
    # F16 — fromto 모드는 trip CSV 만 가능 (kpi/lots/machines 는 production 전용)
    if recorder is not None and recorder.trips:
        csv_paths = save_csv_exports(out_path, trip_log=recorder.trips)
        if verbose and csv_paths:
            print(f"[run_legacy] CSV export: {len(csv_paths)} file — "
                  f"{', '.join(os.path.basename(p) for p in csv_paths)}")
    if verbose:
        print(f"[run_legacy] 결과 저장: {out_path}")
        analyze_one(out_path)

    # 호환용 raw 결과 (CosimWorker 등에서 참조)
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

    # ── Rerun 재생 (viz 시) ──
    if viz and traj_path:
        try:
            from ufast.viz.rerun_replay import show_run
            if verbose:
                print("[run_legacy] Rerun 시각화 시작 (Fromto 모드)...")
            show_run(rail_file, traj_path)
        except Exception as e:
            print(f"[run_legacy] ⚠️  시각화 실패: {e}")

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
    """CLI 인자 정의 — production 모드(run.py)와 동일하게 argparse 로 제공."""
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
