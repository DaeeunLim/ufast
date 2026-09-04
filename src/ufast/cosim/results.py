"""
results.py — extract/save ufast run results as a KPI dict.

Output format: JSON (aggregated KPIs) + 4 CSVs (raw data for paper/R/pandas analysis):
  - trips.csv          per OHT trip (filled only with record_trajectory)
  - kpi_timeseries.csv KPI snapshot time series (filled only with record_trajectory)
  - lots.csv           per completed lot (production mode)
  - machines.csv       machine activity periods (when MachineActivityPlugin is enabled)

Aggregation units:
  - by_lot_type : individual LOT (Lot_3, HotLot_3, ...) — matches paper Table 1 (HVLM)
  - by_category : Regular/Hot/SuperHot — matches paper Table 3 (LVHM=HMLV)
"""
from __future__ import annotations
import csv
import json
import os
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from ufast.common.equipment_kpi import collect_equipment_kpis

# Result root: <repo>/results (outside the source tree)
from ufast.paths import RESULTS_DIR as DEFAULT_RESULTS_DIR


def new_result_run_id() -> str:
    """Return a filesystem-safe, locally timestamped simulation run id."""
    return datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')


def categorize(lot_name: str) -> str:
    """LOT name → priority category."""
    if lot_name.startswith('SuperHotLot'):
        return 'SuperHot'
    if lot_name.startswith('HotLot'):
        return 'Hot'
    if lot_name.startswith('Lot'):
        return 'Regular'
    return 'Other'


def _safe_mean(seq):
    seq = list(seq)
    return statistics.mean(seq) if seq else 0.0


def _fmt_suffix_number(value: Any) -> str:
    text = f"{float(value):g}"
    return text.replace('.', 'p').replace('-', 'm')


def _aggregate(lots: List) -> Dict[str, Any]:
    """KPI aggregation for a group of lots (Cycle Time / Throughput / On-Time / ...)."""
    n = len(lots)
    if n == 0:
        return None
    cts = [(l.done_at - l.release_at) / 86400 for l in lots]
    on_time = sum(1 for l in lots if l.done_at <= l.deadline_at)
    tardiness = [max(0.0, l.done_at - l.deadline_at) for l in lots]
    return {
        'throughput': n,
        'avg_cycle_days': round(statistics.mean(cts), 2),
        'median_cycle_days': round(statistics.median(cts), 2),
        'on_time_count': on_time,
        'on_time_pct': round(100 * on_time / n, 1),
        'avg_tardiness_s': round(_safe_mean(tardiness), 1),
        'avg_waiting_s': round(_safe_mean(l.waiting_time for l in lots), 1),
        'avg_processing_s': round(_safe_mean(l.processing_time for l in lots), 1),
        'avg_transport_s': round(_safe_mean(l.transport_time for l in lots), 1),
    }


def _percentile(sorted_vals: List[float], q: float) -> float:
    """Linearly interpolated percentile (q ∈ [0,100]). sorted_vals is assumed ascending."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_vals[0]
    idx = (n - 1) * q / 100.0
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def _tail_stats(samples: List[float], prefix: str) -> Dict[str, float]:
    """Tail statistics of measurement-window samples (delivery-time tail vs production KPIs)."""
    s = sorted(samples)
    return {
        f'{prefix}_p50_s': round(_percentile(s, 50), 2),
        f'{prefix}_p95_s': round(_percentile(s, 95), 2),
        f'{prefix}_p99_s': round(_percentile(s, 99), 2),
        f'{prefix}_max_s': round(s[-1], 2) if s else 0.0,
    }


def collect_results(instance, amhs, meta: Dict[str, Any]) -> Dict[str, Any]:
    """Collect the KPIs of a completed simulation into a dict."""
    if hasattr(amhs, '_update_busy_time'):
        amhs._update_busy_time(instance.current_time)
    done = instance.done_lots
    by_lot: Dict[str, List] = {}
    by_cat: Dict[str, List] = {'Regular': [], 'Hot': [], 'SuperHot': [], 'Other': []}
    for lot in done:
        by_lot.setdefault(lot.name, []).append(lot)
        by_cat[categorize(lot.name)].append(lot)

    production = {
        'completed': len(done),
        'active': len(instance.active_lots),
        'transport_count': getattr(instance, 'transport_count', 0),
        'static_transport_count': getattr(instance, 'static_transport_count', 0),
        'static_transport_time_s': round(getattr(instance, 'static_transport_time', 0.0), 2),
        'skipped_transport': getattr(instance, 'skipped_transport', 0),
        'same_node_transport': getattr(instance, 'same_node_transport', 0),
        'reserved_transport': getattr(instance, 'reserved_transport', 0),
        'preassigned_machine': getattr(instance, 'preassigned_machine', 0),
        'by_lot_type': {k: _aggregate(v) for k, v in sorted(by_lot.items())},
        'by_category': {k: _aggregate(v) for k, v in by_cat.items() if v},
    }

    amhs_kpi = _amhs_kpis(amhs, instance.current_time)
    return {
        'meta': meta,
        'production': production,
        'equipment': collect_equipment_kpis(instance),
        'amhs': amhs_kpi,
    }


def _amhs_kpis(amhs, current_time: float) -> Dict[str, Any]:
    """AMHSExecutor cumulative statistics → KPI dict (shared by production / fromto)."""
    amhs_kpi = {
        'measurement_start_s': getattr(amhs, 'measurement_start_time', 0.0),
        'total_requested_jobs': getattr(amhs, 'total_requested_jobs', amhs.total_jobs),
        'total_jobs': amhs.total_jobs,
        'avg_free_flow_s': (round(amhs.total_free_flow / amhs.total_jobs, 2)
                            if amhs.total_jobs else 0.0),
        'avg_transport_s': (round(amhs.total_transport_time / amhs.total_jobs, 2)
                            if amhs.total_jobs else 0.0),
        'avg_empty_travel_s': (round(getattr(amhs, 'total_empty_time', 0.0) / amhs.total_jobs, 2)
                               if amhs.total_jobs else 0.0),
        'avg_loaded_travel_s': (round(getattr(amhs, 'total_loaded_time', 0.0) / amhs.total_jobs, 2)
                                if amhs.total_jobs else 0.0),
        'avg_delivery_s': (round(
            (amhs.total_wait_for_oht + amhs.total_transport_time) / amhs.total_jobs, 2)
                           if amhs.total_jobs else 0.0),
        'avg_congestion_factor': (round(amhs.total_transport_time / amhs.total_free_flow, 4)
                                  if amhs.total_free_flow else 1.0),
        'avg_oht_wait_s': (round(amhs.total_wait_for_oht / amhs.total_jobs, 2)
                           if amhs.total_jobs else 0.0),
        **_tail_stats(getattr(amhs, 'delivery_samples', []), 'delivery'),
        **_tail_stats(getattr(amhs, 'transport_samples', []), 'transport'),
        'max_queue': amhs.max_queue,
        # Meaning after the section-aware refactor: max number of OHTs simultaneously
        # occupying one section (previously node occupancy — key name kept for compatibility)
        'max_node_inflight': amhs.max_node_inflight,
        # 'queue' congestion model (capacity blocking) only — always 0 for the other models
        'blocked_events': getattr(amhs, 'total_blocked_events', 0),
        'blocked_time_s': round(getattr(amhs, 'total_blocked_time', 0.0), 2),
        'deadlock_forced': getattr(amhs, 'deadlock_forced', 0),
        # per-section blocking heatmap data (empty dict outside queue mode)
        'blocked_by_section': {
            str(k): v
            for k, v in getattr(amhs, 'blocked_by_section', {}).items()},
        'blocked_time_by_section': {
            str(k): round(v, 1)
            for k, v in getattr(amhs, 'blocked_time_by_section', {}).items()},
        'reposition_count': getattr(amhs, 'total_repositions', 0),
        'oht_count': len(amhs.ohts),
        'oht_busy_end': sum(1 for o in amhs.ohts if o.busy),
        'congestion_alpha': amhs.congestion_alpha,
        'avg_utilization': round(
            (getattr(amhs, 'total_busy_oht_time', 0.0) /
             ((current_time - amhs.measurement_start_time) * len(amhs.ohts)))
            if (current_time - amhs.measurement_start_time) > 0 and len(amhs.ohts) > 0 else 0.0,
            4
        )
    }
    return amhs_kpi


def collect_fromto_amhs_results(meta: Dict[str, Any], amhs, current_time: float
                                ) -> Dict[str, Any]:
    """KPI dict for logistics-only mode (AMHSExecutor engine).

    The 'transport' block keeps the same keys as the legacy engine results
    (lots_generated/…) so that analyze reads both engines' results the same way.
    The 'amhs' block is the same AMHS KPI as production mode (including blocking /
    deadlock statistics).
    """
    if hasattr(amhs, '_update_busy_time'):
        amhs._update_busy_time(current_time)
    requested = getattr(amhs, 'total_requested_jobs', amhs.total_jobs)
    transport = {
        'lots_generated': requested,
        'lots_completed': amhs.total_jobs,
        'completion_rate': (amhs.total_jobs / requested) if requested else 0.0,
        'oht_count': len(amhs.ohts),
        'oht_final_status': {
            'BUSY': sum(1 for o in amhs.ohts if o.busy),
            'IDLE': sum(1 for o in amhs.ohts if not o.busy),
        },
    }
    return {'meta': meta, 'transport': transport,
            'amhs': _amhs_kpis(amhs, current_time)}


def collect_fromto_results(meta: Dict[str, Any],
                           vehicle_controller,
                           data_set,
                           trajectory_data: Dict[str, Any] = None,
                           ) -> Dict[str, Any]:
    """
    KPI dict for fromto-driven mode — same wrapper structure as production (meta + ...).

    If trajectory_data is given, per-trip statistics are aggregated as well.
    """
    transport = {
        'lots_generated': data_set.lot_count,
        'lots_completed': data_set.num_of_processed_lot,
        'completion_rate': (data_set.num_of_processed_lot / data_set.lot_count
                            if data_set.lot_count else 0.0),
        'oht_count': len(vehicle_controller.oht_list),
        'oht_final_status': {
            s: sum(1 for o in vehicle_controller.oht_list.values() if o.status == s)
            for s in ('IDLE', 'ASSIGNED', 'LOADED', 'REPOSITIONING')
        },
    }
    if vehicle_controller.dispatcher is not None:
        transport['dispatch_stats'] = vehicle_controller.dispatcher.get_stats()

    # trajectory-based trip statistics (viz mode only)
    if trajectory_data:
        trips = trajectory_data.get('trips', [])
        if trips:
            durs = [t['empty_duration'] + t['loaded_duration'] for t in trips]
            empties = [t['empty_duration'] for t in trips]
            loads = [t['loaded_duration'] for t in trips]
            transport['recorded_trips'] = len(trips)
            transport['avg_transport_s'] = round(statistics.mean(durs), 2)
            transport['avg_empty_leg_s'] = round(statistics.mean(empties), 2)
            transport['avg_loaded_leg_s'] = round(statistics.mean(loads), 2)
            transport['median_transport_s'] = round(statistics.median(durs), 2)

    return {'meta': meta, 'transport': transport}


def save_results(results: Dict[str, Any], out_path: str) -> str:
    """Save the results as JSON."""
    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    return out_path


def save_csv_exports(json_path: str, *,
                     trip_log: Optional[List[Dict[str, Any]]] = None,
                     kpi_snapshots: Optional[List[Any]] = None,
                     done_lots: Optional[List] = None,
                     machine_activities: Optional[List[Dict[str, Any]]] = None,
                     ) -> List[str]:
    """
    Save the 4 CSVs next to the JSON — for paper / R / pandas analysis.

    Each CSV is skipped when its input is empty. Returns the list of saved paths.

    File naming: <json_base>_trips.csv / _kpi_timeseries.csv / _lots.csv / _machines.csv

    Arguments:
      trip_log         list of OHT trip dicts (amhs.trip_log or
                       legacy_trajectory.recorder.trips as is)
      kpi_snapshots    (sim_time, busy, inflight_sum, pending, delivered, max) tuples
      done_lots        list of production lot objects (name, release_at, done_at,
                       deadline_at, waiting_time, processing_time, transport_time)
      machine_activities  [{start, end, family}, ...]
    """
    base = json_path[:-5] if json_path.endswith('.json') else json_path
    written: List[str] = []

    # ── 1) trips.csv ──
    if trip_log:
        path = base + '_trips.csv'
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['oht_id', 'request_time', 'assignment_time',
                        'delivery_time', 'empty_duration', 'loaded_duration',
                        'total_duration', 'congestion',
                        'empty_path_len', 'loaded_path_len'])
            for t in trip_log:
                empty_dur = t.get('empty_duration', 0.0) or 0.0
                loaded_dur = t.get('loaded_duration', 0.0) or 0.0
                w.writerow([
                    t.get('oht_id', ''),
                    t.get('request_time', 0.0),
                    t.get('assignment_time', 0.0),
                    t.get('delivery_time', 0.0),
                    empty_dur, loaded_dur, empty_dur + loaded_dur,
                    t.get('congestion', 1.0),
                    len(t.get('empty_path', []) or []),
                    len(t.get('loaded_path', []) or []),
                ])
        written.append(path)

    # ── 2) kpi_timeseries.csv ──
    if kpi_snapshots:
        path = base + '_kpi_timeseries.csv'
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['sim_time', 'busy_oht', 'section_inflight_sum',
                        'pending_queue', 'cumulative_delivered',
                        'max_section_inflight'])
            for snap in kpi_snapshots:
                w.writerow(list(snap))
        written.append(path)

    # ── 3) lots.csv ──
    if done_lots:
        path = base + '_lots.csv'
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['lot_name', 'release_at', 'done_at', 'deadline_at',
                        'cycle_time_days', 'tardiness_s',
                        'waiting_s', 'processing_s', 'transport_s',
                        'on_time'])
            for lot in done_lots:
                release = getattr(lot, 'release_at', 0.0)
                done_at = getattr(lot, 'done_at', 0.0)
                deadline = getattr(lot, 'deadline_at', 0.0)
                cycle_d = (done_at - release) / 86400 if done_at else 0.0
                tardy = max(0.0, done_at - deadline)
                w.writerow([
                    getattr(lot, 'name', ''),
                    release, done_at, deadline,
                    round(cycle_d, 3),
                    round(tardy, 1),
                    round(getattr(lot, 'waiting_time', 0.0), 1),
                    round(getattr(lot, 'processing_time', 0.0), 1),
                    round(getattr(lot, 'transport_time', 0.0), 1),
                    1 if done_at <= deadline else 0,
                ])
        written.append(path)

    # ── 4) machines.csv ──
    if machine_activities:
        path = base + '_machines.csv'
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['family', 'start_time', 'end_time', 'duration_s'])
            for a in machine_activities:
                start = a.get('start', 0.0)
                end = a.get('end', 0.0)
                w.writerow([a.get('family', ''), start, end, end - start])
        written.append(path)

    return written


def auto_result_path(results_dir: str, meta: Dict[str, Any]) -> str:
    """Automatic result file naming (per mode). results_dir is the result root (usually DEFAULT_RESULTS_DIR)."""
    mode = meta.get('mode', 'production')
    if mode == 'fromto':
        rail = os.path.splitext(meta.get('rail_short', 'unk'))[0]
        ft = os.path.splitext(meta.get('fromto_short', 'unk'))[0]
        strat = meta.get('strategy', 'nearest')
        cmod = meta.get('congestion_model')
        cmod_short = ('' if not cmod else
                      '_' + {'section_local': 'sl', 'global_tip': 'gt',
                             'off': 'no'}.get(cmod, cmod))
        name = (f"fromto_{rail}_{ft}"
                f"_{int(meta.get('sim_duration_s', 0))}s"
                f"_{meta.get('num_oht', 0)}oht"
                f"_{strat}{cmod_short}"
                f"_s{meta.get('seed', 0)}.json")
    else:
        strat = meta.get('amhs_strategy', 'fifo')
        disp = meta.get('dispatcher', 'fifo')
        cmod = meta.get('congestion_model', 'section_local')
        idle = meta.get('idle_positioning', 'off')
        rout = meta.get('routing_model', 'off')
        # abbreviations
        cmod_short = {'section_local': 'sl', 'global_tip': 'gt', 'off': 'no'}.get(cmod, cmod)
        # only non-default options go into the file name — avoids bloating it with 'off's
        extras = ''
        if idle != 'off':
            extras += f'_idle{idle}'
        if rout != 'off':
            extras += f'_r{rout[:3]}'  # 'dynamic' → 'rdyn'
        msel = meta.get('machine_selection', 'exact')
        if msel != 'exact':
            extras += f'_ms{msel}'  # 'nearest' → '_msnearest' (avoids clashing with exact)
        sw = float(meta.get('static_warmup_days', 0.0) or 0.0)
        settle = float(meta.get('amhs_settling_days', 0.0) or 0.0)
        if sw:
            extras += f'_sw{_fmt_suffix_number(sw)}'
        if settle:
            extras += f'_as{_fmt_suffix_number(settle)}'
        name = (f"{meta.get('dataset_short', 'unk')}"
                f"_{meta.get('days', 0)}d"
                f"_{meta.get('num_oht', 0)}oht"
                f"_a{meta.get('alpha', 0)}"
                f"_{disp}_{strat}_{cmod_short}{extras}"
                f"_s{meta.get('seed', 0)}.json")
    run_id = str(meta.get('run_id') or new_result_run_id())
    meta.setdefault('run_id', run_id)
    return os.path.join(results_dir, run_id, name)
