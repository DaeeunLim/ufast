"""
ufast/legacy_trajectory.py — trajectory recorder for fromto-driven simulation.

Observes the OHT state transitions (IDLE→ASSIGNED→LOADED→IDLE) of the legacy
controllers.py and converts them into the same TrajectoryLog (Trip sequence) as
production mode, so that rerun_replay can replay both modes identically.

Design:
  - recorder.observe(t) is called between event-loop iterations — detects each
    OHT's state transitions.
  - State machine:
      IDLE → ASSIGNED   : trip start (assignment_time, initial node of empty_path)
      section change    : append node to the path of the leg in progress
      ASSIGNED → LOADED : pickup (pickup_time = end of empty_duration)
      LOADED → IDLE/REPO: delivery complete → Trip finalised and appended to trips
  - Node position = bridge.section_exit_node[current_section_id] (else entry_node).
  - REPOSITIONING trips are ignored (not lot transports).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional


class LegacyTrajectoryRecorder:
    """Observes the OHT states of the controllers.py VehicleController by polling."""

    def __init__(self, vehicle_controller, bridge):
        self.vc = vehicle_controller
        self.bridge = bridge

        # previously observed state (oht_id → (status, current_section_id, destination_eq, loaded_lot_info))
        self._prev: Dict[str, tuple] = {}
        # trips in progress (oht_id → partial dict)
        self._active_trip: Dict[str, Dict[str, Any]] = {}
        # completed trips
        self.trips: List[Dict[str, Any]] = []
        # initial OHT positions (oht_id → node)
        self.initial_positions: Dict[str, str] = {}

    # ── Helpers ─────────────────────────────────────────
    def _oht_node(self, oht) -> Optional[str]:
        """Representative node of an OHT — section exit_node (fallback: entry_node)."""
        sec_id = oht.current_section_id
        if sec_id is None or sec_id < 0:
            return None
        n = self.bridge.section_exit_node.get(sec_id)
        if not n:
            n = self.bridge.section_entry_node.get(sec_id)
        return n

    def _key(self, oht) -> tuple:
        return (oht.status, oht.current_section_id,
                oht.destination_eq, oht.loaded_lot_info)

    # ── Initial snapshot ────────────────────────────────
    def snapshot_initial(self):
        """Call right after vc.init() — records the initial OHT node positions and states."""
        for name, oht in self.vc.oht_list.items():
            node = self._oht_node(oht)
            self.initial_positions[name] = node or ""
            self._prev[name] = self._key(oht)

    # ── Observation after each event ────────────────────
    def observe(self, t: float):
        """Detect OHT state transitions after event processing and update the trip data."""
        for name, oht in self.vc.oht_list.items():
            prev = self._prev.get(name)
            curr = self._key(oht)
            if prev == curr:
                continue

            prev_status, prev_sec, _, _ = prev or (None, None, None, None)
            curr_status, curr_sec, _, _ = curr

            # ── 1) (IDLE / REPOSITIONING) → ASSIGNED : trip start ──
            # REPOSITIONING→ASSIGNED is the interrupt_reposition path of assign_oht.
            if prev_status in ("IDLE", "REPOSITIONING") and curr_status == "ASSIGNED":
                start_node = self._oht_node(oht)
                self._active_trip[name] = {
                    'oht_id': name,
                    'request_time': t,
                    'assignment_time': t,
                    'empty_path': [start_node] if start_node else [],
                    'loaded_path': [],
                    'pickup_time': None,
                }

            # ── 2) ASSIGNED → LOADED : pickup complete ──
            elif prev_status == "ASSIGNED" and curr_status == "LOADED":
                trip = self._active_trip.get(name)
                if trip is not None:
                    trip['pickup_time'] = t
                    trip['empty_duration'] = t - trip['assignment_time']
                    pickup_node = self._oht_node(oht)
                    if pickup_node:
                        # ensure the pickup point is the last entry of empty_path
                        if not trip['empty_path'] or trip['empty_path'][-1] != pickup_node:
                            trip['empty_path'].append(pickup_node)
                        trip['loaded_path'] = [pickup_node]

            # ── 3) LOADED → IDLE/REPOSITIONING : delivery complete ──
            elif prev_status == "LOADED" and curr_status in ("IDLE", "REPOSITIONING"):
                trip = self._active_trip.pop(name, None)
                if trip and trip.get('pickup_time') is not None:
                    delivery_node = self._oht_node(oht)
                    if delivery_node and (not trip['loaded_path']
                                          or trip['loaded_path'][-1] != delivery_node):
                        trip['loaded_path'].append(delivery_node)
                    trip['delivery_time'] = t
                    trip['loaded_duration'] = t - trip['pickup_time']
                    trip['congestion'] = 1.0  # legacy has no separate congestion measurement
                    # keep only the fields of the Trip dataclass
                    self.trips.append({
                        'oht_id': trip['oht_id'],
                        'request_time': trip['request_time'],
                        'assignment_time': trip['assignment_time'],
                        'delivery_time': trip['delivery_time'],
                        'empty_path': trip['empty_path'],
                        'loaded_path': trip['loaded_path'],
                        'empty_duration': trip['empty_duration'],
                        'loaded_duration': trip['loaded_duration'],
                        'congestion': 1.0,
                    })

            # ── 4) section change (append node to the leg in progress) ──
            elif prev_sec != curr_sec and curr_sec is not None and curr_sec >= 0:
                trip = self._active_trip.get(name)
                if trip is not None:
                    node = self._oht_node(oht)
                    if node:
                        if curr_status == "ASSIGNED":
                            if not trip['empty_path'] or trip['empty_path'][-1] != node:
                                trip['empty_path'].append(node)
                        elif curr_status == "LOADED":
                            if not trip['loaded_path'] or trip['loaded_path'][-1] != node:
                                trip['loaded_path'].append(node)

            # ── 5) ASSIGNED → IDLE (pickup failed) — discard the trip in progress ──
            elif prev_status == "ASSIGNED" and curr_status == "IDLE":
                self._active_trip.pop(name, None)

            self._prev[name] = curr

    # ── Build TrajectoryLog ─────────────────────────────
    def build_log_data(self) -> Dict[str, Any]:
        """Dict for saving (same key structure as TrajectoryLog.save)."""
        return {
            'trips': self.trips,
            'oht_initial_positions': self.initial_positions,
            # fromto mode has no production machine activity — empty list/dict
            'machine_activities': [],
            'family_sizes': {},
        }
