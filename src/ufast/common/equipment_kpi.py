from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def record_equipment_downtime(instance, machine, kind: str, duration_s: float) -> None:
    """Record one breakdown or PM interval on a machine."""
    duration_s = max(0.0, float(duration_s))
    start_s = float(instance.current_time)
    end_s = start_s + duration_s
    if kind == "breakdown":
        machine.bred_time += duration_s
        machine.breakdown_intervals.append((start_s, end_s))
    elif kind == "pm":
        machine.pmed_time += duration_s
        machine.pm_intervals.append((start_s, end_s))
    else:
        raise ValueError(f"unknown downtime kind: {kind!r}")


def open_starvation(machine, process_end_s: float) -> None:
    """Start a material-starvation interval after processing completes."""
    machine.starvation_open_since = float(process_end_s)


def close_starvation(machine, next_lot_time_s: float, lots=None) -> None:
    """Close starvation when the next lot becomes available for dispatch.

    Interval tuple: (start_s, end_s, transport_s).  transport_s is the overlap
    between the starvation interval and the transport window
    (last_transit_start~end) of the lot that closed it — "starvation that would
    not exist if transport were instantaneous" (transport-blocked).  The rest is
    upstream (time when material was not even in transit).  0 if lots is not given.
    """
    start_s = getattr(machine, "starvation_open_since", None)
    if start_s is None:
        return
    start_s = float(start_s)
    end_s = max(start_s, float(next_lot_time_s))
    transport_s = 0.0
    if lots and end_s > start_s:
        # For a batch dispatch the latest-ready lot determines the time — use that lot.
        binding = max(lots, key=lambda l: l.free_since if l.free_since is not None else start_s)
        ts = getattr(binding, "last_transit_start", None)
        te = getattr(binding, "last_transit_end", None)
        if ts is not None and te is not None:
            transport_s = max(0.0, min(end_s, float(te)) - max(start_s, float(ts)))
    machine.starvation_intervals.append((start_s, end_s, transport_s))
    machine.starvation_open_since = None


def _overlap(interval: tuple[float, float], start_s: float, end_s: float) -> float:
    return max(0.0, min(interval[1], end_s) - max(interval[0], start_s))


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _merge_duration(intervals: Iterable[tuple[float, float]]) -> float:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    if not ordered:
        return 0.0
    total = 0.0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    return total + cur_end - cur_start


def collect_equipment_kpis(instance) -> dict[str, Any]:
    """Aggregate starvation, breakdown, and PM KPIs for one simulation run."""
    measurement_start_s = float(getattr(instance, "measurement_start_time", 0.0) or 0.0)
    configured_end_s = float(getattr(instance, "run_to", instance.current_time))
    measurement_end_s = max(
        measurement_start_s,
        min(float(instance.current_time), configured_end_s),
    )
    by_family: dict[str, list] = defaultdict(list)
    for machine in instance.machines:
        by_family[str(machine.family)].append(machine)

    def aggregate(machines) -> dict[str, Any]:
        starvation = []
        transport_blocked = []
        censored = []
        breakdown_intervals = []
        pm_intervals = []
        downtime_s = 0.0
        breakdown_count = 0
        pm_count = 0

        for machine in machines:
            for interval in getattr(machine, "starvation_intervals", []):
                if measurement_start_s <= interval[1] <= measurement_end_s:
                    clipped = _overlap(interval, measurement_start_s, measurement_end_s)
                    starvation.append(clipped)
                    # The transport-attributed part sits at the tail of the interval
                    # (delivery time = close), so min() preserves it exactly even if the
                    # start is clipped.
                    raw_transport = interval[2] if len(interval) > 2 else 0.0
                    transport_blocked.append(min(raw_transport, clipped))
            open_since = getattr(machine, "starvation_open_since", None)
            if open_since is not None and measurement_end_s > max(open_since, measurement_start_s):
                censored.append(measurement_end_s - max(open_since, measurement_start_s))

            machine_breakdowns = []
            machine_pms = []
            for interval in getattr(machine, "breakdown_intervals", []):
                overlap = _overlap(interval, measurement_start_s, measurement_end_s)
                if overlap > 0:
                    clipped = (max(interval[0], measurement_start_s),
                               min(interval[1], measurement_end_s))
                    breakdown_intervals.append(clipped)
                    machine_breakdowns.append(clipped)
                if measurement_start_s <= interval[0] < measurement_end_s:
                    breakdown_count += 1
            for interval in getattr(machine, "pm_intervals", []):
                overlap = _overlap(interval, measurement_start_s, measurement_end_s)
                if overlap > 0:
                    clipped = (max(interval[0], measurement_start_s),
                               min(interval[1], measurement_end_s))
                    pm_intervals.append(clipped)
                    machine_pms.append(clipped)
                if measurement_start_s <= interval[0] < measurement_end_s:
                    pm_count += 1
            downtime_s += _merge_duration(machine_breakdowns + machine_pms)

        breakdown_s = sum(end - start for start, end in breakdown_intervals)
        pm_s = sum(end - start for start, end in pm_intervals)

        # ── Utilization time-budget decomposition ──
        # If busy/setup intervals (recorded at dispatch) exist, compute over an arbitrary
        # window (including warm-up) by clipping to the measurement window. Legacy runs
        # without intervals fall back to whole-run scalars (utilized_time/setuped_time)
        # — only when measurement_start_s == 0.
        has_intervals = any(getattr(m, "busy_intervals", None) for m in machines)
        window_total_s = (measurement_end_s - measurement_start_s) * len(machines)
        starvation_total_s = sum(starvation)
        if has_intervals:
            busy_s = sum(_overlap(iv, measurement_start_s, measurement_end_s)
                         for m in machines
                         for iv in getattr(m, "busy_intervals", []))
            setup_s = sum(_overlap(iv, measurement_start_s, measurement_end_s)
                          for m in machines
                          for iv in getattr(m, "setup_intervals", []))
            budget_ok = window_total_s > 0
        else:
            busy_s = sum(float(getattr(m, "utilized_time", 0.0)) for m in machines)
            setup_s = sum(float(getattr(m, "setuped_time", 0.0)) for m in machines)
            budget_ok = measurement_start_s == 0 and window_total_s > 0
        if budget_ok:
            # May go slightly negative when starvation and downtime intervals overlap —
            # tolerated as a boundary error.
            idle_other_s = (window_total_s - busy_s - setup_s - downtime_s
                            - starvation_total_s - sum(censored))
            utilization_pct = round(100.0 * busy_s / window_total_s, 2)
        else:
            idle_other_s = None
            utilization_pct = None

        return {
            "machine_count": len(machines),
            "busy_time_s": round(busy_s, 2),
            "setup_time_s": round(setup_s, 2),
            "utilization_pct": utilization_pct,
            "idle_other_s": round(idle_other_s, 2) if idle_other_s is not None else None,
            "starvation_count": len(starvation),
            "total_starvation_s": round(sum(starvation), 2),
            "transport_blocked_starvation_s": round(sum(transport_blocked), 2),
            "upstream_starvation_s": round(sum(starvation) - sum(transport_blocked), 2),
            "transport_blocked_starvation_pct": (
                round(100.0 * sum(transport_blocked) / sum(starvation), 1)
                if sum(starvation) > 0 else 0.0),
            "transport_blocked_starvation_count": sum(1 for t in transport_blocked if t > 0),
            "avg_starvation_s": round(sum(starvation) / len(starvation), 2) if starvation else 0.0,
            "p95_starvation_s": round(_percentile(starvation, 0.95), 2),
            "max_starvation_s": round(max(starvation), 2) if starvation else 0.0,
            "censored_starvation_count": len(censored),
            "censored_starvation_s": round(sum(censored), 2),
            "breakdown_count": breakdown_count,
            "breakdown_time_s": round(breakdown_s, 2),
            "pm_count": pm_count,
            "pm_time_s": round(pm_s, 2),
            "total_downtime_s": round(downtime_s, 2),
        }

    return {
        "measurement_start_s": measurement_start_s,
        "measurement_end_s": measurement_end_s,
        **aggregate(instance.machines),
        "by_family": {
            family: aggregate(machines)
            for family, machines in sorted(by_family.items())
        },
    }
