"""
Simulation controllers: OHT management (VehicleController) + event handling (EventHandler)
"""
import heapq
import os
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple
from ufast.core.data_set import SimulatorDataSet, Event
from ufast.common.fromto_parser import generate_fixed_interval_events

# Repositioning logs emit one line per event and flood the console — enable only for debugging.
_REPO_VERBOSE = bool(os.environ.get("UFAST_DEBUG_REPO"))
from ufast.common.logger import get_logger
from ufast.core.components import OHT, Section, EQ

try:
    from ufast.common.strategy_loader import (
        describe_strategy,
        invoke_assignment_strategy,
        invoke_routing_strategy,
        invoke_idle_positioning_strategy,
        normalize_oht_selection,
        normalize_section_path,
    )
    _CUSTOM_STRATEGY_AVAILABLE = True
except ImportError:
    describe_strategy = None
    invoke_assignment_strategy = None
    invoke_routing_strategy = None
    invoke_idle_positioning_strategy = None
    normalize_oht_selection = None
    normalize_section_path = None
    _CUSTOM_STRATEGY_AVAILABLE = False

try:
    from ufast.route import RouteManager
    from ufast.route.logistics_logger import get_logistics_logger
    _ROUTE_PKG_AVAILABLE = True
except ImportError:
    get_logistics_logger = None
    _ROUTE_PKG_AVAILABLE = False


class VehicleController:
    def __init__(self, num_vehicles: int, route_manager=None, bridge=None):
        self.num_of_vehicles = num_vehicles
        self.oht_list: Dict[str, OHT] = {}
        self.data_set = SimulatorDataSet.get_instance()
        self.route_manager = route_manager  # RouteManager from the route package (fallback if None)
        self.bridge = bridge                # SectionNodeBridge (fallback if None)
        self.dispatcher = None              # Dispatcher (injected in start_simulation)
        self.routing_strategy = None        # external routing strategy (.py/.pkl); existing logic if None
        self.assignment_strategy = None     # external assignment strategy (.py/.pkl); existing logic if None
        # Keep the existing idle repositioning feature, but when assigning a real LOT request,
        # treat REPOSITIONING vehicles as "idle vehicles in motion" and recall them immediately if needed.
        # The default Dispatcher/NearestIdleStrategy is left as is; this is an auxiliary selection layer on top.
        self.allow_repositioning_dispatch_preemption = True
        self.repositioning_dispatch_margin = 0.0

    def set_custom_strategies(self, routing_strategy=None, assignment_strategy=None):
        """Inject the external strategies selected in the UI at simulation start."""
        self.routing_strategy = routing_strategy
        self.assignment_strategy = assignment_strategy

    def _custom_strategy_name(self, strategy) -> str:
        if describe_strategy is not None:
            return describe_strategy(strategy)
        return type(strategy).__name__ if strategy is not None else "Default"

    def init(self):
        """Create OHTs and distribute them across sections"""
        for i in range(self.num_of_vehicles):
            name = f"OHT_{i:03d}"
            oht = OHT(name)
            self.oht_list[name] = oht
            self.data_set.oht_list[name] = oht

        self._distribute_ohts()

    def _route_log(self):
        if not _ROUTE_PKG_AVAILABLE or get_logistics_logger is None:
            return None
        return get_logistics_logger()

    def _log_dispatch_decision(self, current_time: float, target_section_id: int, selected_oht: Optional[OHT], reason: str):
        log = self._route_log()
        if log is None:
            return
        idle_count = sum(1 for x in self.oht_list.values() if x.status == "IDLE")
        log.log_dispatch(
            sim_time=current_time,
            target_section_id=target_section_id,
            selected_oht=selected_oht.name if selected_oht else None,
            strategy_name=(
                self._custom_strategy_name(self.assignment_strategy)
                if self.assignment_strategy is not None
                else (type(self.dispatcher.strategy).__name__ if self.dispatcher is not None else "ControllerFallback")
            ),
            idle_count=idle_count,
            total_count=len(self.oht_list),
            reason=reason,
        )

    def _log_vehicle_move_event(self, current_time: float, oht_name: str, from_sec: Optional[int], to_sec: Optional[int], success: bool, blocked_by: str = ""):
        log = self._route_log()
        if log is None:
            return
        log.log_vehicle_move(
            sim_time=current_time,
            vehicle_name=oht_name,
            from_node=f"sec{from_sec}" if from_sec is not None else "",
            to_node=f"sec{to_sec}" if to_sec is not None else "",
            success=success,
            blocked_by=blocked_by or None,
        )

    def _log_vehicle_status_event(self, current_time: float, oht_name: str, old_status: str, new_status: str, current_sec: Optional[int] = None, destination: str = "", path_length: int = 0):
        log = self._route_log()
        if log is None:
            return
        log.log_vehicle_status(
            sim_time=current_time,
            vehicle_name=oht_name,
            old_status=old_status,
            new_status=new_status,
            current_node=f"sec{current_sec}" if current_sec is not None else "",
            destination=destination,
            path_length=path_length,
        )

    def _estimate_dispatch_cost_for_oht(self, oht: OHT, target_section_id: int) -> float:
        """
        Estimate the approximate cost for a candidate OHT to reach the target section.
        Used only for comparing candidates, without changing the existing routing/bridge logic.

        [Optimization] The assignment cost is for relative comparison between candidates,
        not an absolute value, so a static (cached) route ignoring traffic penalty is used.
        path_search_static results are cached permanently, so repeated calls cost almost nothing.
        """
        if oht.current_section_id == target_section_id:
            return 0.0

        if self.bridge is not None:
            try:
                # Use the static (penalty-ignoring) cached route → no Dijkstra re-run
                _, cost, _ = self.bridge.estimate_section_route_cost_static(
                    oht.current_section_id,
                    target_section_id,
                )
                if cost is not None and cost >= 0:
                    return float(cost)
            except Exception:
                pass

        try:
            path = self._get_route_by_section(oht.current_section_id, target_section_id)
        except Exception:
            path = []
        if not path:
            return float("inf")

        total = 0.0
        for sid in path:
            idx = self.data_set.section_id_to_index.get(sid)
            if idx is None:
                total += 1.0
            else:
                section = self.data_set.sections[idx]
                total += section.length if section.length > 0 else 1.0
        return total

    def _select_repositioning_candidate_for_dispatch(self, target_section_id: int, idle_candidate: Optional[OHT] = None) -> Optional[OHT]:
        """
        Consider REPOSITIONING vehicles as additional candidates when assigning a LOT request.

        Key principles:
        - The existing idle repositioning feature is preserved.
        - The existing Dispatcher/NearestIdleStrategy is still used as is.
        - However, REPOSITIONING is really just an idle vehicle in motion, so if a nearby
          vehicle is already passing by, its idle movement is interrupted and it is put to work.
        """
        if not self.allow_repositioning_dispatch_preemption:
            return None

        repo_ohts = [oht for oht in self.oht_list.values() if oht.status == "REPOSITIONING"]
        if not repo_ohts:
            return None

        idle_cost = float("inf")
        if idle_candidate is not None:
            idle_cost = self._estimate_dispatch_cost_for_oht(idle_candidate, target_section_id)

        best_repo = None
        best_repo_cost = float("inf")
        for oht in repo_ohts:
            cost = self._estimate_dispatch_cost_for_oht(oht, target_section_id)
            if cost < best_repo_cost:
                best_repo = oht
                best_repo_cost = cost

        if best_repo is None or best_repo_cost == float("inf"):
            return None

        # Recall if there is no IDLE candidate or the REPOSITIONING candidate is closer.
        if idle_candidate is None or best_repo_cost <= idle_cost + self.repositioning_dispatch_margin:
            self._interrupt_reposition(best_repo)
            return best_repo

        return None

    def _get_rm_node_for_section(self, sec_id: int) -> Optional[str]:
        """
        Section ID → RouteManager node name.
        Prefers bridge.section_entry_node (same node naming scheme as rm.network).
        Otherwise tries buf.initial_node with the N_ prefix stripped.
        """
        bridge = self.bridge
        if bridge:
            node = bridge.section_entry_node.get(sec_id)
            if node and node in self.route_manager.network.nodes:
                return node

        # fallback: strip N_ from buf.initial_node
        idx = self.data_set.section_id_to_index.get(sec_id)
        if idx is not None:
            sec = self.data_set.sections[idx]
            if sec.oht_buffers:
                raw = sec.oht_buffers[0].initial_node
                if raw:
                    rm_node = raw.lstrip("N_") if raw.startswith("N_") else raw
                    if rm_node in self.route_manager.network.nodes:
                        return rm_node
        return None

    def _section_center(self, section: Section) -> Tuple[float, float]:
        xs: List[float] = []
        ys: List[float] = []
        for fig in getattr(section, "figures", []):
            for x_attr, y_attr in (("start_x", "start_y"), ("end_x", "end_y")):
                x = getattr(fig, x_attr, None)
                y = getattr(fig, y_attr, None)
                if x is not None and y is not None:
                    xs.append(float(x))
                    ys.append(float(y))
        if xs and ys:
            return (sum(xs) / len(xs), sum(ys) / len(ys))
        return (float(section.section_id), 0.0)

    def _build_discrete_section_order(self, sections: List[Section]) -> List[Section]:
        """
        Build a spatially distributed order using section center coordinates
        so that the initial OHTs do not cluster in one area.
        """
        valid = [sec for sec in sections if sec.oht_buffers]
        if not valid:
            return []

        centers = {sec.section_id: self._section_center(sec) for sec in valid}
        xs = [c[0] for c in centers.values()]
        ys = [c[1] for c in centers.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)

        grid_x = 4 if len(valid) >= 16 else 3
        grid_y = 4 if len(valid) >= 16 else 3
        buckets: Dict[Tuple[int, int], List[Section]] = {}

        for sec in valid:
            cx, cy = centers[sec.section_id]
            ix = min(grid_x - 1, max(0, int((cx - min_x) / span_x * grid_x)))
            iy = min(grid_y - 1, max(0, int((cy - min_y) / span_y * grid_y)))
            buckets.setdefault((ix, iy), []).append(sec)

        for key in buckets:
            buckets[key].sort(key=lambda s: (self._section_center(s)[1], self._section_center(s)[0], s.section_id))

        ordered_keys = sorted(buckets.keys(), key=lambda k: ((k[0] + k[1]) % 2, k[1], k[0]))
        ordered: List[Section] = []
        added = True
        while added:
            added = False
            for key in ordered_keys:
                bucket = buckets[key]
                if bucket:
                    ordered.append(bucket.pop(0))
                    added = True
        return ordered

    def _distribute_ohts(self):
        """
        Place the OHTs initially so that they are spatially distributed.
        The existing logging/creation flow is kept, but sections are visited in a
        discrete bin order so that the initial OHTs do not cluster in one area.
        """
        sections = self._build_discrete_section_order(self.data_set.sections)
        if not sections:
            return

        sec_idx = 0
        num_sections = len(sections)

        for oht_name, oht in self.oht_list.items():
            placed = False
            for attempt in range(num_sections):
                section = sections[(sec_idx + attempt) % num_sections]
                if not section.oht_buffers:
                    continue
                buf = section.oht_buffers[0]
                if not buf.vacant_buffer_exist():
                    continue

                buf.msg_receiver("ADD", oht_name)
                oht.update_index_in_section(section.section_id, 0, -1)
                oht.time_enter_current_section = 0.0
                oht.status = "IDLE"
                placed = True

                if self.bridge is not None:
                    self.bridge.on_oht_enter_section(section.section_id)

                if self.route_manager:
                    rm_node = self._get_rm_node_for_section(section.section_id)
                    if rm_node:
                        self.route_manager.register_vehicle(oht_name, rm_node)
                        vehicle = self.route_manager.tracker.vehicles.get(oht_name)
                        if vehicle is not None:
                            vehicle.status = "IDLE"
                sec_idx = (sec_idx + attempt + 1) % num_sections
                break

            if not placed:
                section = sections[sec_idx % num_sections]
                oht.update_index_in_section(section.section_id, 0, -1)
                oht.time_enter_current_section = 0.0
                oht.status = "IDLE"
                if self.route_manager:
                    rm_node = self._get_rm_node_for_section(section.section_id)
                    if rm_node:
                        self.route_manager.register_vehicle(oht_name, rm_node)
                        vehicle = self.route_manager.tracker.vehicles.get(oht_name)
                        if vehicle is not None:
                            vehicle.status = "IDLE"
                sec_idx = (sec_idx + 1) % num_sections

    def assign_oht(self, target_section_id: int) -> Optional[OHT]:
        """
        Find and return an IDLE OHT near target_section_id.
        Delegates to the Dispatcher if present; otherwise uses the existing logic.
        """
        # Apply the external assignment strategy first if present.
        if self.assignment_strategy is not None and invoke_assignment_strategy is not None:
            idle_ohts = [oht for oht in self.oht_list.values() if oht.status == "IDLE"]
            if idle_ohts:
                try:
                    selected = invoke_assignment_strategy(
                        self.assignment_strategy,
                        target_section_id,
                        idle_ohts,
                        self.route_manager,
                        self.bridge,
                        self,
                    )
                    selected_name = normalize_oht_selection(selected, self.oht_list) if normalize_oht_selection else None
                    if selected_name is not None:
                        selected_oht = self.oht_list.get(selected_name)
                        if selected_oht is not None and selected_oht.status == "IDLE":
                            return selected_oht
                    print(f"[ASSIGNMENT] ⚠️  custom strategy return value unusable → default assignment fallback: {selected}")
                except Exception as e:
                    print(f"[ASSIGNMENT] ⚠️  custom strategy error → default assignment fallback: {e}")

        # ✅ Use the Dispatcher (NearestIdleStrategy, etc.)
        if self.dispatcher is not None:
            oht_name = self.dispatcher.dispatch(target_section_id, self.oht_list)
            selected_idle = self.oht_list.get(oht_name) if oht_name else None

            # Extra layer: even though the default Dispatcher only looks at IDLE, REPOSITIONING is
            # idle motion, so if one is closer to the LOT request its repositioning is interrupted
            # and it is put to work.
            repo_candidate = self._select_repositioning_candidate_for_dispatch(target_section_id, selected_idle)
            if repo_candidate is not None:
                return repo_candidate

            if selected_idle is not None:
                return selected_idle

        ds = self.data_set

        # 1. IDLE OHT in the same section
        for oht in self.oht_list.values():
            if oht.status == "IDLE" and oht.current_section_id == target_section_id:
                repo_candidate = self._select_repositioning_candidate_for_dispatch(target_section_id, oht)
                return repo_candidate if repo_candidate is not None else oht

        # 2. IDLE OHT in an adjacent section
        target_idx = ds.section_id_to_index.get(target_section_id)
        if target_idx is not None:
            target_sec = ds.sections[target_idx]
            adjacent = set(target_sec.next_sections + target_sec.prev_sections)
            for oht in self.oht_list.values():
                if oht.status == "IDLE" and oht.current_section_id in adjacent:
                    repo_candidate = self._select_repositioning_candidate_for_dispatch(target_section_id, oht)
                    return repo_candidate if repo_candidate is not None else oht

        # 3. Any IDLE OHT
        for oht in self.oht_list.values():
            if oht.status == "IDLE":
                repo_candidate = self._select_repositioning_candidate_for_dispatch(target_section_id, oht)
                return repo_candidate if repo_candidate is not None else oht

        # 4. If no IDLE OHT, interrupt a REPOSITIONING OHT immediately and assign it
        #    Selected in order: same section → adjacent section → any REPOSITIONING
        for oht in self.oht_list.values():
            if oht.status == "REPOSITIONING" and oht.current_section_id == target_section_id:
                self._interrupt_reposition(oht)
                return oht

        if target_idx is not None:
            for oht in self.oht_list.values():
                if oht.status == "REPOSITIONING" and oht.current_section_id in adjacent:
                    self._interrupt_reposition(oht)
                    return oht

        for oht in self.oht_list.values():
            if oht.status == "REPOSITIONING":
                self._interrupt_reposition(oht)
                return oht

        return None

    def _interrupt_reposition(self, oht):
        """
        Interrupt a REPOSITIONING OHT immediately and switch it to IDLE.
        Called from assign_oht() when no IDLE OHT is available.
        """
        oht.status = "IDLE"
        oht.path_in_section_ids = []
        oht.destination_eq = None

        # Also sync the VehicleTracker state
        if self.route_manager:
            vehicle = self.route_manager.tracker.vehicles.get(oht.name)
            if vehicle:
                vehicle.status = "IDLE"
                vehicle.path = []
                vehicle.path_index = 0
                vehicle.destination_node = None
                vehicle.idle_since = self.route_manager.sim_time

    def get_route(self, from_sec_id: int, to_sec_id: int) -> List[int]:
        """
        Route search priority:
        1) bridge.find_section_route() — section↔node mapping + traffic_penalty applied
        2) direct route_manager search (when there is no bridge)
        3) existing section Dijkstra (fallback)
        """
        if from_sec_id == to_sec_id:
            return []

        # Apply the external routing strategy first if present.
        if self.routing_strategy is not None and invoke_routing_strategy is not None:
            try:
                custom_result = invoke_routing_strategy(
                    self.routing_strategy,
                    from_sec_id,
                    to_sec_id,
                    self,
                )
                custom_path = normalize_section_path(custom_result, from_sec_id) if normalize_section_path else None
                if custom_path:
                    valid_path = [sid for sid in custom_path if sid in self.data_set.section_id_to_index]
                    if valid_path:
                        print(f"[ROUTING] ✅ custom route: {from_sec_id} → {to_sec_id}, {len(valid_path)}hop")
                        return valid_path
                print(f"[ROUTING] ⚠️  custom route missing/invalid → default routing fallback: {from_sec_id} → {to_sec_id}")
            except Exception as e:
                print(f"[ROUTING] ⚠️  custom strategy error → default routing fallback: {e}")

        # ── 1) use bridge ─────────────────────────────────
        if self.bridge is not None:
            result = self.bridge.find_section_route(from_sec_id, to_sec_id)
            if result:
                return result
            print(f"[ROUTING] ⚠️  no bridge route → fallback: {from_sec_id} → {to_sec_id}")

        # ── 2) direct route_manager search ────────────────────
        elif self.route_manager is not None:
            result = self._get_route_via_node(from_sec_id, to_sec_id)
            if result:
                return result
            print(f"[ROUTING] ⚠️  no route-package route → fallback: {from_sec_id} → {to_sec_id}")

        # ── 3) fallback: existing section Dijkstra ───────────────
        return self._get_route_by_section(from_sec_id, to_sec_id)

    def _get_route_via_node(self, from_sec_id: int, to_sec_id: int) -> List[int]:
        """Node-based route search via the route package RouteManager, returning a list of section IDs"""
        rm = self.route_manager
        from_node = self._sec_id_to_node(from_sec_id)
        to_node   = self._sec_id_to_node(to_sec_id)
        print(f"[ROUTING]   sec {from_sec_id} → node '{from_node}' / sec {to_sec_id} → node '{to_node}'")

        if not from_node or not to_node:
            return []

        node_path = rm.get_route(from_node, to_node)
        print(f"[ROUTING]   node_path: {node_path}")
        if not node_path:
            return []

        section_names = rm.node_path_to_section_path(node_path)
        print(f"[ROUTING]   section_names: {section_names}")

        result = []
        for name in section_names:
            sec_id = self._network_section_name_to_sec_id(name)
            if sec_id is not None and sec_id not in result:
                result.append(sec_id)
        return result

    def _sec_id_to_node(self, sec_id: int) -> Optional[str]:
        """Section ID → RouteManager node name (EQ mapping first, else the buffer initial_node)"""
        idx = self.data_set.section_id_to_index.get(sec_id)
        if idx is None:
            return None
        section = self.data_set.sections[idx]
        if section.eq_list:
            node = self.route_manager.get_eq_node(section.eq_list[0].name)
            if node:
                return node
        if section.oht_buffers:
            return section.oht_buffers[0].initial_node
        return None

    def _network_section_name_to_sec_id(self, name: str) -> Optional[int]:
        """Convert a NetworkSection name (numeric string) → section_id in ds.sections"""
        try:
            sec_id = int(name)
            if sec_id in self.data_set.section_id_to_index:
                return sec_id
        except ValueError:
            pass
        return None

    def _get_route_by_section(self, from_sec_id: int, to_sec_id: int) -> List[int]:
        """Existing section-level Dijkstra"""
        ds = self.data_set
        sec_index = ds.section_id_to_index

        all_ids = set(sec_index.keys())
        if from_sec_id not in all_ids or to_sec_id not in all_ids:
            return []

        dist: Dict[int, float] = {sid: float('inf') for sid in all_ids}
        prev: Dict[int, int] = {}
        dist[from_sec_id] = 0.0

        pq: List[Tuple[float, int]] = [(0.0, from_sec_id)]

        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            if u == to_sec_id:
                break

            idx = sec_index.get(u)
            if idx is None:
                continue
            section = ds.sections[idx]

            for next_id in section.next_sections:
                next_idx = sec_index.get(next_id)
                if next_idx is None:
                    continue
                next_sec = ds.sections[next_idx]
                cost = next_sec.length if next_sec.length > 0 else 1.0
                new_dist = dist[u] + cost
                if new_dist < dist.get(next_id, float('inf')):
                    dist[next_id] = new_dist
                    prev[next_id] = u
                    heapq.heappush(pq, (new_dist, next_id))

        if to_sec_id not in prev and from_sec_id != to_sec_id:
            return []

        path = []
        cur = to_sec_id
        while cur != from_sec_id:
            path.append(cur)
            cur = prev.get(cur)
            if cur is None:
                return []
        path.reverse()
        return path


# ─── OHT default speed (mm/s) ───
OHT_SPEED = 1000.0


class EventHandler:
    def __init__(self, vehicle_controller: VehicleController):
        self.vc = vehicle_controller
        self.ds = SimulatorDataSet.get_instance()
        # ── Reposition cooldown (C) ──
        # Time each OHT last became IDLE.
        # Requirement: IDLE OHTs must not sit still on the rail (blocking/collisions).
        # So the cooldown is kept to the bare minimum needed to "secure assignment priority".
        # If a nearby LOT arrives during this time, assign_oht grabs the OHT.
        self._oht_idle_since: Dict[str, float] = {}
        # Minimum wait (seconds) after becoming IDLE before repositioning is allowed
        # Kept short to satisfy the "keep moving" requirement.
        self.REPOSITION_COOLDOWN: float = 0.0
        # Retry interval for IDLE/idle movement
        self.REPOSITION_RETRY_DELAY: float = 0.35
        # Round-robin pointer for reposition_idle_ohts()
        self._repo_rr_index: int = 0
        # Keep a recent-visit history so idle repositioning does not ping-pong between two sections
        self._repo_recent_sections: Dict[str, deque] = defaultdict(lambda: deque(maxlen=8))
        self.REPOSITION_BACKTRACK_PENALTY: float = 100.0
        self.REPOSITION_RECENT_PENALTY: float = 12.0
        self.idle_positioning_strategy = None  # external idle positioning strategy; existing logic if None
        # Hybrid simulator mode. from_to_only keeps the existing behaviour.
        self.simulation_mode: str = "from_to_only"
        self.default_processing_time: float = 60.0
        self._lot_process_start_times: Dict[str, float] = {}

    def configure_simulation_mode(self, simulation_mode: str = "from_to_only", default_processing_time: float = 60.0):
        """Configure the simulation mode.

        - from_to_only: keeps the existing From-To logistics-centric behaviour
        - production_logistics: adds a PROCESS_END event after delivery and
          records the rundown wait_start relative to the request time
        """
        self.simulation_mode = simulation_mode or "from_to_only"
        self.default_processing_time = max(0.0, float(default_processing_time))

    def set_idle_positioning_strategy(self, idle_positioning_strategy=None):
        """Inject the external idle positioning strategy selected in the UI at simulation start."""
        self.idle_positioning_strategy = idle_positioning_strategy

    # ── Idle Repositioning ─────────────────────────────────
    def reposition_idle_ohts(self, current_time: float):
        """
        Repositions IDLE OHTs, but the real meaning is "idle vehicles keep moving".
        That is, IDLE and REPOSITIONING differ only in status and are treated as the same
        idle-movement flow.

        [Optimization] Limits the number of OHTs handled per call (at most MAX_REPO_PER_CALL).
        Because it is round-robin, every IDLE OHT is handled in turn.
        Prevents the GUI from freezing when dozens of vehicles call get_route at once on
        large layouts.
        """
        rm = self.vc.route_manager
        bridge = self.vc.bridge
        if rm is None or bridge is None:
            return

        MAX_REPO_PER_CALL = 10  # maximum number of IDLE OHTs handled per call

        idle_like_names: List[str] = []
        for name, oht in self.vc.oht_list.items():
            if oht.status != "IDLE":
                continue
            idle_since = self._oht_idle_since.get(name, 0.0)
            if current_time - idle_since < self.REPOSITION_COOLDOWN:
                continue
            idle_like_names.append(name)

        if not idle_like_names:
            return

        n = len(idle_like_names)
        start = self._repo_rr_index % n
        ordered = idle_like_names[start:] + idle_like_names[:start]
        self._repo_rr_index = (self._repo_rr_index + 1) % max(n, 1)

        # Handle only MAX_REPO_PER_CALL per call (the rest on the next call)
        for name in ordered[:MAX_REPO_PER_CALL]:
            oht = self.vc.oht_list.get(name)
            if oht is None:
                continue
            self._try_reposition(oht, current_time)

    # ── FromTo → bulk LOT event registration ──────────────────────
    def init_lot_events(self, fromto_data: List[Tuple[str, str, float]],
                        sim_duration: float = 3600.0):
        """
        fromto_data: [(from_eq, to_eq, rate_per_hour), ...]
        Register LOT events in bulk at a fixed interval (Δt = 3600 / rate seconds)
        based on the hourly rate (count/hour).
        """
        valid_records = [
            (fe, te, float(r)) for fe, te, r in fromto_data
            if fe in self.ds.eq_list and te in self.ds.eq_list and float(r) > 0
        ]

        if not valid_records:
            print("[EventHandler] No valid FromTo records. "
                  "Check that the EQ names match the layout.")
            return

        events = generate_fixed_interval_events(valid_records, sim_duration)

        for sim_t, from_eq, to_eq in events:
            evt = Event(
                time_scheduled=sim_t,
                event_type="LOT",
                from_node=from_eq,
                to_node=to_eq,
            )
            self.ds.add_event(evt)
            self.ds.increase_lot_count()

        print(f"[EventHandler] Registered {len(events)} LOT events "
              f"(fixed-interval replay, {len(valid_records)} valid records, "
              f"{len(fromto_data) - len(valid_records)} skipped) "
              f"[simulation time: {sim_duration:.1f}s = {sim_duration/3600:.2f}h]")

    # ── Main event dispatcher ────────────────────────────────
    def process_event(self, event: Event):
        if event.event_type == "LOT":
            self._handle_lot(event)
        elif event.event_type == "TRANSFER_EXT":
            self._handle_transfer_ext(event)
        elif event.event_type == "PROCESS_END":
            self._handle_process_end(event)

    # ── LOT event ─────────────────────────────────────────
    def _handle_lot(self, event: Event):
        from_eq_name = event.from_node
        to_eq_name = event.to_node
        current_time = event.time_scheduled

        from_eq = self.ds.eq_list.get(from_eq_name)
        if from_eq is None:
            return

        to_eq = self.ds.eq_list.get(to_eq_name)
        if to_eq is None:
            return

        # Assign an IDLE OHT
        oht = self.vc.assign_oht(from_eq.section_id)

        if oht is None:
            self.vc._log_dispatch_decision(current_time, from_eq.section_id, None, "RETRY_NO_OHT")
            # Retry after 5 seconds
            retry = event.clone()
            retry.time_scheduled = current_time + 5.0
            self.ds.add_event(retry)
            return

        dispatch_reason = "DISPATCHED_IDLE" if oht.status == "IDLE" else f"DISPATCHED_{oht.status}"
        self.vc._log_dispatch_decision(current_time, from_eq.section_id, oht, dispatch_reason)

        # Create the lot info & pass it to the EQ
        # Build a unique id from the processed lot count + current time
        lot_serial = getattr(self.ds, '_lot_serial', 0) + 1
        self.ds._lot_serial = lot_serial
        lot_id = f"LOT_{lot_serial:05d}"
        lot_info = f"{lot_id}@{to_eq_name}@{current_time:.2f}"
        from_eq.message_receiver("LOT_CREATED", lot_info, self.vc)

        # Logger record
        get_logger().on_lot_created(lot_id, from_eq_name, to_eq_name, current_time)

        # In production+logistics mode, record the request time as the equipment rundown start.
        # The measurement method of the existing from-to only mode is kept.
        if self.simulation_mode == "production_logistics":
            to_eq.message_receiver("LOT_INBOUND", lot_info, self.vc)
            get_logger().on_eq_rundown_wait_start(
                to_eq_name, lot_id, current_time, "", wait_origin="REQUEST"
            )

        # OHT → ASSIGNED
        old_status = oht.status
        oht.status = "ASSIGNED"
        get_logger().on_oht_status_change(oht.name, "ASSIGNED", current_time)
        oht.destination_eq = from_eq_name
        oht.loaded_lot_info = None
        self.vc._log_vehicle_status_event(current_time, oht.name, old_status, "ASSIGNED", oht.current_section_id, f"eq:{from_eq_name}")

        # Notify the EQ of the OHT assignment (eq_status → OHT_COMING)
        from_eq.message_receiver("OHT_ASSIGNED", oht.name, self.vc)

        # Route: OHT current position → from_eq section
        path = self.vc.get_route(oht.current_section_id, from_eq.section_id)
        oht.path_in_section_ids = path
        log = self.vc._route_log()
        if log is not None:
            log.log_vehicle_assign(
                current_time, oht.name, f"sec{oht.current_section_id}", f"sec{from_eq.section_id}", len(path), 0.0
            )

        self._schedule_next_transfer(oht, current_time)

    # ── TRANSFER_EXT event ────────────────────────────────
    def _handle_transfer_ext(self, event: Event):
        oht = self.vc.oht_list.get(event.oht_id)
        if oht is None:
            return

        current_time = event.time_scheduled

        # ── Stale event blocking ──────────────────────────────
        # When switching REPOSITIONING → ASSIGNED, a TRANSFER_EXT from the previous route
        # left in the queue would fire late and move the OHT to the wrong section.
        # If the to_node at event creation differs from the current path[0], the event is invalid.
        if event.to_node:
            if oht.path_in_section_ids:
                if event.to_node != str(oht.path_in_section_ids[0]):
                    return  # route was replaced → ignore the stale event
            else:
                # path is empty but to_node is set
                # REPOSITIONING completed normally → handled by _handle_arrival
                # stale after switching to ASSIGNED/LOADED → ignore
                if oht.status in ("ASSIGNED", "LOADED"):
                    return  # ignore the stale event

        if oht.path_in_section_ids:
            next_sec_id = oht.path_in_section_ids[0]
            next_idx = self.ds.section_id_to_index.get(next_sec_id)
            if next_idx is None:
                return
            next_section = self.ds.sections[next_idx]

            if not next_section.oht_buffers:
                return
            entry_buf = next_section.oht_buffers[0]

            if not entry_buf.vacant_buffer_exist():
                self.vc._log_vehicle_move_event(current_time, oht.name, oht.current_section_id, next_sec_id, False, "ENTRY_BUFFER_FULL")
                # Idle movement should not insist on a blocked route; immediately re-check other 1-hop candidates.
                if oht.status == "REPOSITIONING":
                    alt_next = self._pick_idle_next_hop_section(oht)
                    if alt_next is not None and alt_next != next_sec_id:
                        oht.path_in_section_ids = [alt_next]
                        retry = event.clone()
                        retry.to_node = str(alt_next)
                        retry.time_scheduled = current_time + self.REPOSITION_RETRY_DELAY
                        self.ds.add_event(retry)
                        return
                retry = event.clone()
                retry.time_scheduled = current_time + self.REPOSITION_RETRY_DELAY
                self.ds.add_event(retry)
                return

            # Remove from the current section
            cur_idx = self.ds.section_id_to_index.get(oht.current_section_id)
            if cur_idx is not None:
                cur_section = self.ds.sections[cur_idx]
                if cur_section.oht_buffers:
                    cur_section.message_receiver(
                        "MOVE_TO_NEXT_SECTION",
                        next_section,
                        oht.current_buffer_index,
                        0,
                        oht.name,
                    )

            # Update the OHT position + log distance
            get_logger().on_oht_moved(oht.name, next_section.length)
            old_sec_id = oht.current_section_id          # ✅ save the previous section
            self.vc._log_vehicle_move_event(current_time, oht.name, old_sec_id, next_sec_id, True, "")
            oht.current_section_id = next_sec_id
            oht.current_buffer_index = 0
            oht.time_enter_current_section = current_time
            oht.path_in_section_ids.pop(0)

            if oht.status == "REPOSITIONING":
                self._remember_repo_section(oht.name, next_sec_id)

            # ✅ update the bridge penalty (reflects section congestion)
            if self.vc.bridge is not None:
                self.vc.bridge.on_oht_leave_section(old_sec_id)
                self.vc.bridge.on_oht_enter_section(next_sec_id)

            # Sync VehicleTracker current_node (bridge node naming)
            if self.vc.route_manager is not None:
                new_rm_node = self.vc._get_rm_node_for_section(next_sec_id)
                if new_rm_node:
                    vehicle = self.vc.route_manager.tracker.vehicles.get(oht.name)
                    if vehicle:
                        vehicle.current_node = new_rm_node

            if oht.path_in_section_ids:
                travel_time = max(next_section.length / OHT_SPEED, 0.1)
                self._schedule_transfer_ext(oht, current_time + travel_time)
                return

        # Route exhausted → arrival handling
        # If the route is empty during REPOSITIONING but this is not a destination arrival,
        # safely return to IDLE
        if oht.status == "REPOSITIONING" and not oht.path_in_section_ids:
            pass  # handled by the REPOSITIONING case in _handle_arrival
        self._handle_arrival(oht, current_time)

    # ── Arrival handling ──────────────────────────────────────────
    def _handle_arrival(self, oht: OHT, current_time: float):
        if oht.status == "ASSIGNED":
            # Arrived at from_eq → pick up the lot
            eq = self.ds.eq_list.get(oht.destination_eq)
            if eq and eq.port_buffer[0] is not None:
                oht.loaded_lot_info = eq.port_buffer[0]
                eq.message_receiver("LOT_TRANSPORTED", oht.name, self.vc)

            # Logger: record the pickup time
            if oht.loaded_lot_info:
                lot_id = oht.loaded_lot_info.split("@")[0]
                get_logger().on_lot_picked_up(lot_id, current_time)

            old_status = oht.status
            oht.status = "LOADED"
            get_logger().on_oht_status_change(oht.name, "LOADED", current_time)
            self.vc._log_vehicle_status_event(current_time, oht.name, old_status, "LOADED", oht.current_section_id, "lot_loaded")

            to_eq_name = self._extract_to_eq(oht.loaded_lot_info)
            oht.destination_eq = to_eq_name

            to_eq = self.ds.eq_list.get(to_eq_name)
            if to_eq is None:
                old_status = oht.status
                oht.status = "IDLE"
                oht.loaded_lot_info = None
                self.vc._log_vehicle_status_event(current_time, oht.name, old_status, "IDLE", oht.current_section_id, "invalid_to_eq")
                return

            # Only in Production + Logistics mode is the destination EQ's production-wait state shown.
            # In From-To only / logistics-KPI-only mode no processing or rundown concept is created;
            # only the transport event of the OHT bringing the lot is performed.
            lot_id = oht.loaded_lot_info.split("@")[0] if oht.loaded_lot_info else ""
            if self.simulation_mode == "production_logistics":
                # LOT_INBOUND and the rundown wait are already opened at request time.
                # No further state change here, to avoid duplicate rundown records.
                pass

            path = self.vc.get_route(oht.current_section_id, to_eq.section_id)
            oht.path_in_section_ids = path
            log = self.vc._route_log()
            if log is not None:
                log.log_vehicle_assign(
                    current_time, oht.name, f"sec{oht.current_section_id}", f"sec{to_eq.section_id}", len(path), 0.0
                )
            self._schedule_next_transfer(oht, current_time)

        elif oht.status == "LOADED":
            # Arrived at to_eq → delivery complete
            delivered_eq_name = self._extract_to_eq(oht.loaded_lot_info)
            delivered_eq = self.ds.eq_list.get(delivered_eq_name) if delivered_eq_name else None

            # Logger: record delivery completion. Equipment processing start/end is recorded only in
            # Production + Logistics mode.
            if oht.loaded_lot_info:
                lot_id = oht.loaded_lot_info.split("@")[0]
                to_eq_from_lot = delivered_eq_name
                get_logger().on_lot_delivered(lot_id, to_eq_from_lot, current_time)
                if self.simulation_mode == "production_logistics":
                    get_logger().on_eq_process_start(delivered_eq_name, lot_id, current_time, oht.name)
                    self._lot_process_start_times[lot_id] = current_time
                    self.ds.add_event(Event(
                        time_scheduled=current_time + self.default_processing_time,
                        event_type="PROCESS_END",
                        from_node=delivered_eq_name or "",
                        to_node=lot_id,
                        time_enter_section=current_time,
                    ))
            get_logger().on_oht_status_change(oht.name, "IDLE", current_time)

            if delivered_eq is not None and self.simulation_mode == "production_logistics":
                delivered_eq.message_receiver("LOT_DELIVERED", oht.loaded_lot_info or oht.name, self.vc)

            old_status = oht.status
            oht.status = "IDLE"
            self.vc._log_vehicle_status_event(current_time, oht.name, old_status, "IDLE", oht.current_section_id, "delivery_complete")
            oht.loaded_lot_info = None
            oht.destination_eq = None
            oht.path_in_section_ids = []
            self.ds.plus_num_of_processed_lot()

            # Immediately queue as an idle-movement candidate so the rail is not blocked after delivery.
            self._oht_idle_since[oht.name] = current_time - self.REPOSITION_COOLDOWN
            self._try_reposition(oht, current_time)

        elif oht.status == "REPOSITIONING":
            # Arrived at the repositioning destination → return to IDLE
            old_status = oht.status
            oht.status = "IDLE"
            oht.path_in_section_ids = []
            get_logger().on_oht_status_change(oht.name, "IDLE", current_time)
            self.vc._log_vehicle_status_event(current_time, oht.name, old_status, "IDLE", oht.current_section_id, "reposition_arrived")

            # Also sync the route_manager VehicleTracker state
            rm = self.vc.route_manager
            if rm is not None:
                vehicle = rm.tracker.vehicles.get(oht.name)
                if vehicle:
                    vehicle.status = "IDLE"
                    vehicle.idle_since = current_time
                    vehicle.path = []
                    vehicle.path_index = 0
                    vehicle.destination_node = None

            # REPOSITIONING is just idle movement in progress, so retry the next move immediately on arrival.
            self._oht_idle_since[oht.name] = current_time - self.REPOSITION_COOLDOWN
            self._try_reposition(oht, current_time)

    def _handle_process_end(self, event: Event):
        """Record the end of equipment processing in production+logistics mode.

        This event does not affect the existing From-To transport logic; it only additionally
        records the equipment state and processing duration for production KPI computation.
        """
        if self.simulation_mode != "production_logistics":
            return
        eq_name = event.from_node
        lot_id = event.to_node
        current_time = event.time_scheduled
        eq = self.ds.eq_list.get(eq_name)
        process_start_time = self._lot_process_start_times.pop(lot_id, event.time_enter_section)
        get_logger().on_eq_process_end(eq_name, lot_id, process_start_time, current_time)

        if eq is not None and getattr(eq, "processing_lot_id", None) == lot_id:
            eq.processing_lot_id = None
            # Stay in PROCESS_WAITING if another inbound lot exists; otherwise return to IDLE
            if getattr(eq, "inbound_lot_count", 0) > 0:
                eq.eq_status = "PROCESS_WAITING"
            else:
                eq.eq_status = "IDLE"

    # ── Idle repositioning for a single OHT ──────────────────────
    def _sec_id_to_rm_node(self, sec_id: int, rm) -> Optional[str]:
        """Convert section ID → rm.network node name (including N_ prefix handling)"""
        node = self._sec_id_to_node(sec_id)
        if node is None:
            return None
        # Strip the N_ prefix
        rm_node = node.lstrip("N_") if node.startswith("N_") else node
        if rm_node in rm.network.nodes:
            return rm_node
        # Also try the original
        if node in rm.network.nodes:
            return node
        return None

    def _remember_repo_section(self, oht_name: str, sec_id: Optional[int]):
        if sec_id is None:
            return
        hist = self._repo_recent_sections[oht_name]
        if not hist or hist[-1] != sec_id:
            hist.append(sec_id)

    def _last_repo_section(self, oht_name: str) -> Optional[int]:
        hist = self._repo_recent_sections.get(oht_name)
        if hist and len(hist) >= 2:
            return hist[-2]
        return None

    def _pick_idle_next_hop_section(self, oht: OHT) -> Optional[int]:
        """
        Pick the best adjacent section an idle vehicle can move to immediately, even by one hop.
        Key points:
          - Large penalty for backtracking to the immediately previous section
          - Penalty for revisiting recently visited sections
          - Prefer next_sections; consider prev_sections only when truly blocked
        """
        bridge = self.vc.bridge
        ds = self.vc.data_set
        if bridge is None:
            return None

        idx = ds.section_id_to_index.get(oht.current_section_id)
        if idx is None:
            return None
        sec = ds.sections[idx]

        self._remember_repo_section(oht.name, oht.current_section_id)

        heading_sections = set()
        for other in self.vc.oht_list.values():
            if other.name == oht.name:
                continue
            if other.status == "REPOSITIONING" and other.path_in_section_ids:
                heading_sections.add(other.path_in_section_ids[0])

        cur_cong = bridge.get_section_congestion(oht.current_section_id)
        recent_hist = list(self._repo_recent_sections.get(oht.name, []))
        last_sec = recent_hist[-2] if len(recent_hist) >= 2 else None
        recent_set = set(recent_hist[:-1]) if len(recent_hist) >= 2 else set()

        def score(candidate_sec_id: int, hop_bias: float, allow_backtrack: bool) -> Optional[Tuple[float, int]]:
            cand_idx = ds.section_id_to_index.get(candidate_sec_id)
            if cand_idx is None:
                return None
            cand_sec = ds.sections[cand_idx]
            if not cand_sec.oht_buffers or not cand_sec.oht_buffers[0].vacant_buffer_exist():
                return None

            cong = bridge.get_section_congestion(candidate_sec_id)
            s = cong * 10.0 + hop_bias

            if cong <= cur_cong:
                s -= 3.0
            if candidate_sec_id in heading_sections:
                s += 4.0
            if last_sec is not None and candidate_sec_id == last_sec:
                if not allow_backtrack:
                    return None
                s += self.REPOSITION_BACKTRACK_PENALTY
            elif candidate_sec_id in recent_set:
                s += self.REPOSITION_RECENT_PENALTY

            return (s, candidate_sec_id)

        next_candidates: List[Tuple[float, int]] = []
        for nxt_id in sec.next_sections:
            cand = score(nxt_id, 0.0, allow_backtrack=False)
            if cand is not None:
                next_candidates.append(cand)

        # If forward candidates exist, prev is not considered
        if next_candidates:
            next_candidates.sort(key=lambda x: x[0])
            return next_candidates[0][1]

        prev_candidates: List[Tuple[float, int]] = []
        for prev_id in sec.prev_sections:
            cand = score(prev_id, 1.0, allow_backtrack=True)
            if cand is not None:
                prev_candidates.append(cand)

        if not prev_candidates:
            return None
        prev_candidates.sort(key=lambda x: x[0])
        return prev_candidates[0][1]

    def _pick_reposition_target(self, oht, current_node: str, rm, bridge) -> Optional[str]:
        """
        Select the repositioning destination node for an IDLE OHT — BFS over next_sections.

        Background:
          - The previous `pathfinder.path_search`-based approach often returned "no route",
            leaving IDLE OHTs stopped on the rail (a cause of collisions/blocking)
          - BFS over the simulator's actual connectivity (next_sections) finds routes far
            more often and guarantees an actually drivable route.

        Strategy:
          1) Expand the section graph by BFS from the current section along next_sections
          2) Collect visited sections whose congestion is "at or below the current" as candidates
          3) If a less congested section exists, choose it as the destination
          4) Otherwise choose the nearest section with "the same congestion as now" (always move)
          5) Weighted penalty for sections other REPOSITIONING OHTs are already heading to
          6) If there is still no candidate (= isolated), any adjacent next_section
        """
        from collections import deque

        ds = self.vc.data_set
        cur_sec_id = oht.current_section_id
        cur_congestion = bridge.get_section_congestion(cur_sec_id)

        # Destination sections of other REPOSITIONING OHTs (penalty to avoid crowding)
        heading_sections = set()
        for other in self.vc.oht_list.values():
            if other.name == oht.name:
                continue
            if other.status == "REPOSITIONING" and other.path_in_section_ids:
                heading_sections.add(other.path_in_section_ids[-1])

        # Collect reachable sections by BFS (hop-distance limited)
        # Going too far causes congestion instead → usually 5–15 hops is enough to reach another area
        MAX_HOPS = 12
        visited = {cur_sec_id: 0}
        queue = deque([cur_sec_id])

        # (score, sec_id, hop) tuples. Lower score is better
        candidates: List[Tuple[float, int, int]] = []

        while queue:
            sid = queue.popleft()
            hop = visited[sid]
            if hop >= MAX_HOPS:
                continue

            idx = ds.section_id_to_index.get(sid)
            if idx is None:
                continue
            sec = ds.sections[idx]

            for nxt_id in sec.next_sections:
                if nxt_id in visited:
                    continue
                visited[nxt_id] = hop + 1
                queue.append(nxt_id)

                # Candidate evaluation: lower congestion + closer is better
                nxt_cong = bridge.get_section_congestion(nxt_id)
                # Score: congestion * 10 + hop distance + heading-overlap penalty
                score = nxt_cong * 10.0 + hop + 1
                if nxt_id in heading_sections:
                    score += 5.0  # less attractive where another OHT is already heading

                # The entry node must exist in rm.network for route conversion
                entry_node = bridge.section_entry_node.get(nxt_id)
                if entry_node and entry_node in rm.network.nodes:
                    candidates.append((score, nxt_id, hop + 1))

        if not candidates:
            # Fully isolated: next_sections is empty or every search failed
            # The rail structure itself is blocked, so movement is truly impossible
            return None

        # Sort by ascending score
        candidates.sort(key=lambda x: x[0])

        # Priority 1: best-scoring section less congested than the current one
        for score, sec_id, hop in candidates:
            cong = bridge.get_section_congestion(sec_id)
            if cong < cur_congestion:
                entry_node = bridge.section_entry_node.get(sec_id)
                if entry_node:
                    return entry_node

        # Priority 2: sections with the same congestion as the current one (must move even at the same level)
        # — Requirement: IDLE must never sit still. Move even at equal congestion.
        for score, sec_id, hop in candidates:
            cong = bridge.get_section_congestion(sec_id)
            if cong <= cur_congestion:
                entry_node = bridge.section_entry_node.get(sec_id)
                if entry_node:
                    return entry_node

        # Priority 3: simply the lowest-scoring section (move anyway even if all are more congested)
        _, best_sec_id, _ = candidates[0]
        return bridge.section_entry_node.get(best_sec_id)

    def _target_section_to_path(self, oht: OHT, target_sec_id: Optional[int]) -> Optional[List[int]]:
        if target_sec_id is None or target_sec_id == oht.current_section_id:
            return None
        if target_sec_id not in self.vc.data_set.section_id_to_index:
            return None
        path = self.vc.get_route(oht.current_section_id, target_sec_id)
        path = [sid for sid in path if sid in self.vc.data_set.section_id_to_index]
        return path or None

    def _valid_custom_idle_path(self, oht: OHT, path: Optional[List[int]]) -> Optional[List[int]]:
        if not path:
            return None
        valid = [sid for sid in path if sid in self.vc.data_set.section_id_to_index]
        if not valid:
            return None
        if valid[0] == oht.current_section_id:
            valid = valid[1:]
        return valid or None

    def _resolve_custom_idle_result(self, result, oht: OHT, bridge) -> Optional[Tuple[List[int], int]]:
        """Convert an idle positioning strategy return value into the existing REPOSITIONING path."""
        if result is None:
            return None

        # dict return: path or target section/node accepted
        if isinstance(result, dict):
            path = normalize_section_path(result, oht.current_section_id) if normalize_section_path else None
            path = self._valid_custom_idle_path(oht, path)
            if path:
                return path, path[-1]

            for key in ("target_section_id", "target_sec_id", "section_id", "sec_id"):
                if key in result:
                    try:
                        target_sec_id = int(result[key])
                    except (TypeError, ValueError):
                        return None
                    path = self._target_section_to_path(oht, target_sec_id)
                    return (path, target_sec_id) if path else None

            for key in ("target_node", "node", "target"):
                if key in result and isinstance(result[key], str):
                    target_sec_id = bridge.node_to_section.get(result[key])
                    path = self._target_section_to_path(oht, target_sec_id)
                    return (path, target_sec_id) if path and target_sec_id is not None else None
            return None

        # int return: destination section id
        if isinstance(result, int):
            path = self._target_section_to_path(oht, result)
            return (path, result) if path else None

        # str return: target node or 'sec123'
        if isinstance(result, str):
            text = result.strip()
            if text.lower().startswith("sec"):
                try:
                    target_sec_id = int(text[3:])
                except ValueError:
                    return None
            else:
                target_sec_id = bridge.node_to_section.get(text)
            path = self._target_section_to_path(oht, target_sec_id)
            return (path, target_sec_id) if path and target_sec_id is not None else None

        # list/tuple return: treated as a section path
        path = normalize_section_path(result, oht.current_section_id) if normalize_section_path else None
        path = self._valid_custom_idle_path(oht, path)
        if path:
            return path, path[-1]
        return None

    def _try_reposition(self, oht: OHT, current_time: float):
        """
        Idle vehicles (IDLE) always try to move.
        REPOSITIONING is just IDLE in motion, not a separate policy.
        """
        rm = self.vc.route_manager
        bridge = self.vc.bridge
        if rm is None or bridge is None:
            return
        if oht.status not in ("IDLE", "REPOSITIONING"):
            return
        if oht.loaded_lot_info is not None or oht.destination_eq is not None:
            return

        vehicle = rm.tracker.vehicles.get(oht.name)
        if vehicle is None or vehicle.current_node is None:
            cur_node = self.vc._get_rm_node_for_section(oht.current_section_id)
            if cur_node is None:
                cur_node = self._sec_id_to_rm_node(oht.current_section_id, rm)
            if cur_node is None:
                self._oht_idle_since[oht.name] = current_time - self.REPOSITION_COOLDOWN + self.REPOSITION_RETRY_DELAY
                return
            if vehicle is None:
                rm.tracker.register_vehicle(oht.name, cur_node, status="IDLE")
                vehicle = rm.tracker.vehicles.get(oht.name)
            else:
                vehicle.current_node = cur_node

        self._remember_repo_section(oht.name, oht.current_section_id)

        custom_plan = None
        if self.idle_positioning_strategy is not None and invoke_idle_positioning_strategy is not None:
            try:
                custom_result = invoke_idle_positioning_strategy(
                    self.idle_positioning_strategy,
                    oht,
                    current_time,
                    vehicle.current_node,
                    rm,
                    bridge,
                    self,
                    self.vc,
                )
                custom_plan = self._resolve_custom_idle_result(custom_result, oht, bridge)
                if custom_plan is None:
                    print(f"[REPO] ⚠️  custom idle positioning return value invalid → default repositioning fallback: {custom_result}")
            except Exception as e:
                print(f"[REPO] ⚠️  custom idle positioning error → default repositioning fallback: {e}")

        if custom_plan is not None:
            sec_id_path, target_sec_id = custom_plan
        else:
            next_hop_sec = self._pick_idle_next_hop_section(oht)
            if next_hop_sec is not None:
                sec_id_path = [next_hop_sec]
                target_sec_id = next_hop_sec
            else:
                target_node = self._pick_reposition_target(oht, vehicle.current_node, rm, bridge)
                if not target_node:
                    self._oht_idle_since[oht.name] = current_time - self.REPOSITION_COOLDOWN + self.REPOSITION_RETRY_DELAY
                    return
                target_sec_id = bridge.node_to_section.get(target_node)
                if target_sec_id is None:
                    self._oht_idle_since[oht.name] = current_time - self.REPOSITION_COOLDOWN + self.REPOSITION_RETRY_DELAY
                    return
                sec_id_path = self.vc.get_route(oht.current_section_id, target_sec_id)
                sec_id_path = [s for s in sec_id_path if s in self.vc.data_set.section_id_to_index]
                if not sec_id_path:
                    self._oht_idle_since[oht.name] = current_time - self.REPOSITION_COOLDOWN + self.REPOSITION_RETRY_DELAY
                    return

        idle_elapsed = rm.tracker.flush_idle_time(oht.name)
        old_status = oht.status
        oht.status = "REPOSITIONING"
        oht.path_in_section_ids = sec_id_path
        oht.destination_eq = None
        if vehicle:
            vehicle.status = "REPOSITIONING"

        self.vc._log_vehicle_status_event(current_time, oht.name, old_status, "REPOSITIONING", oht.current_section_id, f"sec{target_sec_id}", len(sec_id_path))
        log = self.vc._route_log()
        if log is not None:
            log.log_vehicle_reposition(
                current_time, oht.name, f"sec{oht.current_section_id}", f"sec{target_sec_id}", len(sec_id_path), idle_elapsed
            )

        if _REPO_VERBOSE:
            cur_cong = bridge.get_section_congestion(oht.current_section_id)
            tgt_cong = bridge.get_section_congestion(target_sec_id)
            print(f"[REPO] {oht.name} sec{oht.current_section_id}(c={cur_cong}) "
                  f"→ sec{target_sec_id}(c={tgt_cong}), {len(sec_id_path)}hop")
        self._schedule_next_transfer(oht, current_time)

    # ── Utilities ───────────────────────────────────────────
    def _schedule_next_transfer(self, oht: OHT, current_time: float):
        if not oht.path_in_section_ids:
            self._handle_arrival(oht, current_time)
            return

        cur_idx = self.ds.section_id_to_index.get(oht.current_section_id)
        travel_time = 0.1
        if cur_idx is not None:
            cur_sec = self.ds.sections[cur_idx]
            travel_time = max(cur_sec.length / OHT_SPEED, 0.1)

        self._schedule_transfer_ext(oht, current_time + travel_time)

    def _schedule_transfer_ext(self, oht: OHT, time: float):
        evt = Event(
            time_scheduled=time,
            event_type="TRANSFER_EXT",
            oht_id=oht.name,
            from_node=str(oht.current_section_id),
            to_node=str(oht.path_in_section_ids[0]) if oht.path_in_section_ids else "",
        )
        self.ds.add_event(evt)

    @staticmethod
    def _extract_to_eq(lot_info: Optional[str]) -> str:
        """Extract toEQ from lot_info 'LOT_n@toEQ@time'"""
        if not lot_info:
            return ""
        parts = lot_info.split("@")
        return parts[1] if len(parts) >= 2 else ""
