"""
ufast/machine_activity.py — plugin that records the busy periods of production Machines.

Uses the PySCFabSim IPlugin interface to receive the dispatch-start / machine-free
hooks and accumulates (start, end, family) tuples.
Stored alongside the trajectory log and used by rerun_replay to visualise
per-family activity.

Recorded activity:
  - From the moment a machine becomes busy via dispatch until it becomes free
    via free_up_machines.
  - Machines still busy when the simulation ends remain as unfinished activities
    (_active) — finalize() closes them with current_time as the end point.
"""
from __future__ import annotations
from typing import Any, Dict, List, Tuple

from ufast.production.plugins.interface import IPlugin


class MachineActivityPlugin(IPlugin):
    """Plugin accumulating machine busy periods as (start, end, family)."""

    def __init__(self):
        self.activities: List[Dict[str, Any]] = []
        # machine.idx → (start_time, family)
        self._active: Dict[int, Tuple[float, str]] = {}

    def on_dispatch(self, instance, machine, lots,
                    machine_end_time, lot_end_time):
        # If the same machine was never released (safety), close that record and start anew
        if machine.idx in self._active:
            start, family = self._active.pop(machine.idx)
            self.activities.append({
                'start': start,
                'end': instance.current_time,
                'family': family,
            })
        self._active[machine.idx] = (instance.current_time, machine.family)

    def on_machine_free(self, instance, machine):
        if machine.idx in self._active:
            start, family = self._active.pop(machine.idx)
            self.activities.append({
                'start': start,
                'end': instance.current_time,
                'family': family,
            })

    def on_sim_done(self, instance):
        # Close the activities of machines still busy at the end
        end = instance.current_time
        for idx, (start, family) in list(self._active.items()):
            self.activities.append({
                'start': start,
                'end': end,
                'family': family,
            })
        self._active.clear()
