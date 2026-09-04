"""
amhs.py — co-simulation 물류(AMHS) 실행기 (section-aware, transition 이벤트).

생산 레이어가 발주한 이송 작업(TransportJob)을 받아 OHT를 배차하고,
이송 완료 시 생산 레이어에 콜백으로 알린다.

U-FAST novelty:
  - **Section 단위 transition 이벤트** — OHT 가 section A → B 로 넘어가는
    매 transition 마다 SectionEnterEvent 가 시간축에 명시적으로 일어난다.
    section 내부의 노드 단위 step 이동은 시뮬레이션하지 않고, section 통과
    시간만 free-flow (가감속 운동학) 로 계산.
  - **밀림 현상 (congestion propagation)** — 각 section 의 통과 시간은
    *그 section 에 진입하는 시점* 의 section_inflight 점유로 결정된다.
    발주 시점의 정적 스냅샷이 아니라 동적 상태. 다른 OHT 가 막 진입해
    점유를 +1 시켰다면 그 직후 진입하는 OHT 는 더 큰 inflight 를 보고
    더 느리게 통과한다 — 시간 도메인에서 밀림이 자연 발생.

배차:
  - route.Dispatcher 위임 (NearestIdle / SameSectionFirst / CongestionAware).
  - 'fifo' 는 dispatcher 우회 (가장 빠른 단순 baseline).

이송시간 모델:
  - 각 section 의 base 통과 시간: 그 section 을 차지하는 node_path 구간의
    가감속 운동학 free-flow 합.
  - 실 통과 시간 = base * (1 + alpha * 진입 시점 section_inflight[sec]).

이벤트 큐 흐름 (한 transport 당):
  발주 t₀
  → SectionEnterEvent(S₁) at t₁ = t₀ + travel(S₀ → S₁)
  → SectionEnterEvent(S₂) at t₂ = t₁ + travel(S₁ → S₂, inflight at t₁)
  → ...
  → SectionEnterEvent(pickup_section) at t_p  — status: ASSIGNED → LOADED
  → ...
  → ArriveEvent(dest_section) at t_d  — on_deliver 콜백, OHT idle
"""
from __future__ import annotations
from collections import deque, defaultdict
from typing import Callable, Deque, Dict, List, Optional, Set, Tuple

from ufast.route import (
    Dispatcher, SectionNodeBridge,
    NearestIdleStrategy, SameSectionFirstStrategy, CongestionAwareStrategy,
)
from ufast.common.strategy_loader import (
    invoke_assignment_strategy,
    invoke_idle_positioning_strategy,
    invoke_node_routing_strategy,
    invoke_routing_strategy,
    normalize_node_path,
    normalize_oht_selection,
    normalize_section_path,
)


# legacy 와 동일한 strategy 이름 + 'fifo' (dispatcher 우회).
_STRATEGY_FACTORIES = {
    'nearest':      NearestIdleStrategy,
    'same_section': SameSectionFirstStrategy,
    'congestion':   CongestionAwareStrategy,
}


class OHT:
    """이송 차량 — section 단위 위치/상태 (Dispatcher 의 OHTInfo Protocol 충족)."""

    def __init__(self, name: str, node: str, section_id: int):
        self.name = name
        self.current_node = node
        self.current_section_id = section_id
        self.time_enter_current_section: float = 0.0  # viewer 위치 보간용
        self.status = "IDLE"     # IDLE / ASSIGNED / LOADED — Dispatcher 와 호환
        self.busy = False        # 내부 호환 플래그

    def __repr__(self):
        return f"OHT({self.name}@sec{self.current_section_id}/{self.current_node})"


class TransportJob:
    """
    이송 작업 — production layer 는 node 인자만 알고, 내부에서 section sequence 로 확장.

    transition 이벤트가 진행됨에 따라 cursor (section_idx) 가 전진한다.
    """

    def __init__(self, lot, from_node: str, to_node: str,
                 request_time: float, on_deliver: Callable):
        self.lot = lot
        self.from_node = from_node
        self.to_node = to_node
        self.request_time = request_time
        self.on_deliver = on_deliver

        # 경로 (assign 시 채움)
        self.section_path: List[int] = []          # 전 경로 section 시퀀스 (출발 sec 포함)
        self.section_durations: List[float] = []   # 각 section 의 base free-flow 통과 시간 (sec[i] 통과)
        self.pickup_idx: int = -1                  # section_path 에서 pickup 이 일어나는 idx
        self.node_path: List[str] = []             # debug/trajectory 용

        # 진행 상태
        self.current_idx: int = 0                  # 현재 점유 section 의 index (= section_path[current_idx])
        self.assignment_time: float = 0.0          # _assign 시점

        # 통계용
        self.dur = 0.0                  # 실제 총 이송 소요 (배달 시 산출)
        self.free_flow = 0.0            # sum(section_durations)
        self.empty_t = 0.0               # pickup 까지 free-flow
        self.loaded_t = 0.0              # pickup 이후 free-flow
        self.empty_actual = 0.0          # pickup 까지 실제 (혼잡 반영)
        self.loaded_actual = 0.0         # pickup 이후 실제 (혼잡 반영)
        self.wait = 0.0
        self.count_for_stats = True
        # trajectory 호환 — atomic 시절 분리되어 있던 두 leg 의 node_path
        self.empty_path: List[str] = []
        self.loaded_path: List[str] = []


class SectionEnterEvent:
    """OHT 가 section_path[next_idx] 에 진입하는 시점의 이벤트."""

    __slots__ = ('timestamp', 'executor', 'oht', 'job', 'next_idx',
                 'machines', 'lots')

    def __init__(self, timestamp: float, executor: "AMHSExecutor",
                 oht: OHT, job: TransportJob, next_idx: int):
        self.timestamp = timestamp
        self.executor = executor
        self.oht = oht
        self.job = job
        self.next_idx = next_idx
        # PySCFabSim 이벤트 인터페이스 호환
        self.machines: List = []
        self.lots: List = []

    def handle(self, instance):
        self.executor.on_section_enter(instance, self.oht, self.job,
                                       self.next_idx, self.timestamp)


class ArriveEvent:
    """OHT 가 목적 section 에 도착해 배달 완료하는 시점의 이벤트."""

    __slots__ = ('timestamp', 'executor', 'oht', 'job',
                 'machines', 'lots')

    def __init__(self, timestamp: float, executor: "AMHSExecutor",
                 oht: OHT, job: TransportJob):
        self.timestamp = timestamp
        self.executor = executor
        self.oht = oht
        self.job = job
        self.machines: List = []
        self.lots: List = []

    def handle(self, instance):
        self.executor.on_arrive(instance, self.oht, self.job, self.timestamp)


class RepositionTickEvent:
    """주기적 idle reposition trigger — 일정 sim 간격으로 발사."""

    __slots__ = ('timestamp', 'executor', 'machines', 'lots')

    def __init__(self, timestamp: float, executor: "AMHSExecutor"):
        self.timestamp = timestamp
        self.executor = executor
        self.machines: List = []
        self.lots: List = []

    def handle(self, instance):
        self.executor.tick_reposition(self.timestamp)


class DeadlockCheckEvent:
    """'queue' 모델 전용 — 차단된 OHT 가 있는 동안 주기적으로 데드락 검사."""

    __slots__ = ('timestamp', 'executor', 'machines', 'lots')

    def __init__(self, timestamp: float, executor: "AMHSExecutor"):
        self.timestamp = timestamp
        self.executor = executor
        self.machines: List = []
        self.lots: List = []

    def handle(self, instance):
        self.executor.check_deadlocks(self.timestamp)


class AMHSExecutor:
    """유한 OHT 풀 + section transition 이벤트 + 동적 밀림 모델."""

    DISPATCH_STRATEGIES = ('fifo', 'nearest', 'same_section', 'congestion')

    # 혼잡 모델 — 각 section transition 시점에 적용할 통과시간 보정.
    #   'off'           : 보정 없음 (c=1, free-flow 만).
    #   'global_tip'    : LogiFabSim 형태 — c = 1 + α * (현재 in-flight OHT 수 / num_oht).
    #                     전역 TIP 비례. layout 무시.
    #   'section_local' : U-FAST 고유 — c = 1 + α * section_inflight[sec].
    #                     국지·동적 (transition 시점 점유).
    #   'queue'         : 용량 제약 blocking 모델 (GUI 물류 모델의 headless 이식).
    #                     통과시간은 free-flow (c=1) 이지만 section 용량
    #                     (= ⌊section 길이 / OHT footprint⌋) 초과 시 진입이
    #                     *차단*되어 FIFO 로 대기 → 자리가 나면 순서대로 진입.
    #                     순환 대기(데드락)는 주기 검사로 탐지 후 강제 진입으로
    #                     해소(deadlock_forced 집계). 혼잡은 delay 가 아니라
    #                     back-pressure queueing 으로 창발한다. 이벤트 수가 많아
    #                     full-fab 장기 실험에서는 느리다.
    #                     주의: GUI 모델과 달리 IDLE OHT 는 점유에 포함하지 않는다
    #                     (delay 모델과 동일한 관례 — 이송 시작 시 +1).
    CONGESTION_MODELS = ('off', 'global_tip', 'section_local', 'queue')

    # Routing 모델 — 경로 탐색 시 혼잡을 *cost* 로 반영할지.
    #   'off'    : 정적. 한 번 계산한 (a,b)→node_path 를 _route_cache 에 보관, 재사용.
    #   'dynamic': bridge.on_oht_enter_section/leave_section 을 호출해 노드별
    #              traffic_penalty 를 OHT 점유 비례로 갱신. 매 transport 시
    #              _route_cache 와 bridge.route_cost_cache 를 무효화 → pathfinder
    #              가 혼잡 우회 경로 발견. 비용 폭증 (production scale 에서 권장 안 함).
    ROUTING_MODELS = ('off', 'dynamic')

    def __init__(self, route_manager, kinematics, num_oht: int,
                 oht_start_nodes: List[str],
                 bridge: SectionNodeBridge,
                 congestion_alpha: float = 0.05,
                 record_trajectory: bool = False,
                 dispatch_strategy: str = 'fifo',
                 congestion_model: str = 'queue',
                 idle_positioning: str = 'off',
                 reposition_interval_s: float = 30.0,
                 reposition_per_tick: int = 5,
                 routing_model: str = 'off',
                 oht_footprint_mm: float = 909.0,
                 line_speed_mm_s: float = 5000.0,
                 curve_speed_mm_s: float = 1000.0):
        self.rm = route_manager
        self.bridge = bridge
        self.kin = kinematics
        self.congestion_alpha = congestion_alpha
        self.record_trajectory = record_trajectory
        if dispatch_strategy not in self.DISPATCH_STRATEGIES:
            raise ValueError(
                f"unknown dispatch_strategy: {dispatch_strategy!r} "
                f"(expected one of {self.DISPATCH_STRATEGIES})")
        self.dispatch_strategy = dispatch_strategy
        if congestion_model not in self.CONGESTION_MODELS:
            raise ValueError(
                f"unknown congestion_model: {congestion_model!r} "
                f"(expected one of {self.CONGESTION_MODELS})")
        self.congestion_model = congestion_model
        self._num_oht = num_oht
        # global_tip 분모 — busy 상태인 OHT 수 (in-flight 카운터)
        self._busy_count = 0
        if routing_model not in self.ROUTING_MODELS:
            raise ValueError(
                f"unknown routing_model: {routing_model!r} "
                f"(expected one of {self.ROUTING_MODELS})")
        self.routing_model = routing_model

        # ── Idle vehicle positioning (F10) ──
        # 'off'         : 비활성 — IDLE OHT 는 도착 section 에 머묾.
        # 'spread'      : 인접 section 중 inflight 더 적은 곳으로 1-hop 이동.
        #                 fromto 모드 legacy 의 reposition_idle_ohts 단순화 버전.
        self.idle_positioning = idle_positioning
        self.reposition_interval_s = reposition_interval_s
        self.reposition_per_tick = reposition_per_tick
        # OHT 별 최근 reposition 한 section (ping-pong 방지 — 직전 3개)
        self._recent_repo_sec: Dict[str, Deque[int]] = defaultdict(
            lambda: deque(maxlen=3))
        # section_id → 인접 section_id list. _build_adjacency 가 채움.
        self.adjacency: Dict[int, List[int]] = {}

        # fifo 외에는 route.Dispatcher 위임
        if dispatch_strategy == 'fifo':
            self.dispatcher: Optional[Dispatcher] = None
        else:
            strat = _STRATEGY_FACTORIES[dispatch_strategy]()
            self.dispatcher = Dispatcher(route_manager, bridge, strat)

        # 엣지 색인: (from, to) → (거리, 속도제한 mm/s). 가감속 운동학 계산용.
        # 제한속도는 VehicleSpec(line/curve_speed_mm_s) 에서 온다.
        self.line_speed_mm_s = line_speed_mm_s
        self.curve_speed_mm_s = curve_speed_mm_s
        self._edge: Dict[Tuple[str, str], Tuple[float, float]] = {
            (l.from_node, l.to_node):
                (l.distance, line_speed_mm_s if l.link_type == 'LINE'
                 else curve_speed_mm_s)
            for l in route_manager.network.links
        }

        # ── 'queue' 모델 (용량 제약 blocking) 상태 ──
        # section_capacity : sec → 동시 수용 가능 OHT 수
        #                    (= max(1, ⌊section 길이 합 / footprint⌋)).
        # _waiting         : sec → 진입 대기 FIFO (oht, job, next_idx).
        # _blocked_since   : oht.name → (target_sec, 대기 시작 시각).
        # _section_vehicles: sec → 현재 in-flight 로 점유 중인 OHT 이름 집합
        #                    (데드락 wait-for 그래프 구축용, queue 모드에서만 유지).
        self.oht_footprint_mm = oht_footprint_mm
        self.section_capacity: Dict[int, int] = {}
        self._waiting: Dict[int, Deque[Tuple[OHT, "TransportJob", int]]] = \
            defaultdict(deque)
        self._blocked_since: Dict[str, Tuple[int, float]] = {}
        self._section_vehicles: Dict[int, Set[str]] = defaultdict(set)
        self._deadlock_check_pending = False
        self.total_blocked_events = 0
        self.total_blocked_time = 0.0
        self.deadlock_forced = 0
        # 섹션별 차단 통계 — blocking heatmap 용 (queue 모드에서만 채워짐)
        self.blocked_by_section: Dict[int, int] = {}
        self.blocked_time_by_section: Dict[int, float] = {}
        if congestion_model == 'queue':
            for sec_id, sec_nodes in bridge.section_to_nodes.items():
                length = 0.0
                for i in range(len(sec_nodes) - 1):
                    e = self._edge.get((sec_nodes[i], sec_nodes[i + 1]))
                    if e is not None:
                        length += e[0]
                self.section_capacity[sec_id] = max(
                    1, int(length // oht_footprint_mm))

        # OHT 초기화 — 시작 노드의 section_id 도 계산해서 보관.
        self.ohts: List[OHT] = []
        for i in range(num_oht):
            node = oht_start_nodes[i % len(oht_start_nodes)]
            sec = bridge.get_section_for_node(node)
            if sec is None:
                sec = next(iter(bridge.section_to_nodes), -1)
            self.ohts.append(OHT(f"OHT_{i:04d}", node, sec))

        # 궤적 기록
        self.initial_positions: Dict[str, str] = {o.name: o.current_node for o in self.ohts}
        self.trip_log: List[Dict] = []

        self._oht_by_name: Dict[str, OHT] = {o.name: o for o in self.ohts}
        self.idle: List[OHT] = list(self.ohts)
        self.pending: Deque[TransportJob] = deque()
        self.instance = None  # UFastInstance 가 주입

        # 경로 캐시: (a_node, b_node) → (node_path, section_path, section_durations)
        # section_durations[i] = section_path[i] 가 차지하는 node_path 구간의 free-flow 시간.
        self._route_cache: Dict[
            Tuple[str, str],
            Tuple[List[str], List[int], List[float]]
        ] = {}

        # section 별 in-flight OHT 수 (이송 경로 진행 중 점유). 동적 — 매 transition 마다 갱신.
        self.section_inflight: Dict[int, int] = {}

        # 통계
        self.measurement_start_time = 0.0
        self.total_requested_jobs = 0
        self.total_jobs = 0
        self.total_transport_time = 0.0
        self.total_free_flow = 0.0
        self.total_empty_time = 0.0
        self.total_loaded_time = 0.0
        self.total_wait_for_oht = 0.0
        # 측정창 job 별 배달시간(request→delivery)·이송시간 샘플 — 꼬리(p95/p99) 계산용
        self.delivery_samples: List[float] = []
        self.transport_samples: List[float] = []
        self.total_transitions = 0
        self.total_repositions = 0       # F10 — 발생한 reposition 이동 횟수
        self.max_queue = 0
        self.max_node_inflight = 0   # 이름은 외부 호환 (실은 max section_inflight)
        self.total_busy_oht_time = 0.0
        self.last_event_time = 0.0

        # F12 — KPI 시계열 (record_trajectory=True 일 때만)
        # 누적 transport 완료 수 (배달된 lot transport)
        self._delivered_count = 0
        # snapshot list: (sim_time, busy_count, sum_section_inflight,
        #                  pending_queue, delivered, max_section_inflight)
        self.kpi_snapshots: List[Tuple[float, int, int, int, int, int]] = []

        # F17 — Custom strategy plugin hooks. None 이면 빌트인 사용.
        #   custom_assignment.select(target_sec, idle_ohts, rm, bridge) -> oht_name|None
        #   custom_idle.plan_reposition(oht, t, rm, bridge) -> next_section_id|None
        #   custom_routing.get_route(a, b, rm, bridge) -> List[node]
        self.custom_assignment = None
        self.custom_idle = None
        self.custom_routing = None

        # 인접 그래프 + 첫 reposition tick 등록 — instance 주입 후 첫 schedule 가능.
        # (instance 가 아직 None — request_transport 가 처음 호출되기 전까지는 지연.)
        if self.idle_positioning != 'off':
            self._build_adjacency()

    def _update_busy_time(self, now: float):
        dt = now - self.last_event_time
        if dt > 0:
            if now >= self.measurement_start_time:
                active_dt = dt
                if self.last_event_time < self.measurement_start_time:
                    active_dt = now - self.measurement_start_time
                self.total_busy_oht_time += self._busy_count * active_dt
        self.last_event_time = now

    # ── 경로/시간 ────────────────────────────────────────
    def _kinematic_time(self, path: List[str]) -> float:
        edges: List[Tuple[float, float]] = []
        for i in range(len(path) - 1):
            e = self._edge.get((path[i], path[i + 1]))
            if e is not None:
                edges.append(e)
        return self.kin.path_time(edges)

    def _split_node_path_by_section(
        self, node_path: List[str],
    ) -> Tuple[List[int], List[float]]:
        """
        node_path 를 section 경계로 잘라 (section_path, section_durations) 반환.

        - section_path[i] : i 번째로 진입한 section id (출발 section 포함)
        - section_durations[i] : OHT 가 그 section 안에 머무는 동안 (다음 section
          진입 직전까지) 의 node-edge free-flow 시간 합. 마지막 항목은 마지막
          section 내부에서 도착 노드까지의 시간.
        """
        if not node_path:
            return [], []

        get_sec = self.bridge.get_section_for_node
        node_secs: List[Optional[int]] = [get_sec(n) for n in node_path]

        # 첫 transition 시점을 찾으면서 시간 누적.
        section_path: List[int] = []
        section_durations: List[float] = []
        cur_sec: Optional[int] = node_secs[0] if node_secs else None
        accum_time = 0.0

        if cur_sec is not None:
            section_path.append(cur_sec)

        # 노드 사이 엣지 단위로 진행
        for i in range(len(node_path) - 1):
            a, b = node_path[i], node_path[i + 1]
            e = self._edge.get((a, b))
            t = self.kin.path_time([e]) if e is not None else 0.0
            next_sec = node_secs[i + 1]

            # next_sec 가 cur_sec 과 같으면 같은 section 내부 이동 — 그냥 누적
            if next_sec == cur_sec or next_sec is None:
                accum_time += t
                continue

            # section 경계 — 이 edge 의 시간을 이전 section 의 통과 시간에 포함시키고
            # next_sec 로 전환. (b 노드가 이미 next_sec 에 속하므로 이 edge 는
            # "cur_sec exit 직전" 의 마지막 이동으로 본다.)
            accum_time += t
            section_durations.append(accum_time)
            section_path.append(next_sec)
            cur_sec = next_sec
            accum_time = 0.0

        section_durations.append(accum_time)
        return section_path, section_durations

    def _route(self, a: str, b: str) -> Tuple[List[str], List[int], List[float]]:
        """(node_path, section_path, section_durations). 캐시됨."""
        if a == b:
            return [], [], []
        key = (a, b)
        # F17 — custom routing 이 있으면 캐시 우회 (혼잡 변동 매번 반영 가능)
        if self.custom_routing is not None:
            try:
                result = invoke_node_routing_strategy(
                    self.custom_routing, a, b, self.rm, self.bridge)
                node_path = normalize_node_path(result, self.rm)

                if not node_path:
                    from_sec = self.bridge.get_section_for_node(a)
                    to_sec = self.bridge.get_section_for_node(b)
                    if from_sec is not None and to_sec is not None:
                        result = invoke_routing_strategy(
                            self.custom_routing, from_sec, to_sec, None)
                        sec_path = normalize_section_path(result, from_sec)
                        if sec_path:
                            node_path = self.bridge.section_route_to_node_route(
                                sec_path, from_sec_id=from_sec)
                            if node_path and node_path[0] != a:
                                node_path.insert(0, a)
                            if node_path and node_path[-1] != b:
                                node_path.append(b)
            except Exception as e:
                print(f"[amhs] ⚠️  custom_routing 오류 → 기본 라우팅 폴백: {e}")
                node_path = None
            if node_path:
                sec_path, durs = self._split_node_path_by_section(node_path)
                return (node_path, sec_path, durs)
        r = self._route_cache.get(key)
        if r is not None:
            return r

        from_sec = self.bridge.get_section_for_node(a)
        to_sec = self.bridge.get_section_for_node(b)

        if from_sec is not None and to_sec is not None and from_sec != to_sec:
            # 섹션 단위 라우팅 — U-FAST novelty.
            _sec_path, _cost, node_path = self.bridge.estimate_section_route_cost(
                from_sec, to_sec, context="AMHS_ROUTE",
            )
        else:
            node_path, _ = self.rm.pathfinder.path_search(a, b)

        if not node_path or len(node_path) < 2:
            r = ([], [], [])
        else:
            sec_path, durations = self._split_node_path_by_section(node_path)
            r = (node_path, sec_path, durations)

        self._route_cache[key] = r
        return r

    # ── 배차 ─────────────────────────────────────────────
    def _pop_idle(self, job: TransportJob) -> Optional[OHT]:
        """전략에 따라 idle pool 에서 OHT 한 대를 꺼낸다 (없으면 None)."""
        if not self.idle:
            return None
        target_sec = self.bridge.get_section_for_node(job.from_node)
        # F17 — custom assignment 가 있으면 우선 사용
        if self.custom_assignment is not None and target_sec is not None:
            idle_dict = {o.name: o for o in self.idle}
            try:
                selected = invoke_assignment_strategy(
                    self.custom_assignment, target_sec, list(self.idle),
                    self.rm, self.bridge, None)
                name = normalize_oht_selection(selected, idle_dict)
            except Exception as e:
                print(f"[amhs] ⚠️  custom_assignment 오류 → fifo 폴백: {e}")
                name = None
            if name is not None:
                oht = idle_dict.get(name)
                if oht is not None:
                    try:
                        self.idle.remove(oht)
                        return oht
                    except ValueError:
                        pass
            # 폴백
        if self.dispatch_strategy == 'fifo':
            return self.idle.pop(0)

        if target_sec is None:
            return self.idle.pop(0)
        idle_dict = {o.name: o for o in self.idle}
        name = self.dispatcher.dispatch(target_sec, idle_dict)
        if name is None:
            return None
        oht = self._oht_by_name.get(name)
        if oht is None:
            return None
        try:
            self.idle.remove(oht)
        except ValueError:
            return None
        return oht

    def request_transport(self, lot, from_node: str, to_node: str,
                          request_time: float, on_deliver: Callable):
        """생산 레이어가 이송 작업을 발주한다."""
        job = TransportJob(lot, from_node, to_node, request_time, on_deliver)
        self.total_requested_jobs += 1
        job.count_for_stats = request_time >= self.measurement_start_time
        if self.idle:
            oht = self._pop_idle(job)
            if oht is not None:
                self._assign(oht, job, request_time)
                return
        self.pending.append(job)
        if job.count_for_stats:
            self.max_queue = max(self.max_queue, len(self.pending))

    # ── 배차 후 첫 transition 예약 ───────────────────────
    def _assign(self, oht: OHT, job: TransportJob, now: float):
        """
        OHT 에 job 할당. 빈 leg + 적재 leg 의 section path 를 이어붙여 첫
        transition 이벤트를 예약. 이후 transition 은 on_section_enter 가 chain.
        """
        self._update_busy_time(now)
        oht.busy = True
        oht.status = "ASSIGNED"
        self._busy_count += 1
        job.assignment_time = now
        job.wait = now - job.request_time

        # 빈 leg(OHT→픽업) + 적재 leg(픽업→목적)
        empty_nodes, empty_secs, empty_durs = self._route(oht.current_node, job.from_node)
        loaded_nodes, loaded_secs, loaded_durs = self._route(job.from_node, job.to_node)

        # 두 leg 를 이어붙이되 pickup section 중복 제거.
        # empty leg 가 비어있는 경우 (OHT 가 이미 pickup 위치): pickup section = OHT current.
        if empty_secs and loaded_secs and empty_secs[-1] == loaded_secs[0]:
            # 같은 section — 그 section 의 통과시간은 empty + loaded 합
            merged_secs = empty_secs + loaded_secs[1:]
            merged_durs = empty_durs[:-1] + [empty_durs[-1] + loaded_durs[0]] + loaded_durs[1:]
            pickup_idx = len(empty_secs) - 1
        elif not empty_secs and loaded_secs:
            merged_secs = list(loaded_secs)
            merged_durs = list(loaded_durs)
            pickup_idx = 0
        elif empty_secs and not loaded_secs:
            merged_secs = list(empty_secs)
            merged_durs = list(empty_durs)
            pickup_idx = len(empty_secs) - 1
        else:
            # 둘 다 비어있음 — 즉시 배달
            merged_secs = [oht.current_section_id]
            merged_durs = [0.0]
            pickup_idx = 0

        job.section_path = merged_secs
        job.section_durations = merged_durs
        job.pickup_idx = pickup_idx
        job.node_path = empty_nodes + (loaded_nodes[1:] if loaded_nodes else [])
        job.empty_path = empty_nodes
        job.loaded_path = loaded_nodes
        # leg 별 free-flow 시간 — merged_durs 를 pickup_idx 기준으로 분할.
        # (이전 버전은 empty_durs+loaded_durs 를 더해 pickup section 통과시간을
        # 이중 카운트하던 버그가 있었다.)
        job.empty_t = sum(merged_durs[: pickup_idx + 1])
        job.loaded_t = sum(merged_durs[pickup_idx + 1:])
        job.free_flow = sum(merged_durs)

        # OHT 는 이미 section_path[0] 에 있다고 본다 (idle 시점에 그 section 에 머묾).
        # idle OHT 가 section 점유 카운터에 포함되지 않게 일관성 유지 — 이송이
        # *시작* 될 때 비로소 +1. 즉 section_path[0] (출발 section) 도 +1.
        first_sec = merged_secs[0]
        self._add_inflight(first_sec, oht_name=oht.name)
        job.current_idx = 0
        # pickup_idx == 0 (OHT 가 이미 pickup section) → 진입 동시에 LOADED.
        if pickup_idx == 0:
            oht.status = "LOADED"

        # 출발 section 의 통과시간 (그 section 안에서 다음 section 경계까지)
        first_dur = merged_durs[0] * self._congestion_multiplier(first_sec)
        # 첫 section 통과 시간도 leg 별 actual 통계에 누적해야 한다.
        if 0 <= pickup_idx:
            job.empty_actual += first_dur
        else:
            job.loaded_actual += first_dur
        next_t = now + first_dur

        # 첫 transition 예약 — section_path 가 1개뿐이면 바로 도착
        if len(merged_secs) == 1:
            self.instance.add_event(ArriveEvent(next_t, self, oht, job))
        else:
            self.instance.add_event(
                SectionEnterEvent(next_t, self, oht, job, next_idx=1))

    # ── transition 처리 ──────────────────────────────────
    def _add_inflight(self, sec: int, oht_name: Optional[str] = None):
        c = self.section_inflight.get(sec, 0) + 1
        self.section_inflight[sec] = c
        if self.congestion_model == 'queue' and oht_name is not None:
            self._section_vehicles[sec].add(oht_name)
        if c > self.max_node_inflight:
            self.max_node_inflight = c
        # P0-1 — 공유 bridge 혼잡 카운터를 항상 동기화. CongestionAwareStrategy
        # (route.Dispatcher 가 쓰는 전략) 는 bridge.get_section_congestion() 을
        # 보므로, routing_model='off' 에서도 실제 in-flight 점유를 반영해야
        # '--strategy congestion' 이 nearest 로 퇴화하지 않는다.
        # node.traffic_penalty 갱신은 dynamic 일 때만 (정적 라우팅 의미 보존).
        self.bridge.on_oht_enter_section(
            sec, update_penalty=(self.routing_model == 'dynamic'))
        # F11 — dynamic routing 시 캐시 무효화
        self._on_enter_section_dynamic(sec)

    def _remove_inflight(self, sec: int, now: Optional[float] = None,
                         oht_name: Optional[str] = None):
        c = self.section_inflight.get(sec, 0)
        if c > 0:
            self.section_inflight[sec] = c - 1
        if self.congestion_model == 'queue' and oht_name is not None:
            self._section_vehicles[sec].discard(oht_name)
        # P0-1 — bridge 혼잡 카운터 항상 동기화 (위 _add_inflight 참고)
        self.bridge.on_oht_leave_section(
            sec, update_penalty=(self.routing_model == 'dynamic'))
        # F11 — dynamic routing 시 캐시 무효화
        self._on_leave_section_dynamic(sec)
        # 'queue' — 자리가 났으니 이 section 진입 대기열의 선두부터 깨운다.
        if self.congestion_model == 'queue' and now is not None:
            self._wake_waiters(sec, now)

    def _congestion_multiplier(self, sec: int) -> float:
        """진입 시점의 통과 시간 multiplier — congestion_model 분기."""
        model = self.congestion_model
        if model == 'off' or model == 'queue':
            # queue 모델은 delay 보정 없음 — 혼잡은 blocking 으로만 표현.
            return 1.0
        if model == 'section_local':
            return 1.0 + self.congestion_alpha * self.section_inflight.get(sec, 0)
        if model == 'global_tip':
            # LogiFabSim B — 전역 TIP/TIP_max 형태. TIP_max = num_oht (모든 OHT in-flight).
            tip_ratio = self._busy_count / self._num_oht if self._num_oht else 0.0
            return 1.0 + self.congestion_alpha * tip_ratio
        return 1.0

    # F12 — KPI snapshot 간격 (transition 카운터 기준)
    _KPI_SNAPSHOT_EVERY = 200

    def _snapshot_kpi(self, t: float):
        """현재 sim 시점의 KPI snapshot 을 시계열에 추가."""
        inflight_sum = sum(self.section_inflight.values())
        self.kpi_snapshots.append((
            t,
            self._busy_count,
            inflight_sum,
            len(self.pending),
            self._delivered_count,
            self.max_node_inflight,
        ))

    def on_section_enter(self, instance, oht: OHT, job: TransportJob,
                          next_idx: int, t: float, force: bool = False):
        """OHT 가 다음 section 으로 transition. 점유 갱신 + 통과시간 결정.

        'queue' 모델에서는 목적 section 이 가득 차면 진입하지 못하고 FIFO
        대기열에 등록된다 (이전 section 점유 유지). force=True 는 데드락
        해소용 강제 진입 — 용량 검사를 건너뛴다.
        """
        self._update_busy_time(t)

        prev_sec = job.section_path[next_idx - 1]
        new_sec = job.section_path[next_idx]

        # ── 'queue' — 용량 검사. 가득 차면 진입 차단 + FIFO 대기 ──
        if self.congestion_model == 'queue' and not force:
            cap = self.section_capacity.get(new_sec, 1)
            if self.section_inflight.get(new_sec, 0) >= cap:
                self._waiting[new_sec].append((oht, job, next_idx))
                self._blocked_since[oht.name] = (new_sec, t)
                if t >= self.measurement_start_time:
                    self.total_blocked_events += 1
                    self.blocked_by_section[new_sec] = \
                        self.blocked_by_section.get(new_sec, 0) + 1
                self._schedule_deadlock_check(t)
                return

        self.total_transitions += 1
        # F12 — 주기적 KPI snapshot (record_trajectory ON 일 때만)
        if (self.record_trajectory
                and self.total_transitions % self._KPI_SNAPSHOT_EVERY == 0):
            self._snapshot_kpi(t)

        # 점유 update — 새 +1 을 *먼저* (queue 모드에서 prev 해제가 대기열
        # wake 연쇄를 일으키므로, 방금 용량 검사한 new_sec 자리를 다른 차량이
        # 가로채지 못하게 선점). prev != new 이므로 delay 모델 계산엔 영향 없음.
        self._add_inflight(new_sec, oht_name=oht.name)
        self._remove_inflight(prev_sec, now=t, oht_name=oht.name)

        oht.current_section_id = new_sec
        oht.time_enter_current_section = t  # viewer 보간용
        job.current_idx = next_idx

        # pickup section 진입 시 상태 전환
        if next_idx == job.pickup_idx and oht.status == "ASSIGNED":
            oht.status = "LOADED"

        # 새 section 의 통과 시간 — *진입 시점의* 점유 (방금 +1 한 것 포함) 반영
        base_dur = job.section_durations[next_idx]
        actual_dur = base_dur * self._congestion_multiplier(new_sec)
        # leg 별 실제 시간 누적 (statistics)
        if next_idx <= job.pickup_idx:
            job.empty_actual += actual_dur
        else:
            job.loaded_actual += actual_dur

        next_t = t + actual_dur
        last_idx = len(job.section_path) - 1
        if next_idx == last_idx:
            self.instance.add_event(ArriveEvent(next_t, self, oht, job))
        else:
            self.instance.add_event(
                SectionEnterEvent(next_t, self, oht, job, next_idx + 1))

    def on_arrive(self, instance, oht: OHT, job: TransportJob, t: float):
        """마지막 section 의 통과까지 끝남 — 배달 완료 + OHT 재활용."""
        self._update_busy_time(t)
        # 마지막 section 의 점유 해제 (OHT 가 도착 후 idle 이 되면 section 점유는 0
        # 가정 — _assign 시점에서 다시 +1 되어야 일관됨)
        last_sec = job.section_path[job.current_idx]
        self._remove_inflight(last_sec, now=t, oht_name=oht.name)

        job.dur = t - job.assignment_time
        if job.count_for_stats and job.lot is not None:
            self.total_jobs += 1
            self.total_transport_time += job.dur
            self.total_free_flow += job.free_flow
            self.total_wait_for_oht += job.wait
            self.delivery_samples.append(job.wait + job.dur)
            self.transport_samples.append(job.dur)
            if job.empty_actual > 0 or job.loaded_actual > 0:
                self.total_empty_time += job.empty_actual
                self.total_loaded_time += job.loaded_actual
            elif job.free_flow:
                empty_ratio = job.empty_t / job.free_flow
                self.total_empty_time += job.dur * empty_ratio
                self.total_loaded_time += job.dur * (1.0 - empty_ratio)

        # 궤적 기록 — reposition (lot=None) 은 trajectory 에 넣지 않음.
        if self.record_trajectory and job.lot is not None:
            # 실제 시간 분배: empty leg 끝까지 누적은 job.empty_actual 에 있음 (없으면 free-flow 비율)
            if job.empty_actual > 0 or job.loaded_actual > 0:
                empty_dur = job.empty_actual
                loaded_dur = job.loaded_actual
            else:
                # 폴백 — section 1개짜리 짧은 trip
                empty_dur = job.dur * (job.empty_t / job.free_flow if job.free_flow else 0.5)
                loaded_dur = job.dur - empty_dur
            self.trip_log.append({
                'oht_id': oht.name,
                'request_time': job.request_time,
                'assignment_time': job.assignment_time,
                'delivery_time': t,
                'empty_path': list(job.empty_path),
                'loaded_path': list(job.loaded_path),
                'empty_duration': empty_dur,
                'loaded_duration': loaded_dur,
                'congestion': (job.dur / job.free_flow) if job.free_flow else 1.0,
            })

        # F12 — delivered count 증가 (reposition 제외)
        if job.lot is not None:
            self._delivered_count += 1

        # 생산 레이어 콜백
        job.on_deliver(t, job.dur)

        # OHT 는 목적 노드/섹션에서 유휴
        oht.current_node = job.to_node
        new_sec = self.bridge.get_section_for_node(job.to_node)
        if new_sec is not None:
            oht.current_section_id = new_sec
        oht.time_enter_current_section = t  # viewer 보간용 (도착 = 섹션 진입)
        oht.busy = False
        oht.status = "IDLE"
        self._busy_count -= 1

        # 대기 작업 있으면 즉시 다음 배차
        if self.pending:
            self._assign(oht, self.pending.popleft(), t)
        else:
            self.idle.append(oht)

    # ── F17: Custom strategy plugin 등록 ─────────────────
    def set_custom_strategies(self, *,
                              assignment=None, idle_positioning=None,
                              routing=None):
        """
        strategy_loader 가 로드한 객체를 amhs hook 에 inject.

        - assignment       : _pop_idle 의 dispatcher 위임 대체. select(...)
        - idle_positioning : _start_reposition 의 sparse 선택 대체. plan_reposition(...)
        - routing          : _route 의 pathfinder 우회. get_route(...)
        None 이면 변경 없음.
        """
        if assignment is not None:
            self.custom_assignment = assignment
        if idle_positioning is not None:
            self.custom_idle = idle_positioning
        if routing is not None:
            self.custom_routing = routing
            # routing 이 바뀌면 캐시 무효화
            self._route_cache.clear()
            self.bridge.clear_route_cost_cache()

    # ── F11: dynamic routing — traffic_penalty 갱신 + cache invalidate ──
    def _on_enter_section_dynamic(self, sec: int):
        """routing_model='dynamic' 일 때만 — 라우팅 캐시 무효화.

        혼잡 카운터/penalty 는 _add_inflight 에서 이미 갱신했다. 여기서는
        node.traffic_penalty 변동을 pathfinder cost 에 반영하기 위해 캐시만 비운다.
        """
        if self.routing_model != 'dynamic':
            return
        self._route_cache.clear()
        self.bridge.clear_route_cost_cache()

    def _on_leave_section_dynamic(self, sec: int):
        if self.routing_model != 'dynamic':
            return
        self._route_cache.clear()
        self.bridge.clear_route_cost_cache()

    # ── 'queue' 모델: 대기열 wake + 데드락 처리 ──────────────
    DEADLOCK_CHECK_INTERVAL_S = 60.0

    def _wake_waiters(self, sec: int, now: float):
        """sec 에 자리가 났을 때 FIFO 대기열 선두부터 진입시킨다.

        진입한 차량의 on_section_enter 가 자기 이전 section 을 해제하며
        다시 _wake_waiters 를 부르므로, 막힘 해소가 상류로 연쇄 전파된다.
        """
        q = self._waiting.get(sec)
        if not q:
            return
        cap = self.section_capacity.get(sec, 1)
        while q and self.section_inflight.get(sec, 0) < cap:
            oht, job, next_idx = q.popleft()
            _, since = self._blocked_since.pop(oht.name, (sec, now))
            if now >= self.measurement_start_time:
                self.total_blocked_time += now - since
                self.blocked_time_by_section[sec] = \
                    self.blocked_time_by_section.get(sec, 0.0) + (now - since)
            self.on_section_enter(self.instance, oht, job, next_idx, now)

    def _schedule_deadlock_check(self, now: float):
        if self._deadlock_check_pending or self.instance is None:
            return
        self._deadlock_check_pending = True
        self.instance.add_event(DeadlockCheckEvent(
            now + self.DEADLOCK_CHECK_INTERVAL_S, self))

    def check_deadlocks(self, t: float):
        """차단 차량 간 wait-for 사이클을 찾아 강제 진입으로 해소한다.

        차단 차량 w(현재 P 점유, T 진입 대기)는 T 의 점유자가 *전부* 차단
        차량일 때만 영구히 막힐 수 있다 (이동 중인 차량은 exit 이벤트가
        예약되어 있어 언젠가 자리를 비운다). 그 조건에서 w → (T 의 점유자)
        간선을 만들고 사이클을 찾으면, 사이클 내 최장 대기 차량을 용량
        무시하고 강제 진입시킨다 (deadlock_forced 집계).
        """
        self._deadlock_check_pending = False
        if not self._blocked_since:
            return

        # 강제 진입이 연쇄 wake 로 _blocked_since 를 바꾸므로, 사이클이
        # 없어질 때까지 반복 (차단 차량 수로 상한).
        for _ in range(len(self._blocked_since) + 1):
            victim = self._find_deadlock_victim()
            if victim is None:
                break
            self._force_admit(victim, t)

        if self._blocked_since:
            self._schedule_deadlock_check(t)

    def _find_deadlock_victim(self) -> Optional[str]:
        """wait-for 사이클 하나를 찾아 그 안의 최장 대기 OHT 이름을 반환."""
        blocked = set(self._blocked_since)
        adj: Dict[str, List[str]] = {}
        for name, (target, _since) in self._blocked_since.items():
            occupants = self._section_vehicles.get(target, set())
            # 이동 중(비차단) 점유자가 하나라도 있으면 자연 해소 가능 — 간선 없음
            if occupants and occupants <= blocked:
                adj[name] = list(occupants)
            else:
                adj[name] = []

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in blocked}
        for start in blocked:
            if color[start] != WHITE:
                continue
            stack: List[Tuple[str, int]] = [(start, 0)]
            path: List[str] = []
            while stack:
                node, ei = stack.pop()
                if ei == 0:
                    color[node] = GRAY
                    path.append(node)
                edges = adj.get(node, [])
                advanced = False
                for i in range(ei, len(edges)):
                    nxt = edges[i]
                    if color.get(nxt, BLACK) == GRAY:
                        # 사이클 발견 — path 에서 nxt 이후가 사이클 멤버
                        cycle = path[path.index(nxt):]
                        return min(
                            cycle, key=lambda n: self._blocked_since[n][1])
                    if color.get(nxt, BLACK) == WHITE:
                        stack.append((node, i + 1))
                        stack.append((nxt, 0))
                        advanced = True
                        break
                if not advanced:
                    color[node] = BLACK
                    path.pop()
        return None

    def _force_admit(self, name: str, t: float):
        """데드락 해소 — name 차량을 대기열에서 꺼내 용량 무시하고 진입시킨다."""
        target, since = self._blocked_since.pop(name, (None, t))
        if target is None:
            return
        q = self._waiting.get(target)
        entry = None
        if q:
            for item in q:
                if item[0].name == name:
                    entry = item
                    break
            if entry is not None:
                q.remove(entry)
        if entry is None:
            return
        oht, job, next_idx = entry
        if t >= self.measurement_start_time:
            self.total_blocked_time += t - since
            self.blocked_time_by_section[target] = \
                self.blocked_time_by_section.get(target, 0.0) + (t - since)
        self.deadlock_forced += 1
        self.on_section_enter(self.instance, oht, job, next_idx, t, force=True)

    # ── F10: Idle vehicle positioning ─────────────────────
    def _build_adjacency(self):
        """section_id → 인접 section_id list. 한 section 안 노드의 graph edge 로 추론."""
        self.adjacency.clear()
        get_sec = self.bridge.get_section_for_node
        for sec_id, node_names in self.bridge.section_to_nodes.items():
            neighbors: Set[int] = set()
            for nname in node_names:
                n = self.rm.network.nodes.get(nname)
                if n is None:
                    continue
                for next_name in n.move_in_times.keys():
                    next_sec = get_sec(next_name)
                    if next_sec is not None and next_sec != sec_id:
                        neighbors.add(next_sec)
            self.adjacency[sec_id] = list(neighbors)

    def schedule_first_reposition(self, now: float):
        """instance 주입 후 첫 호출. 첫 RepositionTickEvent 를 큐에 등록."""
        if self.idle_positioning == 'off' or self.instance is None:
            return
        self.instance.add_event(RepositionTickEvent(
            now + self.reposition_interval_s, self))

    def tick_reposition(self, now: float):
        """주기적 reposition tick — IDLE OHT 중 일부를 sparse 인접 section 으로 이동."""
        self._update_busy_time(now)
        if self.idle_positioning == 'off':
            return
        if not self.idle:
            self._schedule_next_repo_tick(now)
            return

        # idle 중 현재 section_inflight 높은 곳 우선 이동 시도
        candidates = sorted(
            self.idle,
            key=lambda o: -self.section_inflight.get(o.current_section_id, 0))
        moved = 0
        # 후보를 더 넓게 잡되 reposition 성공한 건수로 cap
        for oht in candidates[: self.reposition_per_tick * 3]:
            if moved >= self.reposition_per_tick:
                break
            if self._start_reposition(oht, now):
                moved += 1

        self._schedule_next_repo_tick(now)

    def _schedule_next_repo_tick(self, now: float):
        self.instance.add_event(RepositionTickEvent(
            now + self.reposition_interval_s, self))

    def _normalize_reposition_target(self, result) -> Optional[int]:
        if result is None:
            return None
        if isinstance(result, int):
            return result
        if isinstance(result, str):
            node_sec = self.bridge.get_section_for_node(result)
            if node_sec is not None:
                return node_sec
            text = result.strip()
            if text.lower().startswith("sec"):
                text = text[3:]
            try:
                return int(text)
            except ValueError:
                return None

        path = normalize_section_path(result, None)
        return path[0] if path else None

    def _start_reposition(self, oht: OHT, now: float) -> bool:
        """OHT 를 인접 sparse section 으로 1-hop 이동 (성공 시 True)."""
        cur = oht.current_section_id
        # F17 — custom idle_positioning 우선
        best_sec: Optional[int] = None
        if self.custom_idle is not None:
            try:
                result = invoke_idle_positioning_strategy(
                    self.custom_idle, oht, now, oht.current_node,
                    self.rm, self.bridge, None, None)
                best_sec = self._normalize_reposition_target(result)
            except Exception as e:
                print(f"[amhs] ⚠️  custom_idle 오류 → spread 폴백: {e}")
                best_sec = None

        if best_sec is None:
            neighbors = self.adjacency.get(cur, ())
            if not neighbors:
                return False
            recent = self._recent_repo_sec.get(oht.name)
            cur_cong = self.section_inflight.get(cur, 0)
            # 인접 후보 — 자기 section 보다 inflight 적고 최근에 안 갔던 곳
            best_cong = cur_cong  # 같거나 더 적은 곳만
            for s in neighbors:
                if recent is not None and s in recent:
                    continue
                c = self.section_inflight.get(s, 0)
                if c < best_cong:
                    best_cong = c
                    best_sec = s
        if best_sec is None:
            return False

        target_node = (self.bridge.get_entry_node(best_sec)
                       or self.bridge.get_exit_node(best_sec))
        if not target_node:
            return False

        # idle pool 에서 제거
        try:
            self.idle.remove(oht)
        except ValueError:
            return False

        # 미니 reposition job — lot=None, on_deliver = no-op
        rjob = TransportJob(
            lot=None, from_node=oht.current_node, to_node=target_node,
            request_time=now, on_deliver=lambda dt, dur: None)
        rjob.count_for_stats = False
        self._recent_repo_sec[oht.name].append(best_sec)
        self._assign_reposition(oht, rjob, now)
        return True

    def _assign_reposition(self, oht: OHT, job: TransportJob, now: float):
        """_assign 의 reposition 전용 단순화 — 빈 leg 만, pickup 없음."""
        self._update_busy_time(now)
        oht.busy = True
        oht.status = "REPOSITIONING"
        self._busy_count += 1
        job.assignment_time = now
        job.wait = 0.0

        nodes, secs, durs = self._route(oht.current_node, job.to_node)
        if not secs:
            # 이동 불가 — 즉시 복귀
            oht.busy = False
            oht.status = "IDLE"
            self._busy_count -= 1
            self.idle.append(oht)
            return

        job.section_path = secs
        job.section_durations = durs
        job.pickup_idx = -1            # pickup 이벤트 발생 안 함
        job.node_path = nodes
        job.empty_path = nodes
        job.loaded_path = []
        job.empty_t = sum(durs)
        job.loaded_t = 0.0
        job.free_flow = job.empty_t

        first_sec = secs[0]
        self._add_inflight(first_sec, oht_name=oht.name)
        job.current_idx = 0

        first_dur = durs[0] * self._congestion_multiplier(first_sec)
        job.empty_actual += first_dur
        next_t = now + first_dur

        self.total_repositions += 1
        if len(secs) == 1:
            self.instance.add_event(ArriveEvent(next_t, self, oht, job))
        else:
            self.instance.add_event(
                SectionEnterEvent(next_t, self, oht, job, next_idx=1))
