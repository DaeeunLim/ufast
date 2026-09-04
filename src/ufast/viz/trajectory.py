"""
viz/trajectory.py — OHT trajectory data structures + position interpolation + JSON save/load.

ufast/amhs.py accumulates trip data as dicts each time a trip completes; at the
end of the simulation they are bundled into a TrajectoryLog and saved to
results/<run_id>/*_trajectories.json.
viz/rerun_replay.py reads that file and interpolates OHT positions at time t.

Interpolation method (Phase 2 v1):
  - Linear interpolation along the path by cumulative-distance fraction.
  - Visual smoothness is favoured over kinematic accuracy (per-edge times).
  - Split per leg: empty leg (OHT -> pickup) -> loaded leg (pickup -> delivery).
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# OHT state labels (rerun colour keys)
STATE_IDLE    = 'IDLE'
STATE_EMPTY   = 'EMPTY'    # OHT heading to pickup (empty leg)
STATE_LOADED  = 'LOADED'   # OHT carrying a lot (loaded leg)
STATE_AT_DEST = 'AT_DEST'  # just delivered, waiting for the next job


@dataclass
class Trip:
    """Timing and path record of one OHT transport job."""
    oht_id: str
    request_time: float            # time the request was issued
    assignment_time: float         # time the OHT was assigned and departed
    delivery_time: float           # time the load was delivered
    empty_path: List[str]          # OHT current node -> pickup node (sequence of node names)
    loaded_path: List[str]         # pickup node -> delivery node
    empty_duration: float          # empty-leg duration (congestion applied)
    loaded_duration: float         # loaded-leg duration (congestion applied)
    congestion: float              # congestion factor applied

    @property
    def pickup_time(self) -> float:
        return self.assignment_time + self.empty_duration

    @property
    def total_duration(self) -> float:
        return self.delivery_time - self.assignment_time


@dataclass
class TrajectoryLog:
    """Complete trajectory log of one ufast run."""
    trips: List[Trip] = field(default_factory=list)
    oht_initial_positions: Dict[str, str] = field(default_factory=dict)
    # production Machine busy periods [{start, end, family}, ...] — for tool activity visualisation
    machine_activities: List[Dict[str, Any]] = field(default_factory=list)
    # family -> total machine count — used for the load-ratio (active/total) colour gradient
    family_sizes: Dict[str, int] = field(default_factory=dict)
    # F12 — KPI time-series snapshot tuple list:
    #   [(sim_time, busy_count, section_inflight_sum, pending_queue,
    #     delivered_count, max_section_inflight), ...]
    kpi_snapshots: List[List[Any]] = field(default_factory=list)
    # Time-series metadata — needed for normalisation
    oht_total: int = 0

    def save(self, path: str) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({
                'trips': [asdict(t) for t in self.trips],
                'oht_initial_positions': self.oht_initial_positions,
                'machine_activities': self.machine_activities,
                'family_sizes': self.family_sizes,
                'kpi_snapshots': self.kpi_snapshots,
                'oht_total': self.oht_total,
            }, f, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> 'TrajectoryLog':
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        trips = [Trip(**t) for t in data.get('trips', [])]
        return cls(
            trips=trips,
            oht_initial_positions=data.get('oht_initial_positions', {}),
            machine_activities=data.get('machine_activities', []),
            family_sizes=data.get('family_sizes', {}),
            kpi_snapshots=data.get('kpi_snapshots', []),
            oht_total=data.get('oht_total', 0),
        )

    @classmethod
    def from_amhs_log(cls, trip_dicts: List[Dict[str, Any]],
                      initial_positions: Dict[str, str],
                      machine_activities: Optional[List[Dict[str, Any]]] = None,
                      family_sizes: Optional[Dict[str, int]] = None,
                      kpi_snapshots: Optional[List[Any]] = None,
                      oht_total: int = 0,
                      ) -> 'TrajectoryLog':
        """Raw dict list accumulated by the AMHS executor -> TrajectoryLog."""
        trips = [Trip(**d) for d in trip_dicts]
        return cls(
            trips=trips,
            oht_initial_positions=initial_positions,
            machine_activities=list(machine_activities) if machine_activities else [],
            family_sizes=dict(family_sizes) if family_sizes else {},
            kpi_snapshots=[list(s) for s in (kpi_snapshots or [])],
            oht_total=oht_total,
        )


# ── Position interpolation ──────────────────────────────────

def _path_cumulative_distance(path: List[str],
                              nodes_xy: Dict[str, Tuple[float, float]]
                              ) -> Tuple[List[Tuple[float, float]], List[float]]:
    """Path node coordinates + cumulative distance array."""
    pts = [nodes_xy[n] for n in path if n in nodes_xy]
    cum = [0.0]
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        cum.append(cum[-1] + (dx * dx + dy * dy) ** 0.5)
    return pts, cum


def _interpolate_along_path(path: List[str],
                            leg_elapsed: float,
                            leg_duration: float,
                            nodes_xy: Dict[str, Tuple[float, float]]
                            ) -> Tuple[float, float]:
    """Linear position interpolation along the path by the leg_elapsed/leg_duration fraction (cumulative distance)."""
    pts, cum = _path_cumulative_distance(path, nodes_xy)
    if not pts:
        return (0.0, 0.0)
    if len(pts) == 1 or cum[-1] <= 0 or leg_duration <= 0:
        return pts[-1]

    frac = max(0.0, min(1.0, leg_elapsed / leg_duration))
    target = frac * cum[-1]

    # find the segment of cum that contains target
    for i in range(1, len(cum)):
        if cum[i] >= target:
            seg_len = cum[i] - cum[i - 1]
            seg_frac = (target - cum[i - 1]) / seg_len if seg_len > 0 else 0.0
            x = pts[i - 1][0] + seg_frac * (pts[i][0] - pts[i - 1][0])
            y = pts[i - 1][1] + seg_frac * (pts[i][1] - pts[i - 1][1])
            return (x, y)
    return pts[-1]


def interpolate_trip_position(trip: Trip, t: float,
                              nodes_xy: Dict[str, Tuple[float, float]]
                              ) -> Optional[Tuple[float, float, str]]:
    """
    OHT position and state of a Trip at time t.

    Returns:
        (x, y, state) — None if t < assignment.
        state ∈ {STATE_EMPTY, STATE_LOADED, STATE_AT_DEST}
    """
    if t < trip.assignment_time:
        return None
    if t >= trip.delivery_time:
        # Delivery complete — waiting at the last node (= end of loaded_path)
        if trip.loaded_path:
            last = trip.loaded_path[-1]
            if last in nodes_xy:
                return (nodes_xy[last][0], nodes_xy[last][1], STATE_AT_DEST)
        return None

    elapsed = t - trip.assignment_time
    if elapsed < trip.empty_duration:
        # empty leg
        x, y = _interpolate_along_path(trip.empty_path, elapsed,
                                       trip.empty_duration, nodes_xy)
        return (x, y, STATE_EMPTY)
    else:
        # loaded leg
        leg_elapsed = elapsed - trip.empty_duration
        x, y = _interpolate_along_path(trip.loaded_path, leg_elapsed,
                                       trip.loaded_duration, nodes_xy)
        return (x, y, STATE_LOADED)
