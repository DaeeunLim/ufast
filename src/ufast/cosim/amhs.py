"""
amhs.py — co-simulation logistics (AMHS) runner (section-aware, transition events).

Receives the transport jobs (TransportJob) issued by the production layer, assigns
OHTs to them, and notifies the production layer via callback when a transport
completes.

U-FAST novelty:
  - **Section-level transition events** — every time an OHT crosses from section A
    to section B, a SectionEnterEvent occurs explicitly on the time axis.
    Node-by-node movement inside a section is not simulated; only the section
    traversal time is computed as free-flow (acceleration/deceleration kinematics).
  - **Congestion propagation (slowdown)** — the traversal time of each section is
    determined by the section_inflight occupancy *at the moment of entering that
    section*: a dynamic state, not a static snapshot taken at issue time. If
    another OHT has just entered and incremented the occupancy, the OHT entering
    right after sees a larger inflight and traverses more slowly — congestion
    propagates naturally in the time domain.

Assignment:
  - Delegated to route.Dispatcher (NearestIdle / SameSectionFirst / CongestionAware).
  - 'fifo' bypasses the dispatcher (the fastest, simplest baseline).

Transport-time model:
  - Base traversal time of each section: the free-flow sum of the
    acceleration/deceleration kinematics over the node_path segment occupying
    that section.
  - Actual traversal time = base * (1 + alpha * section_inflight[sec] at entry).

Event-queue flow (per transport):
  issue at t₀
  → SectionEnterEvent(S₁) at t₁ = t₀ + travel(S₀ → S₁)
  → SectionEnterEvent(S₂) at t₂ = t₁ + travel(S₁ → S₂, inflight at t₁)
  → ...
  → SectionEnterEvent(pickup_section) at t_p  — status: ASSIGNED → LOADED
  → ...
  → ArriveEvent(dest_section) at t_d  — on_deliver callback, OHT idle
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


# Same strategy names as legacy + 'fifo' (dispatcher bypass).
_STRATEGY_FACTORIES = {
    'nearest':      NearestIdleStrategy,
    'same_section': SameSectionFirstStrategy,
    'congestion':   CongestionAwareStrategy,
}


class OHT:
    """Transport vehicle — section-level position/state (satisfies the Dispatcher OHTInfo Protocol)."""

    def __init__(self, name: str, node: str, section_id: int):
        self.name = name
        self.current_node = node
        self.current_section_id = section_id
        self.time_enter_current_section: float = 0.0  # for viewer position interpolation
        self.status = "IDLE"     # IDLE / ASSIGNED / LOADED — compatible with Dispatcher
        self.busy = False        # internal compatibility flag

    def __repr__(self):
        return f"OHT({self.name}@sec{self.current_section_id}/{self.current_node})"


class TransportJob:
    """
    Transport job — the production layer only knows the node arguments; internally
    it is expanded into a section sequence.

    The cursor (section_idx) advances as transition events are processed.
    """

    def __init__(self, lot, from_node: str, to_node: str,
                 request_time: float, on_deliver: Callable):
        self.lot = lot
        self.from_node = from_node
        self.to_node = to_node
        self.request_time = request_time
        self.on_deliver = on_deliver

        # route (filled at assign)
        self.section_path: List[int] = []          # full section sequence (incl. start sec)
        self.section_durations: List[float] = []   # base free-flow traversal time of each section (traversing sec[i])
        self.pickup_idx: int = -1                  # index in section_path where pickup happens
        self.node_path: List[str] = []             # for debug/trajectory

        # progress state
        self.current_idx: int = 0                  # index of the currently occupied section (= section_path[current_idx])
        self.assignment_time: float = 0.0          # time of _assign

        # statistics
        self.dur = 0.0                  # actual total transport duration (computed at delivery)
        self.free_flow = 0.0            # sum(section_durations)
        self.empty_t = 0.0               # free-flow up to pickup
        self.loaded_t = 0.0              # free-flow after pickup
        self.empty_actual = 0.0          # actual up to pickup (with congestion)
        self.loaded_actual = 0.0         # actual after pickup (with congestion)
        self.wait = 0.0
        self.count_for_stats = True
        # trajectory compatibility — the two legs' node_paths, kept separate since the atomic era
        self.empty_path: List[str] = []
        self.loaded_path: List[str] = []


class SectionEnterEvent:
    """Event at the moment an OHT enters section_path[next_idx]."""

    __slots__ = ('timestamp', 'executor', 'oht', 'job', 'next_idx',
                 'machines', 'lots')

    def __init__(self, timestamp: float, executor: "AMHSExecutor",
                 oht: OHT, job: TransportJob, next_idx: int):
        self.timestamp = timestamp
        self.executor = executor
        self.oht = oht
        self.job = job
        self.next_idx = next_idx
        # PySCFabSim event interface compatibility
        self.machines: List = []
        self.lots: List = []

    def handle(self, instance):
        self.executor.on_section_enter(instance, self.oht, self.job,
                                       self.next_idx, self.timestamp)


class ArriveEvent:
    """Event at the moment an OHT reaches the destination section and completes delivery."""

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
    """Periodic idle reposition trigger — fired at a fixed sim interval."""

    __slots__ = ('timestamp', 'executor', 'machines', 'lots')

    def __init__(self, timestamp: float, executor: "AMHSExecutor"):
        self.timestamp = timestamp
        self.executor = executor
        self.machines: List = []
        self.lots: List = []

    def handle(self, instance):
        self.executor.tick_reposition(self.timestamp)


class DeadlockCheckEvent:
    """'queue' model only — periodic deadlock check while any OHT is blocked."""

    __slots__ = ('timestamp', 'executor', 'machines', 'lots')

    def __init__(self, timestamp: float, executor: "AMHSExecutor"):
        self.timestamp = timestamp
        self.executor = executor
        self.machines: List = []
        self.lots: List = []

    def handle(self, instance):
        self.executor.check_deadlocks(self.timestamp)


class AMHSExecutor:
    """Finite OHT pool + section transition events + dynamic slowdown model."""

    DISPATCH_STRATEGIES = ('fifo', 'nearest', 'same_section', 'congestion')

    # Congestion model — traversal-time correction applied at each section transition.
    #   'off'           : no correction (c=1, free-flow only).
    #   'global_tip'    : LogiFabSim style — c = 1 + α * (current in-flight OHT count / num_oht).
    #                     Proportional to global TIP. Ignores the layout.
    #   'section_local' : U-FAST specific — c = 1 + α * section_inflight[sec].
    #                     Local and dynamic (occupancy at transition time).
    #   'queue'         : capacity-constrained blocking model (headless port of the GUI
    #                     logistics model). Traversal time is free-flow (c=1), but when
    #                     the section capacity (= ⌊section length / OHT footprint⌋) is
    #                     exceeded, entry is *blocked* and vehicles wait FIFO → they
    #                     enter in order as space frees up. Circular waits (deadlocks)
    #                     are detected by a periodic check and resolved by forced
    #                     admission (counted as deadlock_forced). Congestion emerges as
    #                     back-pressure queueing rather than as a delay. Slow for long
    #                     full-fab experiments because of the large event count.
    #                     Note: unlike the GUI model, IDLE OHTs are not counted as
    #                     occupancy (same convention as the delay models — +1 when a
    #                     transport starts).
    CONGESTION_MODELS = ('off', 'global_tip', 'section_local', 'queue')

    # Routing model — whether congestion is reflected as *cost* in path search.
    #   'off'    : static. The (a,b)→node_path computed once is kept in _route_cache and reused.
    #   'dynamic': calls bridge.on_oht_enter_section/leave_section to update the per-node
    #              traffic_penalty in proportion to OHT occupancy. On every transport
    #              _route_cache and bridge.route_cost_cache are invalidated → the
    #              pathfinder finds congestion-avoiding routes. Cost explodes (not
    #              recommended at production scale).
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
        # global_tip denominator — number of busy OHTs (in-flight counter)
        self._busy_count = 0
        if routing_model not in self.ROUTING_MODELS:
            raise ValueError(
                f"unknown routing_model: {routing_model!r} "
                f"(expected one of {self.ROUTING_MODELS})")
        self.routing_model = routing_model

        # ── Idle vehicle positioning (F10) ──
        # 'off'         : disabled — IDLE OHTs stay in the section they arrived in.
        # 'spread'      : 1-hop move to the adjacent section with fewer inflight OHTs.
        #                 Simplified version of reposition_idle_ohts from fromto-mode legacy.
        self.idle_positioning = idle_positioning
        self.reposition_interval_s = reposition_interval_s
        self.reposition_per_tick = reposition_per_tick
        # per-OHT recently repositioned sections (ping-pong prevention — last 3)
        self._recent_repo_sec: Dict[str, Deque[int]] = defaultdict(
            lambda: deque(maxlen=3))
        # section_id → list of adjacent section_ids. Filled by _build_adjacency.
        self.adjacency: Dict[int, List[int]] = {}

        # everything except fifo delegates to route.Dispatcher
        if dispatch_strategy == 'fifo':
            self.dispatcher: Optional[Dispatcher] = None
        else:
            strat = _STRATEGY_FACTORIES[dispatch_strategy]()
            self.dispatcher = Dispatcher(route_manager, bridge, strat)

        # Edge index: (from, to) → (distance, speed limit mm/s). For the kinematics computation.
        # Speed limits come from VehicleSpec (line/curve_speed_mm_s).
        self.line_speed_mm_s = line_speed_mm_s
        self.curve_speed_mm_s = curve_speed_mm_s
        self._edge: Dict[Tuple[str, str], Tuple[float, float]] = {
            (l.from_node, l.to_node):
                (l.distance, line_speed_mm_s if l.link_type == 'LINE'
                 else curve_speed_mm_s)
            for l in route_manager.network.links
        }

        # ── 'queue' model (capacity-constrained blocking) state ──
        # section_capacity : sec → number of OHTs that fit simultaneously
        #                    (= max(1, ⌊sum of section length / footprint⌋)).
        # _waiting         : sec → FIFO of (oht, job, next_idx) waiting to enter.
        # _blocked_since   : oht.name → (target_sec, time the wait started).
        # _section_vehicles: sec → set of OHT names currently occupying it in flight
        #                    (for building the deadlock wait-for graph; queue mode only).
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
        # per-section blocking statistics — for the blocking heatmap (filled in queue mode only)
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

        # OHT initialisation — also compute and keep the section_id of the start node.
        self.ohts: List[OHT] = []
        for i in range(num_oht):
            node = oht_start_nodes[i % len(oht_start_nodes)]
            sec = bridge.get_section_for_node(node)
            if sec is None:
                sec = next(iter(bridge.section_to_nodes), -1)
            self.ohts.append(OHT(f"OHT_{i:04d}", node, sec))

        # trajectory recording
        self.initial_positions: Dict[str, str] = {o.name: o.current_node for o in self.ohts}
        self.trip_log: List[Dict] = []

        self._oht_by_name: Dict[str, OHT] = {o.name: o for o in self.ohts}
        self.idle: List[OHT] = list(self.ohts)
        self.pending: Deque[TransportJob] = deque()
        self.instance = None  # injected by UFastInstance

        # Route cache: (a_node, b_node) → (node_path, section_path, section_durations)
        # section_durations[i] = free-flow time of the node_path segment occupied by section_path[i].
        self._route_cache: Dict[
            Tuple[str, str],
            Tuple[List[str], List[int], List[float]]
        ] = {}

        # In-flight OHT count per section (occupancy while travelling a route). Dynamic — updated at every transition.
        self.section_inflight: Dict[int, int] = {}

        # statistics
        self.measurement_start_time = 0.0
        self.total_requested_jobs = 0
        self.total_jobs = 0
        self.total_transport_time = 0.0
        self.total_free_flow = 0.0
        self.total_empty_time = 0.0
        self.total_loaded_time = 0.0
        self.total_wait_for_oht = 0.0
        # per-job delivery-time (request→delivery) and transport-time samples in the measurement window — for tail (p95/p99) computation
        self.delivery_samples: List[float] = []
        self.transport_samples: List[float] = []
        self.total_transitions = 0
        self.total_repositions = 0       # F10 — number of reposition moves performed
        self.max_queue = 0
        self.max_node_inflight = 0   # name kept for external compatibility (actually max section_inflight)
        self.total_busy_oht_time = 0.0
        self.last_event_time = 0.0

        # F12 — KPI time series (only when record_trajectory=True)
        # cumulative completed transports (delivered lot transports)
        self._delivered_count = 0
        # snapshot list: (sim_time, busy_count, sum_section_inflight,
        #                  pending_queue, delivered, max_section_inflight)
        self.kpi_snapshots: List[Tuple[float, int, int, int, int, int]] = []

        # F17 — Custom strategy plugin hooks. None means the built-in is used.
        #   custom_assignment.select(target_sec, idle_ohts, rm, bridge) -> oht_name|None
        #   custom_idle.plan_reposition(oht, t, rm, bridge) -> next_section_id|None
        #   custom_routing.get_route(a, b, rm, bridge) -> List[node]
        self.custom_assignment = None
        self.custom_idle = None
        self.custom_routing = None

        # Adjacency graph + first reposition tick — scheduling is possible once instance is injected.
        # (instance is still None here — deferred until request_transport is first called.)
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

    # ── Routes / times ───────────────────────────────────
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
        Split node_path at section boundaries and return (section_path, section_durations).

        - section_path[i] : id of the i-th section entered (including the start section)
        - section_durations[i] : sum of node-edge free-flow times while the OHT stays in
          that section (until just before entering the next section). The last item is
          the time inside the last section up to the arrival node.
        """
        if not node_path:
            return [], []

        get_sec = self.bridge.get_section_for_node
        node_secs: List[Optional[int]] = [get_sec(n) for n in node_path]

        # Accumulate time while looking for each transition point.
        section_path: List[int] = []
        section_durations: List[float] = []
        cur_sec: Optional[int] = node_secs[0] if node_secs else None
        accum_time = 0.0

        if cur_sec is not None:
            section_path.append(cur_sec)

        # walk edge by edge between nodes
        for i in range(len(node_path) - 1):
            a, b = node_path[i], node_path[i + 1]
            e = self._edge.get((a, b))
            t = self.kin.path_time([e]) if e is not None else 0.0
            next_sec = node_secs[i + 1]

            # if next_sec equals cur_sec it is movement inside the same section — just accumulate
            if next_sec == cur_sec or next_sec is None:
                accum_time += t
                continue

            # Section boundary — include this edge's time in the previous section's
            # traversal time and switch to next_sec. (Node b already belongs to next_sec,
            # so this edge is treated as the last movement "just before exiting cur_sec".)
            accum_time += t
            section_durations.append(accum_time)
            section_path.append(next_sec)
            cur_sec = next_sec
            accum_time = 0.0

        section_durations.append(accum_time)
        return section_path, section_durations

    def _route(self, a: str, b: str) -> Tuple[List[str], List[int], List[float]]:
        """(node_path, section_path, section_durations). Cached."""
        if a == b:
            return [], [], []
        key = (a, b)
        # F17 — with custom routing, bypass the cache (lets congestion changes be reflected every time)
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
                print(f"[amhs] ⚠️  custom_routing error → falling back to default routing: {e}")
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
            # Section-level routing — U-FAST novelty.
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

    # ── Assignment ───────────────────────────────────────
    def _pop_idle(self, job: TransportJob) -> Optional[OHT]:
        """Take one OHT from the idle pool according to the strategy (None if none available)."""
        if not self.idle:
            return None
        target_sec = self.bridge.get_section_for_node(job.from_node)
        # F17 — custom assignment takes precedence when present
        if self.custom_assignment is not None and target_sec is not None:
            idle_dict = {o.name: o for o in self.idle}
            try:
                selected = invoke_assignment_strategy(
                    self.custom_assignment, target_sec, list(self.idle),
                    self.rm, self.bridge, None)
                name = normalize_oht_selection(selected, idle_dict)
            except Exception as e:
                print(f"[amhs] ⚠️  custom_assignment error → falling back to fifo: {e}")
                name = None
            if name is not None:
                oht = idle_dict.get(name)
                if oht is not None:
                    try:
                        self.idle.remove(oht)
                        return oht
                    except ValueError:
                        pass
            # fallback
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
        """The production layer issues a transport job."""
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

    # ── Schedule the first transition after assignment ───
    def _assign(self, oht: OHT, job: TransportJob, now: float):
        """
        Assign job to oht. Concatenate the section paths of the empty leg and the loaded
        leg and schedule the first transition event. Subsequent transitions are chained
        by on_section_enter.
        """
        self._update_busy_time(now)
        oht.busy = True
        oht.status = "ASSIGNED"
        self._busy_count += 1
        job.assignment_time = now
        job.wait = now - job.request_time

        # empty leg (OHT→pickup) + loaded leg (pickup→destination)
        empty_nodes, empty_secs, empty_durs = self._route(oht.current_node, job.from_node)
        loaded_nodes, loaded_secs, loaded_durs = self._route(job.from_node, job.to_node)

        # Concatenate the two legs, removing the duplicated pickup section.
        # If the empty leg is empty (OHT already at the pickup position): pickup section = OHT current.
        if empty_secs and loaded_secs and empty_secs[-1] == loaded_secs[0]:
            # same section — its traversal time is the sum of empty + loaded
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
            # both empty — immediate delivery
            merged_secs = [oht.current_section_id]
            merged_durs = [0.0]
            pickup_idx = 0

        job.section_path = merged_secs
        job.section_durations = merged_durs
        job.pickup_idx = pickup_idx
        job.node_path = empty_nodes + (loaded_nodes[1:] if loaded_nodes else [])
        job.empty_path = empty_nodes
        job.loaded_path = loaded_nodes
        # Per-leg free-flow times — split merged_durs at pickup_idx.
        # (An earlier version summed empty_durs+loaded_durs and double-counted the
        # pickup section's traversal time.)
        job.empty_t = sum(merged_durs[: pickup_idx + 1])
        job.loaded_t = sum(merged_durs[pickup_idx + 1:])
        job.free_flow = sum(merged_durs)

        # The OHT is considered to be in section_path[0] already (it stayed there while idle).
        # Keep the convention that idle OHTs are not counted in the section occupancy —
        # +1 only when a transport *starts*. So section_path[0] (start section) gets +1 too.
        first_sec = merged_secs[0]
        self._add_inflight(first_sec, oht_name=oht.name)
        job.current_idx = 0
        # pickup_idx == 0 (OHT already in the pickup section) → LOADED on entry.
        if pickup_idx == 0:
            oht.status = "LOADED"

        # traversal time of the start section (inside it, up to the next section boundary)
        first_dur = merged_durs[0] * self._congestion_multiplier(first_sec)
        # the first section's traversal time must also go into the per-leg actual statistics.
        if 0 <= pickup_idx:
            job.empty_actual += first_dur
        else:
            job.loaded_actual += first_dur
        next_t = now + first_dur

        # schedule the first transition — with a single-section path, arrive directly
        if len(merged_secs) == 1:
            self.instance.add_event(ArriveEvent(next_t, self, oht, job))
        else:
            self.instance.add_event(
                SectionEnterEvent(next_t, self, oht, job, next_idx=1))

    # ── Transition handling ──────────────────────────────
    def _add_inflight(self, sec: int, oht_name: Optional[str] = None):
        c = self.section_inflight.get(sec, 0) + 1
        self.section_inflight[sec] = c
        if self.congestion_model == 'queue' and oht_name is not None:
            self._section_vehicles[sec].add(oht_name)
        if c > self.max_node_inflight:
            self.max_node_inflight = c
        # P0-1 — always synchronise the shared bridge congestion counter.
        # CongestionAwareStrategy (the strategy used by route.Dispatcher) reads
        # bridge.get_section_congestion(), so the actual in-flight occupancy must be
        # reflected even with routing_model='off' — otherwise '--strategy congestion'
        # degenerates to nearest.
        # node.traffic_penalty is updated only when dynamic (preserves static-routing semantics).
        self.bridge.on_oht_enter_section(
            sec, update_penalty=(self.routing_model == 'dynamic'))
        # F11 — cache invalidation under dynamic routing
        self._on_enter_section_dynamic(sec)

    def _remove_inflight(self, sec: int, now: Optional[float] = None,
                         oht_name: Optional[str] = None):
        c = self.section_inflight.get(sec, 0)
        if c > 0:
            self.section_inflight[sec] = c - 1
        if self.congestion_model == 'queue' and oht_name is not None:
            self._section_vehicles[sec].discard(oht_name)
        # P0-1 — always synchronise the bridge congestion counter (see _add_inflight above)
        self.bridge.on_oht_leave_section(
            sec, update_penalty=(self.routing_model == 'dynamic'))
        # F11 — cache invalidation under dynamic routing
        self._on_leave_section_dynamic(sec)
        # 'queue' — space freed up, so wake the head of this section's entry queue.
        if self.congestion_model == 'queue' and now is not None:
            self._wake_waiters(sec, now)

    def _congestion_multiplier(self, sec: int) -> float:
        """Traversal-time multiplier at entry — branches on congestion_model."""
        model = self.congestion_model
        if model == 'off' or model == 'queue':
            # the queue model has no delay correction — congestion is expressed by blocking only.
            return 1.0
        if model == 'section_local':
            return 1.0 + self.congestion_alpha * self.section_inflight.get(sec, 0)
        if model == 'global_tip':
            # LogiFabSim B — global TIP/TIP_max form. TIP_max = num_oht (all OHTs in flight).
            tip_ratio = self._busy_count / self._num_oht if self._num_oht else 0.0
            return 1.0 + self.congestion_alpha * tip_ratio
        return 1.0

    # F12 — KPI snapshot interval (in transition counts)
    _KPI_SNAPSHOT_EVERY = 200

    def _snapshot_kpi(self, t: float):
        """Append a KPI snapshot at the current sim time to the time series."""
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
        """OHT transitions into the next section. Update occupancy + determine traversal time.

        In the 'queue' model, if the target section is full the OHT cannot enter and is
        put on the FIFO wait queue (keeping its previous section occupancy). force=True is
        the forced admission used for deadlock resolution — it skips the capacity check.
        """
        self._update_busy_time(t)

        prev_sec = job.section_path[next_idx - 1]
        new_sec = job.section_path[next_idx]

        # ── 'queue' — capacity check. If full, block entry + wait FIFO ──
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
        # F12 — periodic KPI snapshot (only when record_trajectory is ON)
        if (self.record_trajectory
                and self.total_transitions % self._KPI_SNAPSHOT_EVERY == 0):
            self._snapshot_kpi(t)

        # Occupancy update — the new +1 comes *first* (in queue mode releasing prev
        # triggers a wake cascade, so claim the new_sec slot we just capacity-checked
        # before another vehicle can grab it). prev != new, so the delay-model
        # computation is unaffected.
        self._add_inflight(new_sec, oht_name=oht.name)
        self._remove_inflight(prev_sec, now=t, oht_name=oht.name)

        oht.current_section_id = new_sec
        oht.time_enter_current_section = t  # for viewer interpolation
        job.current_idx = next_idx

        # state transition on entering the pickup section
        if next_idx == job.pickup_idx and oht.status == "ASSIGNED":
            oht.status = "LOADED"

        # traversal time of the new section — reflects occupancy *at entry* (including the +1 just made)
        base_dur = job.section_durations[next_idx]
        actual_dur = base_dur * self._congestion_multiplier(new_sec)
        # accumulate actual time per leg (statistics)
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
        """Traversal of the last section finished — delivery complete + OHT recycled."""
        self._update_busy_time(t)
        # Release the last section's occupancy (an OHT that goes idle after arrival is
        # assumed to occupy nothing — consistent with the +1 again at _assign)
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

        # Trajectory recording — repositions (lot=None) are not added to the trajectory.
        if self.record_trajectory and job.lot is not None:
            # Actual time split: the accumulation up to the end of the empty leg is in job.empty_actual (else free-flow ratio)
            if job.empty_actual > 0 or job.loaded_actual > 0:
                empty_dur = job.empty_actual
                loaded_dur = job.loaded_actual
            else:
                # fallback — short single-section trip
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

        # F12 — increment the delivered count (excluding repositions)
        if job.lot is not None:
            self._delivered_count += 1

        # production-layer callback
        job.on_deliver(t, job.dur)

        # the OHT idles at the destination node/section
        oht.current_node = job.to_node
        new_sec = self.bridge.get_section_for_node(job.to_node)
        if new_sec is not None:
            oht.current_section_id = new_sec
        oht.time_enter_current_section = t  # for viewer interpolation (arrival = section entry)
        oht.busy = False
        oht.status = "IDLE"
        self._busy_count -= 1

        # if jobs are pending, assign the next one immediately
        if self.pending:
            self._assign(oht, self.pending.popleft(), t)
        else:
            self.idle.append(oht)

    # ── F17: Custom strategy plugin registration ─────────
    def set_custom_strategies(self, *,
                              assignment=None, idle_positioning=None,
                              routing=None):
        """
        Inject objects loaded by strategy_loader into the amhs hooks.

        - assignment       : replaces the dispatcher delegation in _pop_idle. select(...)
        - idle_positioning : replaces the sparse selection in _start_reposition. plan_reposition(...)
        - routing          : bypasses the pathfinder in _route. get_route(...)
        None leaves the hook unchanged.
        """
        if assignment is not None:
            self.custom_assignment = assignment
        if idle_positioning is not None:
            self.custom_idle = idle_positioning
        if routing is not None:
            self.custom_routing = routing
            # invalidate the caches when routing changes
            self._route_cache.clear()
            self.bridge.clear_route_cost_cache()

    # ── F11: dynamic routing — traffic_penalty update + cache invalidation ──
    def _on_enter_section_dynamic(self, sec: int):
        """Only when routing_model='dynamic' — invalidate the routing caches.

        The congestion counter/penalty were already updated in _add_inflight. Here we
        only clear the caches so that node.traffic_penalty changes reach the pathfinder cost.
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

    # ── 'queue' model: wait-queue wake + deadlock handling ───
    DEADLOCK_CHECK_INTERVAL_S = 60.0

    def _wake_waiters(self, sec: int, now: float):
        """Admit vehicles from the head of the FIFO queue when space frees up in sec.

        The admitted vehicle's on_section_enter releases its previous section and calls
        _wake_waiters again, so the release of blocking cascades upstream.
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
        """Find wait-for cycles among blocked vehicles and resolve them by forced admission.

        A blocked vehicle w (occupying P, waiting to enter T) can be blocked forever only
        when *all* occupants of T are blocked vehicles (a moving vehicle has an exit event
        scheduled and will eventually free its slot). Under that condition, build edges
        w → (occupants of T); if a cycle is found, force the longest-waiting vehicle in
        the cycle to enter regardless of capacity (counted as deadlock_forced).
        """
        self._deadlock_check_pending = False
        if not self._blocked_since:
            return

        # Forced admission changes _blocked_since through the wake cascade, so repeat
        # until no cycle remains (bounded by the number of blocked vehicles).
        for _ in range(len(self._blocked_since) + 1):
            victim = self._find_deadlock_victim()
            if victim is None:
                break
            self._force_admit(victim, t)

        if self._blocked_since:
            self._schedule_deadlock_check(t)

    def _find_deadlock_victim(self) -> Optional[str]:
        """Find one wait-for cycle and return the name of the longest-waiting OHT in it."""
        blocked = set(self._blocked_since)
        adj: Dict[str, List[str]] = {}
        for name, (target, _since) in self._blocked_since.items():
            occupants = self._section_vehicles.get(target, set())
            # if any occupant is moving (not blocked), it can resolve naturally — no edge
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
                        # cycle found — the members are the path entries from nxt onwards
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
        """Deadlock resolution — take vehicle name off the wait queue and admit it regardless of capacity."""
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
        """section_id → list of adjacent section_ids, inferred from the graph edges of the nodes in each section."""
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
        """First call after instance injection. Registers the first RepositionTickEvent on the queue."""
        if self.idle_positioning == 'off' or self.instance is None:
            return
        self.instance.add_event(RepositionTickEvent(
            now + self.reposition_interval_s, self))

    def tick_reposition(self, now: float):
        """Periodic reposition tick — move some IDLE OHTs to sparse adjacent sections."""
        self._update_busy_time(now)
        if self.idle_positioning == 'off':
            return
        if not self.idle:
            self._schedule_next_repo_tick(now)
            return

        # try to move idle OHTs in sections with high section_inflight first
        candidates = sorted(
            self.idle,
            key=lambda o: -self.section_inflight.get(o.current_section_id, 0))
        moved = 0
        # consider a wider candidate set but cap by the number of successful repositions
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
        """Move the OHT 1 hop to an adjacent sparse section (True on success)."""
        cur = oht.current_section_id
        # F17 — custom idle_positioning takes precedence
        best_sec: Optional[int] = None
        if self.custom_idle is not None:
            try:
                result = invoke_idle_positioning_strategy(
                    self.custom_idle, oht, now, oht.current_node,
                    self.rm, self.bridge, None, None)
                best_sec = self._normalize_reposition_target(result)
            except Exception as e:
                print(f"[amhs] ⚠️  custom_idle error → falling back to spread: {e}")
                best_sec = None

        if best_sec is None:
            neighbors = self.adjacency.get(cur, ())
            if not neighbors:
                return False
            recent = self._recent_repo_sec.get(oht.name)
            cur_cong = self.section_inflight.get(cur, 0)
            # adjacent candidates — fewer inflight than the own section and not visited recently
            best_cong = cur_cong  # only equal or fewer
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

        # remove from the idle pool
        try:
            self.idle.remove(oht)
        except ValueError:
            return False

        # mini reposition job — lot=None, on_deliver = no-op
        rjob = TransportJob(
            lot=None, from_node=oht.current_node, to_node=target_node,
            request_time=now, on_deliver=lambda dt, dur: None)
        rjob.count_for_stats = False
        self._recent_repo_sec[oht.name].append(best_sec)
        self._assign_reposition(oht, rjob, now)
        return True

    def _assign_reposition(self, oht: OHT, job: TransportJob, now: float):
        """Simplified _assign for repositioning only — empty leg only, no pickup."""
        self._update_busy_time(now)
        oht.busy = True
        oht.status = "REPOSITIONING"
        self._busy_count += 1
        job.assignment_time = now
        job.wait = 0.0

        nodes, secs, durs = self._route(oht.current_node, job.to_node)
        if not secs:
            # cannot move — return to idle immediately
            oht.busy = False
            oht.status = "IDLE"
            self._busy_count -= 1
            self.idle.append(oht)
            return

        job.section_path = secs
        job.section_durations = durs
        job.pickup_idx = -1            # no pickup event occurs
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
