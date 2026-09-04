"""
ufast/fromto_amhs.py — fromto.dat → AMHSExecutor scheduler.

Runs a fromto-driven simulation with AMHSExecutor alone, without VehicleController.
Since there is no production layer, HeapInstance (a lightweight shim) drives the
event queue.

Public API:
    setup_fromto_amhs(rm, bridge, kin, fromto_data, num_oht, sim_duration, ...)
        → (amhs: AMHSExecutor, heap: HeapInstance)
"""
from __future__ import annotations
from typing import List, Tuple, Optional

from ufast.cosim.amhs import AMHSExecutor
from ufast.cosim.heap_instance import HeapInstance
from ufast.common.fromto_parser import generate_fixed_interval_events


class _FromtoRequestEvent:
    """FromTo transport request event registered on the HeapInstance."""
    def __init__(self, timestamp: float, from_node: str, to_node: str, job_id: str, amhs: AMHSExecutor):
        self.timestamp = timestamp
        self.from_node = from_node
        self.to_node = to_node
        self.job_id = job_id
        self.amhs = amhs

    def handle(self, instance):
        self.amhs.request_transport(
            lot=self.job_id,
            from_node=self.from_node,
            to_node=self.to_node,
            request_time=self.timestamp,
            on_deliver=lambda deliver_t, dur=0.0: None,
        )


def setup_fromto_amhs(
    rm,
    bridge,
    kin,
    fromto_data: List[Tuple],
    num_oht: int,
    sim_duration: float,
    seed: int = 0,
    congestion_alpha: float = 0.05,
    amhs_strategy: str = 'nearest',
    congestion_model: str = 'queue',
    idle_positioning: str = 'off',
    routing_model: str = 'off',
    record_trajectory: bool = False,
    oht_footprint_mm: float = 909.0,
    line_speed_mm_s: float = 5000.0,
    curve_speed_mm_s: float = 1000.0,
) -> Tuple[AMHSExecutor, HeapInstance]:
    """Initialise an AMHSExecutor + HeapInstance for a fromto simulation.

    fromto_data: [(from_eq, to_eq, rate), ...] (rate: jobs per hour).
    Driving the returned heap advances the simulation.

    Real-time driving: pass (amhs, heap) to RealTimeSimDriver(amhs_mode=True).
    Headless driving: call heap.pop_until(sim_duration) in a while loop.
    """
    import random
    random.seed(seed)

    nodes = list(rm.network.nodes.keys())
    eq_to_node = rm.network.eq_to_node   # eq_name → node_id

    heap = HeapInstance()

    amhs = AMHSExecutor(
        rm, kin, num_oht, nodes,
        bridge=bridge,
        congestion_alpha=congestion_alpha,
        record_trajectory=record_trajectory,
        dispatch_strategy=amhs_strategy,
        congestion_model=congestion_model,
        idle_positioning=idle_positioning,
        routing_model=routing_model,
        oht_footprint_mm=oht_footprint_mm,
        line_speed_mm_s=line_speed_mm_s,
        curve_speed_mm_s=curve_speed_mm_s,
    )
    amhs.instance = heap   # inject HeapInstance in place of UFastInstance
    amhs.schedule_first_reposition(0.0)

    # ── Schedule fromto events ───────────────────────────
    _schedule_all(fromto_data, eq_to_node, amhs, heap, sim_duration, job_counter=[0])

    return amhs, heap


def _schedule_all(
    fromto_data: List[Tuple],
    eq_to_node: dict,
    amhs: AMHSExecutor,
    heap: HeapInstance,
    sim_duration: float,
    job_counter: list,
):
    """Schedule fixed-interval (3600/rate s) events on the heap for each fromto_data record."""
    valid_fromto = []
    for from_eq, to_eq, rate in fromto_data:
        from_node = eq_to_node.get(from_eq)
        to_node = eq_to_node.get(to_eq)
        if from_node is not None and to_node is not None and from_node != to_node:
            valid_fromto.append((from_eq, to_eq, float(rate)))

    events = generate_fixed_interval_events(valid_fromto, sim_duration)

    for sim_t, from_eq, to_eq in events:
        from_node = eq_to_node[from_eq]
        to_node = eq_to_node[to_eq]
        job_counter[0] += 1
        job_id = f"FT_{job_counter[0]:06d}"
        evt = _FromtoRequestEvent(sim_t, from_node, to_node, job_id, amhs)
        heap.add_event(evt)
