"""
analyze.py — read ufast result JSONs and print the KPIs plus a comparison against
the LogiFabSim paper.

Usage:
  ufast-analyze <result.json> [<result.json> ...]
  ufast-analyze                                   # newest one under results/ automatically
  (or: python3 -m ufast.cosim.analyze ...)
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


# ── Format helpers ───────────────────────────────────────────
def _n(v: Optional[float], w: int = 8, prec: int = 1) -> str:
    if v is None:
        return '-'.rjust(w)
    if isinstance(v, float):
        return f"{v:>{w}.{prec}f}"
    return f"{v:>{w}}"


def _hr(width: int = 90, ch: str = '='):
    print(ch * width)


# ── Section printers ─────────────────────────────────────────
def _run_summary(results: Dict[str, Any]):
    m = results['meta']
    p = results['production']
    _hr(); print("Run summary"); _hr()
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
          f"completed lots {p['completed']} / active {p['active']} | "
          f"AMHS transports {p.get('transport_count', 0)} / "
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
    print(); _hr(); print("Production KPI — by category"); _hr()
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
    print(); _hr(); print("Production KPI — by lot type"); _hr()
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
    print(); _hr(); print("AMHS (U-FAST specific)"); _hr()
    print(f"  transport jobs       : {a['total_jobs']:,}")
    print(f"  avg free-flow        : {a['avg_free_flow_s']:.1f} s")
    print(f"  avg transport time   : {a['avg_transport_s']:.1f} s   (with congestion)")
    print(f"  avg empty / loaded   : {a.get('avg_empty_travel_s', 0):.1f} / "
          f"{a.get('avg_loaded_travel_s', 0):.1f} s")
    print(f"  avg delivery         : {a.get('avg_delivery_s', 0):.1f} s   "
          f"(request → delivery)")
    print(f"  avg congestion factor: {a['avg_congestion_factor']:.3f}   (α={a.get('congestion_alpha')})")
    print(f"  avg wait for OHT     : {a['avg_oht_wait_s']:.1f} s")
    print(f"  max pending queue    : {a['max_queue']}")
    print(f"  max section inflight : {a['max_node_inflight']}")
    print(f"  OHT busy (at end)    : {a['oht_busy_end']}/{a['oht_count']}")
    repo = a.get('reposition_count', 0)
    if repo:
        print(f"  reposition moves     : {repo:,}")


# ── Paper comparison ─────────────────────────────────────────
def _table1_compare(results: Dict[str, Any]):
    """HVLM → paper Table 1 (Kovács vs LogiFabSim vs U-FAST)."""
    if results['meta'].get('dataset_short') != 'HVLM':
        return
    by = results['production'].get('by_lot_type') or {}
    ref = PAPER_TABLE_1_HVLM['by_lot_type']
    paper_days = PAPER_TABLE_1_HVLM['days']
    fills_days = results['meta'].get('days', 0)
    scale = paper_days / fills_days if fills_days else 1.0

    print(); _hr(width=104); print("vs LogiFabSim paper Table 1 (HVLM, 2 years)"); _hr(width=104)
    if fills_days != paper_days:
        print(f"  U-FAST run = {fills_days} days / paper = {paper_days} days - "
              f"throughput comparison shown scaled by x{scale:.1f}")
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
    """LVHM → paper Table 3 baseline (LogiFabSim c=1 vs U-FAST)."""
    if results['meta'].get('dataset_short') != 'LVHM':
        return
    by = results['production'].get('by_category') or {}
    ref = PAPER_TABLE_3_HMLV_BASELINE['by_category']
    paper_days = PAPER_TABLE_3_HMLV_BASELINE['days']
    fills_days = results['meta'].get('days', 0)
    scale = paper_days / fills_days if fills_days else 1.0

    print(); _hr(); print("vs LogiFabSim paper Table 3 (LVHM=HMLV, 2 years, c=1)"); _hr()
    if fills_days != paper_days:
        print(f"  U-FAST run = {fills_days} days / paper = {paper_days} days - "
              f"throughput comparison shown scaled by x{scale:.1f}")
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
    """Table 2 — wall-time comparison (1-year basis)."""
    m = results['meta']
    fills_wall = m.get('wall_time_s', 0)
    fills_days = m.get('days', 0)
    if not fills_days:
        return
    fills_1y = fills_wall * 365 / fills_days
    print(); _hr(); print(f"Wall-time comparison (Table 2, scaled to 1 year)"); _hr()
    print(f"  {'Simulator':<30} | {'1y wall (min)':>14}")
    print('  ' + '-' * 50)
    for name, secs in PAPER_TABLE_2_WALL_TIMES_1Y.items():
        print(f"  {name:<30} | {secs / 60:>14.1f}")
    print('  ' + '-' * 50)
    print(f"  {'U-FAST (extrap from ' + str(fills_days) + 'd)':<30} | "
          f"{fills_1y / 60:>14.1f}")


# ── Fromto mode ──────────────────────────────────────────────
def _run_summary_fromto(results: Dict[str, Any]):
    m = results['meta']
    t = results['transport']
    _hr(); print("Run summary (Fromto-driven)"); _hr()
    print(f"  rail={m.get('rail_short')} | fromto={m.get('fromto_short')} | "
          f"duration={m.get('sim_duration_s', 0):.0f}s | "
          f"OHT={m.get('num_oht')} | seed={m.get('seed')} | "
          f"strategy={m.get('strategy', 'nearest')}")
    print(f"  wall {m.get('wall_time_s', 0):.1f}s | "
          f"sim {m.get('sim_time_s', 0):.0f}s | "
          f"events {m.get('events_processed', 0):,}")
    print(f"  lots generated {t['lots_generated']:,} / completed {t['lots_completed']:,} "
          f"({100 * t['completion_rate']:.1f}%)")


def _fromto_transport_summary(results: Dict[str, Any]):
    t = results['transport']
    print(); _hr(); print("Transport KPI"); _hr()
    print(f"  OHT final status: {t.get('oht_final_status', {})}")
    if 'recorded_trips' in t:
        print(f"  --- trajectory statistics (with viz) ---")
        print(f"  recorded trips   : {t['recorded_trips']:,}")
        print(f"  avg transport    : {t['avg_transport_s']:.1f} s")
        print(f"  avg empty leg    : {t['avg_empty_leg_s']:.1f} s")
        print(f"  avg loaded leg   : {t['avg_loaded_leg_s']:.1f} s")
        print(f"  median transport : {t['median_transport_s']:.1f} s")
    ds = t.get('dispatch_stats')
    if ds:
        print(f"  --- assignment statistics (Dispatcher) ---")
        print(f"  total attempts   : {ds.get('total', 0):,}")
        print(f"  failed (no idle) : {ds.get('failed', 0):,}")
        print(f"  same-section     : {ds.get('same_section', 0):,}")
    a = results.get('amhs')
    if a:
        print(f"  --- AMHS KPI (blocking engine) ---")
        print(f"  avg transport    : {a.get('avg_transport_s', 0):.1f} s "
              f"(free-flow {a.get('avg_free_flow_s', 0):.1f} s, "
              f"x{a.get('avg_congestion_factor', 1):.3f})")
        print(f"  avg wait for OHT : {a.get('avg_oht_wait_s', 0):.1f} s | "
              f"p95 delivery {a.get('delivery_p95_s', 0):.1f} s")
        print(f"  OHT utilisation  : {100 * a.get('avg_utilization', 0):.1f}%")
        print(f"  blocking events {a.get('blocked_events', 0):,} / "
              f"{a.get('blocked_time_s', 0):,.0f}s | "
              f"deadlocks resolved by forced admission {a.get('deadlock_forced', 0)}")


# ── Main ─────────────────────────────────────────────────────
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
            print(f"no result files found: {results_dir}")
            sys.exit(1)
        args = [max(files, key=os.path.getmtime)]
        print(f"(auto-loading the newest result: {args[0]})")
    for p in args:
        analyze_one(p)


if __name__ == '__main__':
    main()
