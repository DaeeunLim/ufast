"""
FromTo file parser
Format: tab-separated text — FromEQ\tToEQ\tRate(count/hour)
The third column is the hourly occurrence rate (λ [count/hour, req/hr]).
In fixed-interval simulation the inter-event spacing is Δt = 3600.0 / rate (seconds).
"""
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple


def load_fromto(filepath: str) -> List[Tuple[str, str, float]]:
    """
    Read a FromTo file and return a list of (from_eq, to_eq, rate).
    rate is the hourly occurrence rate (count/hour, req/hr).
    """
    records: List[Tuple[str, str, float]] = []

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) < 3:
                continue
            from_eq = parts[0].strip()
            to_eq = parts[1].strip()
            try:
                rate = float(parts[2])
            except ValueError:
                continue
            records.append((from_eq, to_eq, rate))

    return records


def group_fromto_rates(
    fromto_data: Iterable[Tuple[str, str, float]],
) -> Dict[Tuple[str, str], List[float]]:
    """
    Group FromTo records into per-(from_eq, to_eq) lists of hourly rates.
    Returns: {(from_eq, to_eq): [rate_h0, rate_h1, rate_h2, ...]}
    """
    grouped: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for from_eq, to_eq, rate in fromto_data:
        grouped[(from_eq, to_eq)].append(float(rate))
    return dict(grouped)


def generate_fixed_interval_events(
    fromto_data: Iterable[Tuple[str, str, float]],
    sim_duration: float = 3600.0,
    offset_ratio: float = 0.5,
) -> List[Tuple[float, str, str]]:
    """
    Generate a fixed-interval (Δt = 3600/rate seconds) event list
    [(timestamp_sec, from_eq, to_eq), ...] from the hourly rate.

    - sim_duration: simulation duration (seconds)
    - offset_ratio: offset ratio of the first event (default 0.5: midpoint of the interval)
    """
    grouped = group_fromto_rates(fromto_data)
    events: List[Tuple[float, str, str]] = []

    for (from_eq, to_eq), rates in grouped.items():
        if not rates:
            continue
        r0 = rates[0]
        if r0 <= 0:
            t = 3600.0
        else:
            dt0 = 3600.0 / r0
            t = dt0 * offset_ratio

        while t < sim_duration:
            h = int(t // 3600)
            r = rates[h % len(rates)]
            if r <= 0:
                t = (h + 1) * 3600.0
                continue
            events.append((t, from_eq, to_eq))
            t += 3600.0 / r

    events.sort(key=lambda x: x[0])
    return events
