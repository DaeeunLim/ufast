"""
bridge.py - node ↔ section bridge

The glue layer between the node-level RouteManager and the section-level simulator.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ufast.core.data_set import SimulatorDataSet

from .route_manager import RouteManager


class SectionNodeBridge:
    def __init__(self, route_manager: RouteManager, data_set=None):
        self.rm = route_manager
        self.ds = data_set
        self.section_to_nodes: Dict[int, List[str]] = {}
        self.node_to_section: Dict[str, int] = {}
        self.section_entry_node: Dict[int, str] = {}
        self.section_exit_node: Dict[int, str] = {}
        self._section_oht_count: Dict[int, int] = {}
        self.penalty_per_oht: float = 5.0
        self.base_penalty: float = 1.0
        self._transition_nodes_cache: Dict[Tuple[int, int], List[str]] = {}
        self._transition_zones_cache: Dict[Tuple[int, int], List[str]] = {}

        # ── Section-to-section route cache (TTL based) ─────────────
        # {(from_sec, to_sec): (sim_time_cached, sec_path, cost, node_path)}
        # If the same pair is requested again within the TTL, Dijkstra is skipped.
        self._section_route_cache: Dict[Tuple[int, int], Tuple[float, List[int], float, List[str]]] = {}
        self._section_route_cache_ttl: float = 5.0   # sim-seconds

    def enable_route_cost_cache(self, enabled: bool = True, ttl: float = 5.0):
        self._section_route_cache.clear()
        self._section_route_cache_ttl = float(ttl) if enabled else 0.0

    def clear_route_cost_cache(self):
        """Invalidate only the (from,to) section route cache when traffic penalties change.

        The transition caches are kept because the topology is fixed.
        """
        self._section_route_cache.clear()

    def build_mapping(self, data_set=None):
        self.section_to_nodes.clear()
        self.node_to_section.clear()
        self.section_entry_node.clear()
        self.section_exit_node.clear()
        self._transition_nodes_cache.clear()
        self._transition_zones_cache.clear()
        network = self.rm.network
        ds = data_set or self.ds

        # Pass 1: build the node lists / node→section map
        for sec_name, net_section in network.sections.items():
            try:
                sec_id = int(sec_name)
            except ValueError:
                continue
            node_names = list(net_section.node_list)
            if not node_names:
                continue
            self.section_to_nodes[sec_id] = node_names
            for node_name in node_names:
                self.node_to_section[node_name] = sec_id
            self._section_oht_count.setdefault(sec_id, 0)

        # Pass 2: decide entry/exit (must see all pass-1 results first to avoid order dependence)
        for sec_id, node_names in self.section_to_nodes.items():
            entry, exit_ = self._infer_section_endpoints(sec_id, node_names, ds)
            self.section_entry_node[sec_id] = entry
            self.section_exit_node[sec_id] = exit_

    def _infer_section_endpoints(self, sec_id: int, node_names: List[str], ds=None) -> Tuple[str, str]:
        entry, exit_ = node_names[0], node_names[-1]
        if ds is None:
            return entry, exit_

        idx = ds.section_id_to_index.get(sec_id)
        if idx is None:
            return entry, exit_

        sim_sec = ds.sections[idx]
        next_ids = getattr(sim_sec, "next_sections", []) or []
        if not next_ids:
            return entry, exit_

        first_next = self._endpoint_has_next_link(node_names[0], next_ids)
        last_next = self._endpoint_has_next_link(node_names[-1], next_ids)

        # If the last node links to the next section and the first does not, keep the default direction.
        if last_next and not first_next:
            return node_names[0], node_names[-1]
        # Otherwise, if reversed, flip it.
        if first_next and not last_next:
            return node_names[-1], node_names[0]

        return entry, exit_

    def _endpoint_has_next_link(self, node_name: str, next_ids: List[int]) -> bool:
        node = self.rm.network.nodes.get(node_name)
        if node is None:
            return False
        next_node_pool = set()
        for nid in next_ids:
            next_node_pool.update(self.section_to_nodes.get(nid, []))
        if not next_node_pool:
            return False
        return bool(set(node.move_in_times.keys()) & next_node_pool)

    def _section_endpoints(self, section_id: int) -> List[str]:
        candidates: List[str] = []
        entry = self.section_entry_node.get(section_id)
        exit_ = self.section_exit_node.get(section_id)
        node_names = self.section_to_nodes.get(section_id, [])

        for node_name in (entry, exit_, node_names[0] if node_names else None, node_names[-1] if node_names else None):
            if node_name and node_name not in candidates:
                candidates.append(node_name)
        return candidates

    def _find_best_node_route_between_sections(
        self,
        from_sec_id: int,
        to_sec_id: int,
        context: str = "SECTION_ROUTE",
    ) -> Tuple[List[str], float, Optional[str], Optional[str]]:
        if from_sec_id == to_sec_id:
            return [], 0.0, None, None

        from_candidates = self._section_endpoints(from_sec_id)
        to_candidates = self._section_endpoints(to_sec_id)
        if not from_candidates or not to_candidates:
            return [], -1.0, None, None

        best_path: List[str] = []
        best_cost = float("inf")
        best_from: Optional[str] = None
        best_to: Optional[str] = None

        # perf(#1): one 1:N Dijkstra (cost_search) per from_node yields the costs of all
        # to_candidates — reduces the former |from|×|to| individual searches to |from|.
        # cost_search returns the same path as path_search (verified); the cost differs
        # only by ~1e-11 relative error from summation order — never flips the min choice.
        for from_node in from_candidates:
            results = self.rm.pathfinder.cost_search(from_node, to_candidates, context=context)
            for to_node in to_candidates:
                entry = results.get(to_node)
                if entry is None:
                    continue
                cost, _dist, path = entry
                if path and 0 <= cost < best_cost:
                    best_path = path
                    best_cost = cost
                    best_from = from_node
                    best_to = to_node

        if not best_path:
            return [], -1.0, None, None
        return best_path, best_cost, best_from, best_to

    def estimate_section_route_cost(
        self,
        from_sec_id: int,
        to_sec_id: int,
        context: str = "SECTION_ROUTE",
    ) -> Tuple[List[int], float, List[str]]:
        if from_sec_id == to_sec_id:
            return [], 0.0, []

        # ── Section route cache lookup ───────────────────────
        _ck = (from_sec_id, to_sec_id)
        _cached = self._section_route_cache.get(_ck)
        if _cached is not None:
            _ct, _sp, _sc, _np = _cached
            if self.rm.sim_time - _ct <= self._section_route_cache_ttl:
                return _sp, _sc, _np

        node_path, cost, _, _ = self._find_best_node_route_between_sections(
            from_sec_id, to_sec_id, context=context,
        )
        if not node_path:
            return [], -1.0, []

        sec_path = self.node_route_to_section_route(node_path, exclude_first=False)

        result: List[int] = []
        seen = {from_sec_id}
        for sec_id in sec_path:
            if sec_id not in seen:
                result.append(sec_id)
                seen.add(sec_id)
        if to_sec_id not in seen:
            result.append(to_sec_id)

        # ── Store in cache ────────────────────────────────────
        self._section_route_cache[_ck] = (self.rm.sim_time, result, cost, node_path)

        return result, cost, node_path

    def estimate_section_route_cost_static(
        self,
        from_sec_id: int,
        to_sec_id: int,
    ) -> Tuple[List[int], float, List[str]]:
        """
        Estimate the cost using the static (cached) route that ignores penalties.
        Use only for relative comparisons such as ranking assignment candidates,
        not for actual route selection.
        `path_search_static` keeps a permanent internal cache, so repeated calls are nearly free.
        """
        if from_sec_id == to_sec_id:
            return [], 0.0, []

        from_candidates = self._section_endpoints(from_sec_id)
        to_candidates = self._section_endpoints(to_sec_id)
        if not from_candidates or not to_candidates:
            return [], -1.0, []

        pf = self.rm.pathfinder
        best_path: List[str] = []
        best_cost = float("inf")

        for fn in from_candidates:
            for tn in to_candidates:
                path, cost = pf.path_search_static(fn, tn)
                if path and 0.0 <= cost < best_cost:
                    best_path = path
                    best_cost = cost

        if not best_path:
            return [], -1.0, []

        sec_path = self.node_route_to_section_route(best_path, exclude_first=False)
        result: List[int] = []
        seen = {from_sec_id}
        for sec_id in sec_path:
            if sec_id not in seen:
                result.append(sec_id)
                seen.add(sec_id)
        if to_sec_id not in seen:
            result.append(to_sec_id)

        return result, best_cost, best_path

    def set_mapping(self, section_id: int, node_names: List[str], entry_node: Optional[str] = None, exit_node: Optional[str] = None):
        self.section_to_nodes[section_id] = node_names
        for node_name in node_names:
            self.node_to_section[node_name] = section_id
        if node_names:
            self.section_entry_node[section_id] = entry_node or node_names[0]
            self.section_exit_node[section_id] = exit_node or node_names[-1]
        self._section_oht_count.setdefault(section_id, 0)
        self._transition_nodes_cache.clear()
        self._transition_zones_cache.clear()

    def find_section_route(self, from_sec_id, to_sec_id):
        sec_path, _, _ = self.estimate_section_route_cost(
            from_sec_id, to_sec_id, context="SECTION_ROUTE",
        )
        return sec_path

    def node_route_to_section_route(self, node_path: List[str], exclude_first: bool = True) -> List[int]:
        sec_path: List[int] = []
        seen: set = set()
        start = 1 if exclude_first else 0
        for i in range(start, len(node_path) - 1):
            cur_name = node_path[i]
            next_name = node_path[i + 1]
            cur_node = self.rm.network.nodes.get(cur_name)
            next_node = self.rm.network.nodes.get(next_name)
            if cur_node is None or next_node is None:
                continue
            cur_secs = set(int(s) for s in cur_node.section_list if s.isdigit())
            next_secs = set(int(s) for s in next_node.section_list if s.isdigit())
            common = cur_secs & next_secs
            for sec_id in common:
                if sec_id not in seen:
                    sec_path.append(sec_id)
                    seen.add(sec_id)
        return sec_path

    def section_route_to_node_route(self, sec_path: List[int], from_sec_id: Optional[int] = None) -> List[str]:
        node_path: List[str] = []
        if from_sec_id is not None:
            exit_node = self.section_exit_node.get(from_sec_id)
            if exit_node:
                node_path.append(exit_node)
        for sec_id in sec_path:
            entry = self.section_entry_node.get(sec_id)
            exit_ = self.section_exit_node.get(sec_id)
            if entry and entry not in node_path:
                node_path.append(entry)
            if exit_ and exit_ not in node_path:
                node_path.append(exit_)
        return node_path

    def get_transition_nodes(self, from_sec_id: int, to_sec_id: int) -> List[str]:
        key = (from_sec_id, to_sec_id)
        if key in self._transition_nodes_cache:
            return list(self._transition_nodes_cache[key])

        node_path, _, from_node, to_node = self._find_best_node_route_between_sections(
            from_sec_id, to_sec_id, context="TRANSITION_PROBE",
        )

        nodes: List[str] = []
        for node_name in (from_node, *node_path, to_node):
            if node_name and node_name not in nodes:
                nodes.append(node_name)

        self._transition_nodes_cache[key] = list(nodes)
        return nodes

    def get_conflict_zones(self, from_sec_id: int, to_sec_id: int) -> List[str]:
        key = (from_sec_id, to_sec_id)
        if key in self._transition_zones_cache:
            return list(self._transition_zones_cache[key])
        nodes = self.get_transition_nodes(from_sec_id, to_sec_id)
        zones: List[str] = []
        for node_name in nodes:
            node = self.rm.network.nodes.get(node_name)
            if node is None:
                continue
            sec_count = len([s for s in node.section_list if s.isdigit()])
            if sec_count >= 2 or node_name in (self.section_entry_node.get(to_sec_id), self.section_exit_node.get(from_sec_id)):
                zones.append(f"NODE::{node_name}")
        if not zones and nodes:
            zones = [f"NODE::{n}" for n in nodes]
        self._transition_zones_cache[key] = list(zones)
        return zones

    def can_enter_transition(self, vehicle_name: str, from_sec_id: int, to_sec_id: int, tracker) -> bool:
        """
        In the section-based simulator only merge / crossing conflict zones are subject to entry control.

        The previous implementation reserved every node on the transition, which conflicted
        with the design that pins each OHT's current_node to the section entry node: as soon
        as the next section held even one OHT (even with buffer slots still free), entry kept
        being refused at the node reservation stage, causing a freeze.

        Therefore only the conflict zones are checked here, not the whole node path.
        Whether several vehicles may share one section is managed separately by the section buffer.
        """
        zones = self.get_conflict_zones(from_sec_id, to_sec_id)
        return tracker.can_reserve_conflict_zones(vehicle_name, zones)

    def reserve_transition(self, vehicle_name: str, from_sec_id: int, to_sec_id: int, tracker, until_time: float) -> bool:
        """Reserve only the conflict zones right before a section transition."""
        zones = self.get_conflict_zones(from_sec_id, to_sec_id)
        return tracker.reserve_conflict_zones(vehicle_name, zones, until_time)

    def release_transition(self, vehicle_name: str, tracker):
        tracker.release_reservations(vehicle_name)
        tracker.release_conflict_zones(vehicle_name)

    def on_oht_enter_section(self, section_id: int, update_penalty: bool = True):
        self._section_oht_count[section_id] = self._section_oht_count.get(section_id, 0) + 1
        if update_penalty:
            self._update_section_penalty(section_id)

    def on_oht_leave_section(self, section_id: int, update_penalty: bool = True):
        count = self._section_oht_count.get(section_id, 0)
        self._section_oht_count[section_id] = max(0, count - 1)
        if update_penalty:
            self._update_section_penalty(section_id)

    def _update_section_penalty(self, section_id: int):
        node_names = self.section_to_nodes.get(section_id, [])
        oht_count = self._section_oht_count.get(section_id, 0)
        penalty = self.base_penalty + self.penalty_per_oht * oht_count
        for node_name in node_names:
            node = self.rm.network.nodes.get(node_name)
            if node:
                node.traffic_penalty = penalty
        self.rm.pathfinder.notify_penalty_changed(node_names)

    def update_all_penalties(self):
        for sec_id in self.section_to_nodes:
            self._update_section_penalty(sec_id)

    def reset_penalties(self):
        for node in self.rm.network.nodes.values():
            node.traffic_penalty = self.base_penalty
        for sec_id in self._section_oht_count:
            self._section_oht_count[sec_id] = 0
        self.rm.pathfinder.notify_all_penalties_changed()

    def reset_runtime_state(self):
        self.reset_penalties()
        self._transition_nodes_cache.clear()
        self._transition_zones_cache.clear()
        self._section_route_cache.clear()

    def get_section_for_node(self, node_name: str) -> Optional[int]:
        return self.node_to_section.get(node_name)

    def get_nodes_for_section(self, section_id: int) -> List[str]:
        return self.section_to_nodes.get(section_id, [])

    def get_entry_node(self, section_id: int) -> Optional[str]:
        return self.section_entry_node.get(section_id)

    def get_exit_node(self, section_id: int) -> Optional[str]:
        return self.section_exit_node.get(section_id)

    def get_section_congestion(self, section_id: int) -> int:
        return self._section_oht_count.get(section_id, 0)

    def rank_sparse_entry_nodes(self, exclude_node: Optional[str] = None, exclude_sections: Optional[set] = None) -> List[Tuple[int, str, int]]:
        exclude_sections = exclude_sections or set()
        ranked: List[Tuple[int, str, int]] = []
        for sec_id, entry_node in self.section_entry_node.items():
            if entry_node == exclude_node:
                continue
            if sec_id in exclude_sections:
                continue
            congestion = self._section_oht_count.get(sec_id, 0)
            ranked.append((sec_id, entry_node, congestion))
        ranked.sort(key=lambda x: (x[2], x[0]))
        return ranked

    def __repr__(self) -> str:
        return f"SectionNodeBridge(sections={len(self.section_to_nodes)}, nodes={len(self.node_to_section)})"
