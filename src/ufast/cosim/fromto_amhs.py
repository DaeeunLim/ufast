"""
ufast/fromto_amhs.py — fromto.dat → AMHSExecutor 스케줄러.

VehicleController 없이 AMHSExecutor 만으로 fromto-driven 시뮬레이션을 실행한다.
생산 레이어가 없으므로 HeapInstance (경량 shim) 로 이벤트 큐를 구동.

공개 API:
    setup_fromto_amhs(rm, bridge, kin, fromto_data, num_oht, sim_duration, ...)
        → (amhs: AMHSExecutor, heap: HeapInstance)
"""
from __future__ import annotations
from typing import List, Tuple, Optional

from ufast.cosim.amhs import AMHSExecutor
from ufast.cosim.heap_instance import HeapInstance
from ufast.common.fromto_parser import generate_fixed_interval_events


class _FromtoRequestEvent:
    """HeapInstance에 등록되는 FromTo 운반 요청 이벤트."""
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
    """fromto 시뮬레이션을 위한 AMHSExecutor + HeapInstance 를 초기화한다.

    fromto_data: [(from_eq, to_eq, rate), ...] 형식 (rate: 건/시간).
    반환값으로 받은 heap 을 구동하면 시뮬레이션이 진행된다.

    실시간 구동: RealTimeSimDriver(amhs_mode=True) 에 (amhs, heap) 전달.
    헤드리스 구동: heap.pop_until(sim_duration) 을 while 루프로 반복.
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
    amhs.instance = heap   # HeapInstance 를 UFastInstance 대신 주입
    amhs.schedule_first_reposition(0.0)

    # ── fromto 이벤트 스케줄 ──────────────────────────────
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
    """fromto_data 의 각 레코드를 기반으로 고정 간격(3600/rate 초) 이벤트를 heap에 스케줄."""
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
