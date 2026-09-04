"""
analyze.py — ufast 실행 결과(JSON) 를 읽어 KPI 와 LogiFabSim 논문 대비 비교를 출력.

사용:
  python3 ufast/cosim/analyze.py <result.json> [<result.json> ...]
  python3 ufast/cosim/analyze.py                  # results/ 의 최신 1개 자동
"""
from __future__ import annotations
import glob
import json
import os
import sys
from typing import Any, Dict, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from ufast.cosim.reference import (PAPER_TABLE_1_HVLM,
                             PAPER_TABLE_3_HMLV_BASELINE,
                             PAPER_TABLE_2_WALL_TIMES_1Y)


def _configure_console_output():
    """Avoid UnicodeEncodeError on Windows legacy consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


_configure_console_output()


# ── 포맷 헬퍼 ────────────────────────────────────────────────
def _n(v: Optional[float], w: int = 8, prec: int = 1) -> str:
    if v is None:
        return '-'.rjust(w)
    if isinstance(v, float):
        return f"{v:>{w}.{prec}f}"
    return f"{v:>{w}}"


def _hr(width: int = 90, ch: str = '='):
    print(ch * width)


# ── 섹션 출력 ────────────────────────────────────────────────
def _run_summary(results: Dict[str, Any]):
    m = results['meta']
    p = results['production']
    _hr(); print("실행 요약"); _hr()
    print(f"  dataset={m.get('dataset_short')} | rail={m.get('rail_short')} | "
          f"days={m.get('days')} | OHT={m.get('num_oht')} | "
          f"α={m.get('alpha')} | seed={m.get('seed')}")
    print(f"  policies: prod={m.get('dispatcher', 'fifo')} | "
          f"AMHS={m.get('amhs_strategy', 'fifo')} | "
          f"cong={m.get('congestion_model', 'section_local')} | "
          f"idle={m.get('idle_positioning', 'off')} | "
          f"routing={m.get('routing_model', 'off')}")
    print(f"  wall {m.get('wall_time_s', 0):.1f}s | "
          f"sim {m.get('sim_time_days', 0):.2f}d | "
          f"완료 lot {p['completed']} / 진행중 {p['active']} | "
          f"AMHS 이송 {p.get('transport_count', 0)} / "
          f"static {p.get('static_transport_count', 0)} "
          f"(skip {p.get('skipped_transport', 0)})")

    equipment = results.get('equipment', {})
    if equipment:
        print(f"  starvation avg {equipment.get('avg_starvation_s', 0):.1f}s "
              f"/ p95 {equipment.get('p95_starvation_s', 0):.1f}s "
              f"/ count {equipment.get('starvation_count', 0)} | "
              f"breakdown {equipment.get('breakdown_count', 0)} "
              f"({equipment.get('breakdown_time_s', 0):.1f}s) | "
              f"PM {equipment.get('pm_count', 0)} "
              f"({equipment.get('pm_time_s', 0):.1f}s)")


def _production_by_category(results: Dict[str, Any]):
    by_cat = results['production'].get('by_category') or {}
    if not by_cat:
        return
    print(); _hr(); print("생산 KPI — 카테고리 집계"); _hr()
    print(f"  {'Category':<10} | {'Through':>8} | {'CycleTime[d]':>14} | "
          f"{'On-Time[%]':>11} | {'Wait[s]':>10} | {'Trans[s]':>10}")
    for cat in ['Regular', 'Hot', 'SuperHot']:
        v = by_cat.get(cat)
        if not v:
            continue
        print(f"  {cat:<10} | {_n(v['throughput'], 8, 0)} | "
              f"{_n(v['avg_cycle_days'], 14, 2)} | {_n(v['on_time_pct'], 11, 1)} | "
              f"{_n(v['avg_waiting_s'], 10, 0)} | {_n(v['avg_transport_s'], 10, 0)}")


def _production_by_lot(results: Dict[str, Any]):
    by_lot = results['production'].get('by_lot_type') or {}
    if not by_lot:
        return
    print(); _hr(); print("생산 KPI — Lot 종류별"); _hr()
    print(f"  {'Lot':<16} | {'Through':>8} | {'CycleTime[d]':>14} | "
          f"{'On-Time[%]':>11} | {'Tardy[s]':>10} | {'Trans[s]':>10}")
    for name in sorted(by_lot):
        v = by_lot[name]
        if not v:
            continue
        print(f"  {name:<16} | {_n(v['throughput'], 8, 0)} | "
              f"{_n(v['avg_cycle_days'], 14, 2)} | {_n(v['on_time_pct'], 11, 1)} | "
              f"{_n(v['avg_tardiness_s'], 10, 0)} | {_n(v['avg_transport_s'], 10, 0)}")


def _amhs_summary(results: Dict[str, Any]):
    a = results['amhs']
    print(); _hr(); print("AMHS (U-FAST 고유)"); _hr()
    print(f"  이송 작업 수       : {a['total_jobs']:,}")
    print(f"  평균 free-flow     : {a['avg_free_flow_s']:.1f} s")
    print(f"  평균 이송 소요     : {a['avg_transport_s']:.1f} s   (혼잡 반영)")
    print(f"  평균 empty / loaded: {a.get('avg_empty_travel_s', 0):.1f} / "
          f"{a.get('avg_loaded_travel_s', 0):.1f} s")
    print(f"  평균 delivery      : {a.get('avg_delivery_s', 0):.1f} s   "
          f"(request → delivery)")
    print(f"  평균 혼잡 계수     : {a['avg_congestion_factor']:.3f}   (α={a.get('congestion_alpha')})")
    print(f"  평균 OHT 대기      : {a['avg_oht_wait_s']:.1f} s")
    print(f"  최대 대기 큐       : {a['max_queue']}")
    print(f"  최대 노드 점유     : {a['max_node_inflight']}")
    print(f"  OHT 가동(종료시)   : {a['oht_busy_end']}/{a['oht_count']}")
    repo = a.get('reposition_count', 0)
    if repo:
        print(f"  Reposition 이동    : {repo:,}")


# ── 논문 비교 ────────────────────────────────────────────────
def _table1_compare(results: Dict[str, Any]):
    """HVLM → 논문 Table 1 (Kovács vs LogiFabSim vs U-FAST)."""
    if results['meta'].get('dataset_short') != 'HVLM':
        return
    by = results['production'].get('by_lot_type') or {}
    ref = PAPER_TABLE_1_HVLM['by_lot_type']
    paper_days = PAPER_TABLE_1_HVLM['days']
    fills_days = results['meta'].get('days', 0)
    scale = paper_days / fills_days if fills_days else 1.0

    print(); _hr(width=104); print("LogiFabSim 논문 Table 1 (HVLM 2년) 대비"); _hr(width=104)
    if fills_days != paper_days:
        print(f"  U-FAST run = {fills_days}일 / 논문 = {paper_days}일 - "
              f"Throughput 비교는 x{scale:.1f} 환산 표기")
        print()
    hdr = (f"  {'Lot':<16} | {'CycleTime [d]':^22} | "
           f"{'Throughput':^28} | {'On-Time [%]':^22}")
    sub = (f"  {'':<16} | {'Kovacs':>6} {'Logi':>6} {'U-FAST':>7} | "
           f"{'Kovacs':>7} {'Logi':>7} {'U-FAST':>11} | "
           f"{'Kovacs':>6} {'Logi':>6} {'U-FAST':>7}")
    print(hdr); print(sub)
    print('  ' + '-' * 100)
    for lot_name in sorted(ref):
        r = ref[lot_name]
        f = by.get(lot_name) or {}
        k, lg = r['kovacs'], r['logifabsim']
        fills_th = f.get('throughput')
        scaled = f"{fills_th}(x->{int(fills_th * scale):,})" if (fills_th and scale != 1) else _n(fills_th, 11, 0).strip()
        print(f"  {lot_name:<16} | "
              f"{_n(k['cycle_days'], 6, 0)} {_n(lg['cycle_days'], 6, 0)} "
              f"{_n(f.get('avg_cycle_days'), 7, 1)} | "
              f"{_n(k['throughput'], 7, 0)} {_n(lg['throughput'], 7, 0)} {scaled:>11} | "
              f"{_n(k['on_time_pct'], 6, 0)} {_n(lg['on_time_pct'], 6, 0)} "
              f"{_n(f.get('on_time_pct'), 7, 1)}")


def _table3_compare(results: Dict[str, Any]):
    """LVHM → 논문 Table 3 baseline (LogiFabSim c=1 vs U-FAST)."""
    if results['meta'].get('dataset_short') != 'LVHM':
        return
    by = results['production'].get('by_category') or {}
    ref = PAPER_TABLE_3_HMLV_BASELINE['by_category']
    paper_days = PAPER_TABLE_3_HMLV_BASELINE['days']
    fills_days = results['meta'].get('days', 0)
    scale = paper_days / fills_days if fills_days else 1.0

    print(); _hr(); print("LogiFabSim 논문 Table 3 (LVHM=HMLV 2년, c=1) 대비"); _hr()
    if fills_days != paper_days:
        print(f"  U-FAST run = {fills_days}일 / 논문 = {paper_days}일 - "
              f"Throughput 비교는 x{scale:.1f} 환산 표기")
        print()
    print(f"  {'Category':<10} | {'CycleTime [d]':^16} | "
          f"{'Throughput':^22} | {'On-Time [%]':^16}")
    print(f"  {'':<10} | {'Paper':>7} {'U-FAST':>8} | "
          f"{'Paper':>8} {'U-FAST':>13} | {'Paper':>7} {'U-FAST':>8}")
    print('  ' + '-' * 88)
    for cat in ['Regular', 'Hot', 'SuperHot']:
        r = ref.get(cat) or {}
        f = by.get(cat) or {}
        fills_th = f.get('throughput')
        scaled = (f"{fills_th}(x->{int(fills_th * scale):,})"
                  if (fills_th and scale != 1) else _n(fills_th, 13, 0).strip())
        print(f"  {cat:<10} | "
              f"{_n(r.get('cycle_days'), 7, 1)} {_n(f.get('avg_cycle_days'), 8, 2)} | "
              f"{_n(r.get('throughput'), 8, 0)} {scaled:>13} | "
              f"{_n(r.get('on_time_pct'), 7, 1)} {_n(f.get('on_time_pct'), 8, 1)}")


def _wall_time_compare(results: Dict[str, Any]):
    """Table 2 — 실행시간 비교 (1년 기준)."""
    m = results['meta']
    fills_wall = m.get('wall_time_s', 0)
    fills_days = m.get('days', 0)
    if not fills_days:
        return
    fills_1y = fills_wall * 365 / fills_days
    print(); _hr(); print(f"실행시간 비교 (Table 2, 1년 환산)"); _hr()
    print(f"  {'시뮬레이터':<30} | {'1년 wall (분)':>14}")
    print('  ' + '-' * 50)
    for name, secs in PAPER_TABLE_2_WALL_TIMES_1Y.items():
        print(f"  {name:<30} | {secs / 60:>14.1f}")
    print('  ' + '-' * 50)
    print(f"  {'U-FAST (extrap from ' + str(fills_days) + 'd)':<30} | "
          f"{fills_1y / 60:>14.1f}")


# ── Fromto 모드 ──────────────────────────────────────────────
def _run_summary_fromto(results: Dict[str, Any]):
    m = results['meta']
    t = results['transport']
    _hr(); print("실행 요약 (Fromto-driven)"); _hr()
    print(f"  rail={m.get('rail_short')} | fromto={m.get('fromto_short')} | "
          f"duration={m.get('sim_duration_s', 0):.0f}s | "
          f"OHT={m.get('num_oht')} | seed={m.get('seed')} | "
          f"strategy={m.get('strategy', 'nearest')}")
    print(f"  wall {m.get('wall_time_s', 0):.1f}s | "
          f"sim {m.get('sim_time_s', 0):.0f}s | "
          f"events {m.get('events_processed', 0):,}")
    print(f"  lot 생성 {t['lots_generated']:,} / 완료 {t['lots_completed']:,} "
          f"({100 * t['completion_rate']:.1f}%)")


def _fromto_transport_summary(results: Dict[str, Any]):
    t = results['transport']
    print(); _hr(); print("Transport KPI"); _hr()
    print(f"  OHT 종료 상태: {t.get('oht_final_status', {})}")
    if 'recorded_trips' in t:
        print(f"  --- trajectory(viz 시) 통계 ---")
        print(f"  기록된 trip      : {t['recorded_trips']:,}")
        print(f"  평균 이송 시간   : {t['avg_transport_s']:.1f} s")
        print(f"  평균 빈 leg      : {t['avg_empty_leg_s']:.1f} s")
        print(f"  평균 적재 leg    : {t['avg_loaded_leg_s']:.1f} s")
        print(f"  중앙값 이송 시간 : {t['median_transport_s']:.1f} s")
    ds = t.get('dispatch_stats')
    if ds:
        print(f"  --- 배차 통계 (Dispatcher) ---")
        print(f"  총 배차 시도 : {ds.get('total', 0):,}")
        print(f"  실패 (no idle): {ds.get('failed', 0):,}")
        print(f"  같은 섹션 배차: {ds.get('same_section', 0):,}")
    a = results.get('amhs')
    if a:
        print(f"  --- AMHS KPI (blocking engine) ---")
        print(f"  평균 이송 시간   : {a.get('avg_transport_s', 0):.1f} s "
              f"(free-flow {a.get('avg_free_flow_s', 0):.1f} s, "
              f"x{a.get('avg_congestion_factor', 1):.3f})")
        print(f"  평균 OHT 대기    : {a.get('avg_oht_wait_s', 0):.1f} s | "
              f"p95 delivery {a.get('delivery_p95_s', 0):.1f} s")
        print(f"  OHT 가동률       : {100 * a.get('avg_utilization', 0):.1f}%")
        print(f"  차단 {a.get('blocked_events', 0):,}회 / "
              f"{a.get('blocked_time_s', 0):,.0f}s | "
              f"데드락 강제 해소 {a.get('deadlock_forced', 0)}")


# ── 메인 ─────────────────────────────────────────────────────
def analyze_one(json_path: str):
    with open(json_path, encoding='utf-8') as f:
        results = json.load(f)
    print(f"\n# {os.path.basename(json_path)}")

    mode = results.get('meta', {}).get('mode', 'production')
    if mode == 'fromto':
        _run_summary_fromto(results)
        _fromto_transport_summary(results)
    else:
        _run_summary(results)
        _production_by_category(results)
        _production_by_lot(results)
        _amhs_summary(results)
        _table1_compare(results)
        _table3_compare(results)
        _wall_time_compare(results)
    print()


def main():
    args = sys.argv[1:]
    if not args:
        from ufast.cosim.results import DEFAULT_RESULTS_DIR
        results_dir = DEFAULT_RESULTS_DIR
        files = [f for f in glob.glob(
            os.path.join(results_dir, '**', '*.json'), recursive=True)
                 if not f.endswith('_trajectories.json')]
        if not files:
            print(f"결과 파일 없음: {results_dir}")
            sys.exit(1)
        args = [max(files, key=os.path.getmtime)]
        print(f"(최신 결과 자동 로드: {args[0]})")
    for p in args:
        analyze_one(p)


if __name__ == '__main__':
    main()
