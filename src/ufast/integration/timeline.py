"""
TimelineRecorder
================
"Video record/replay" engine shared by the two simulators (AMHS / production).

Design concept
--------------
* While the simulation runs, a Snapshot is recorded at a fixed *simulation-time
  interval*. (It is a sim-time interval, not a wall-clock one, so it is constant
  regardless of the speed setting.)
* When the user drags the progress bar (slider), the snapshot closest to that
  sim-time is looked up and the screen is restored to that state (= replaying
  the past).
* A snapshot holds only "the minimum state needed to redraw the screen at that
  moment":
  - AMHS: per OHT (section_id, section entry time, status) + EQ status summary
  - production: aggregate metrics (throughput, WIP, utilisation) + machine status summary

Because snapshots are keyed by sim-time, if "1x = 1 sim second per real second"
holds, the timeline position maps exactly onto real elapsed time.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Snapshot:
    """State used to restore the screen at a single point in time."""
    sim_time: float                      # simulation time (s) this snapshot represents
    mode: str                            # "logistics" | "production"
    # AMHS: {oht_name: {"section_id": int, "enter_time": float, "status": str}}
    ohts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # AMHS: {eq_name: {"status": str, "lot_count": int}}
    eqs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Common metrics (both production and AMHS): for the stats panel at the top of the screen
    metrics: Dict[str, Any] = field(default_factory=dict)


class TimelineRecorder:
    """
    Keeps snapshots in ascending sim-time order and quickly looks up the
    nearest (<=) snapshot for an arbitrary time.
    """

    def __init__(self, snapshot_interval: float = 1.0):
        # snapshot_interval: recording interval in sim-time (s). Smaller = smoother but more memory
        self.snapshot_interval = max(1e-6, float(snapshot_interval))
        self._snaps: List[Snapshot] = []
        self._times: List[float] = []          # sim_time key array for bisect
        self._last_recorded_time: float = -1e18
        self.mode: str = "logistics"

    # ── Recording ───────────────────────────────────────────
    def reset(self, mode: str = "logistics", snapshot_interval: Optional[float] = None):
        self._snaps.clear()
        self._times.clear()
        self._last_recorded_time = -1e18
        self.mode = mode
        if snapshot_interval is not None:
            self.snapshot_interval = max(1e-6, float(snapshot_interval))

    def maybe_record(self, sim_time: float, builder) -> bool:
        """
        If sim_time has passed the last record + interval, call builder() to
        build and store a snapshot. builder is a zero-argument callable returning
        a Snapshot. Return value: whether a record was actually made.
        """
        if sim_time - self._last_recorded_time < self.snapshot_interval:
            return False
        snap = builder()
        if snap is None:
            return False
        self.record(snap)
        return True

    def record(self, snap: Snapshot):
        """Force-add an already built snapshot (to guarantee start/end points)."""
        # Assumes monotonically increasing sim_time. Equal/backward times replace the last entry.
        if self._times and snap.sim_time <= self._times[-1] + 1e-9:
            self._snaps[-1] = snap
            self._times[-1] = snap.sim_time
        else:
            self._snaps.append(snap)
            self._times.append(snap.sim_time)
        self._last_recorded_time = snap.sim_time

    # ── Lookup ──────────────────────────────────────────────
    @property
    def duration(self) -> float:
        return self._times[-1] if self._times else 0.0

    @property
    def start_time(self) -> float:
        return self._times[0] if self._times else 0.0

    def __len__(self) -> int:
        return len(self._snaps)

    @property
    def snapshots(self) -> List[Snapshot]:
        """List of all recorded snapshots (in time order). For time-series CSV/plots."""
        return list(self._snaps)

    def at(self, sim_time: float) -> Optional[Snapshot]:
        """Most recent snapshot at or before sim_time; the first snapshot if none."""
        if not self._snaps:
            return None
        idx = bisect.bisect_right(self._times, sim_time) - 1
        if idx < 0:
            idx = 0
        return self._snaps[idx]

    def at_fraction(self, frac: float) -> Optional[Snapshot]:
        """Snapshot at a fractional position in 0.0..1.0."""
        if not self._snaps:
            return None
        frac = max(0.0, min(1.0, frac))
        t = self.start_time + frac * (self.duration - self.start_time)
        return self.at(t)
