"""
route_manager.py - unified API

High-level interface combining Network, PathFinder, and VehicleTracker.
Provides the combined usage pattern of the Java RouteManager + Rail.cLARouteManger.

The simulator's EventHandler searches routes through this class.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple

from .graph import Network
from .pathfinder import PathFinder
from .vehicle_tracker import VehicleTracker, VehicleState
from .rail_parser import RailParser
from .logistics_logger import get_logistics_logger


class RouteManager:
    """
    Route manager for the AMHS rail network.

    Combines the Java RouteManager + Rail.cLARouteManger patterns.
    Usage pattern in the simulator:

        # Initialization
        rm = RouteManager()
        rm.load_from_rail("layout.rail")
        rm.initialize()

        # Route search
        path = rm.get_route("node_1", "node_100")
        path = rm.get_route_by_eq("EQ_A", "EQ_B")

        # Vehicle management (for simulation)
        rm.register_vehicle("OHT_1", "node_10")
        rm.assign_route("OHT_1", "node_100")
    """

    def __init__(self):
        self.network: Network = Network()
        self.pathfinder: PathFinder = PathFinder(self.network)
        self.tracker: VehicleTracker = VehicleTracker(self.network)

        # Equipment-to-equipment cost table cache
        self._eq_cost_table: Optional[Dict[str, Tuple[float, float]]] = None

        # Detour limit statistics
        self._detour_fallback_count: int = 0
        self._total_route_count: int = 0

        # Logistics logger & current simulation time (updated externally)
        self._log = get_logistics_logger()
        self._sim_time: float = 0.0

        # DISPATCH_PROBE/TRANSITION_PROBE are repeated internal searches for candidate evaluation.
        # On large layouts these probe logs grow to thousands or tens of thousands of rows and
        # freeze the GUI, so they are skipped by default.
        # Actual assignment/vehicle/final-route functionality is unaffected.
        self.log_probe_pathfind: bool = False

    @property
    def sim_time(self) -> float:
        return self._sim_time

    @sim_time.setter
    def sim_time(self, value: float):
        self._sim_time = value
        # Keep the VehicleTracker time in sync
        self.tracker.sim_time = value
        # Sync the time so the PathFinder can judge its dynamic cache TTL
        self.pathfinder._sim_time = value

    # ─── Initialization ────────────────────────────

    def load_from_rail(self, filepath: str) -> Network:
        """
        Load the network from a .rail file.
        Java: Rail.openRailFile() + Rail.initRouteManger()
        """
        parser = RailParser()
        self.network = parser.parse(filepath)
        self.pathfinder = PathFinder(self.network)
        self.tracker = VehicleTracker(self.network)
        self.tracker.sim_time = self.sim_time
        self._eq_cost_table = None
        return self.network

    def load_from_network(self, network: Network):
        """Use an already constructed Network object"""
        self.network = network
        self.pathfinder = PathFinder(self.network)
        self.tracker = VehicleTracker(self.network)
        self.tracker.sim_time = self.sim_time
        self._eq_cost_table = None

    def initialize(
        self,
        line_speed: float = 2.6667,
        curve_speed: float = 0.8,
        vehicle_length: float = 1.0,
        vehicle_width: float = 1.0,
    ):
        """
        Initialize the network (compute travel times).
        Java: Rail.initRouteManger()
        """
        self.pathfinder.initialize(
            line_speed=line_speed,
            curve_speed=curve_speed,
            vehicle_length=vehicle_length,
            vehicle_width=vehicle_width,
        )

    def set_custom_cost_function(self, cost_function):
        """Inject an external routing cost function. None keeps the default cost formula."""
        self.pathfinder.set_custom_cost_function(cost_function)

    def configure_detour_limit(
        self,
        max_detour_ratio: float = 1.5,
        penalty_weight: float = 0.5,
        penalty_cap: float = 3.0,
    ):
        """
        Configure the detour limit parameters (A+B approach).

        Args:
            max_detour_ratio: maximum detour ratio relative to the static route.
                If the dynamic route's node count exceeds static route * ratio,
                fall back to the static route that ignores penalties.
                0 disables it (unlimited detours). Default 1.5.
            penalty_weight: traffic_penalty influence ratio (0.0~1.0).
                0.0 ignores penalties entirely, 1.0 uses them as-is.
                Default 0.5 (halves the penalty influence).
            penalty_cap: penalty upper bound.
                However high raw_penalty is, the effective penalty never
                exceeds 1 + weight * cap. Default 3.0.

        Examples:
            # Conservative: allow only slight detours, small penalty influence
            rm.configure_detour_limit(1.3, 0.3, 2.0)

            # Lenient: allow generous detours, large penalty influence
            rm.configure_detour_limit(2.0, 0.8, 5.0)

            # Disabled: same as the previous behaviour (no detour limit)
            rm.configure_detour_limit(0, 1.0, 999)
        """
        self.pathfinder.max_detour_ratio = max_detour_ratio
        self.pathfinder.penalty_weight = penalty_weight
        self.pathfinder.penalty_cap = penalty_cap
        self.pathfinder.clear_static_cache()

    # ─── Route search ──────────────────────────────

    def get_route(
        self,
        from_node: str,
        to_node: str,
        context: str = "GENERAL",
    ) -> List[str]:
        """
        Shortest route between nodes (with detour limiting).

        If max_detour_ratio is set, an excessive detour falls back to the
        static route. Check the fallback frequency with get_detour_stats().

        Returns:
            List of node names. Empty list if there is no route.
        """
        path, _ = self.get_route_with_cost(from_node, to_node, context=context)
        return path

    def get_route_with_cost(
        self,
        from_node: str,
        to_node: str,
        context: str = "GENERAL",
    ) -> Tuple[List[str], float]:
        """Return the route and its travel time"""
        path, cost = self.pathfinder.path_search(from_node, to_node)

        # ── Logistics log ──
        # Internal probe searches are repeated calls for candidate evaluation, not core
        # logs for functional verification. Unless skipped, on the large SMAT2022 layout the
        # path string construction / memory accumulation alone causes periodic stalls.
        if self.log_probe_pathfind or not context.endswith("PROBE"):
            pf = self.pathfinder
            is_fallback = False
            static_len = 0
            detour_ratio = 0.0

            if pf.max_detour_ratio > 0 and path:
                cache_key = (from_node, to_node)
                static_result = pf._static_cache.get(cache_key)
                if static_result:
                    static_len = len(static_result[0])
                    if static_len > 1:
                        detour_ratio = len(path) / static_len
                        is_fallback = (path == static_result[0] and detour_ratio != 1.0)

            self._log.log_pathfind(
                sim_time=self.sim_time,
                from_node=from_node,
                to_node=to_node,
                path=path,
                cost=cost,
                path_length=len(path),
                is_fallback=is_fallback,
                static_path_length=static_len,
                detour_ratio=detour_ratio,
                context=context,
            )

        return path, cost

    def get_route_by_eq(
        self,
        from_eq: str,
        to_eq: str,
    ) -> List[str]:
        """
        Shortest route between two pieces of equipment.
        Converts equipment names to node names, then searches the route.
        """
        from_node = self.network.eq_to_node.get(from_eq)
        to_node = self.network.eq_to_node.get(to_eq)

        if from_node is None or to_node is None:
            return []

        return self.get_route(from_node, to_node)

    def get_cost_by_eq(
        self,
        from_eq: str,
        to_eq: str,
    ) -> Tuple[float, float]:
        """
        Return the travel time and distance between two pieces of equipment.
        Uses the cached cost table if one exists.

        Returns:
            (time, distance). (inf, inf) if there is no route.
        """
        from_node = self.network.eq_to_node.get(from_eq)
        to_node = self.network.eq_to_node.get(to_eq)

        if from_node is None or to_node is None:
            return (float('inf'), float('inf'))

        # Check the cached table
        if self._eq_cost_table:
            key = f"{from_node}_{to_node}"
            if key in self._eq_cost_table:
                return self._eq_cost_table[key]

        _, cost = self.pathfinder.path_search(from_node, to_node)
        if cost < 0:
            return (float('inf'), float('inf'))
        return (cost, 0.0)  # distance must be computed separately

    # ─── Cost table ────────────────────────────────

    def build_eq_cost_table(
        self,
        eq_names: Optional[List[str]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        """
        Build and cache the equipment-to-equipment cost table.
        Java: Rail.makeFromToCostTable()
        """
        self._eq_cost_table = self.pathfinder.build_eq_cost_table(eq_names)
        return self._eq_cost_table

    # ─── Route conversion ──────────────────────────

    def node_path_to_section_path(self, node_path: List[str]) -> List[str]:
        """
        Convert a node path into a section path.

        Selects only the sections that contain both consecutive nodes (cur, next).
        Prevents a wrong section from slipping in when a branch-point node belongs to several sections.

        Args:
            node_path: list of node names

        Returns:
            List of section names (deduplicated, order preserved)
        """
        if not node_path:
            return []

        section_path: List[str] = []
        seen: set = set()

        for i in range(len(node_path) - 1):
            cur_name  = node_path[i]
            next_name = node_path[i + 1]

            cur_node  = self.network.nodes.get(cur_name)
            next_node = self.network.nodes.get(next_name)
            if cur_node is None or next_node is None:
                continue

            # Select only the sections shared by both nodes
            cur_secs  = set(cur_node.section_list)
            next_secs = set(next_node.section_list)
            common    = cur_secs & next_secs

            for sec_name in cur_node.section_list:   # iterate over cur to preserve order
                if sec_name in common and sec_name not in seen:
                    section_path.append(sec_name)
                    seen.add(sec_name)

        return section_path

    # ─── Vehicle management (for simulation) ────────

    def register_vehicle(
        self,
        name: str,
        node_name: Optional[str] = None,
    ) -> VehicleState:
        """Register a vehicle"""
        state = self.tracker.register_vehicle(name, node_name)
        self._log.log_vehicle_register(self.sim_time, name, node_name or "")
        return state

    def assign_route(
        self,
        vehicle_name: str,
        to_node: str,
    ) -> List[str]:
        """
        Assign a route to the destination to a vehicle.

        Returns:
            The assigned route. Empty list if there is no route.
        """
        vehicle = self.tracker.vehicles.get(vehicle_name)
        if vehicle is None or vehicle.current_node is None:
            return []

        from_node = vehicle.current_node
        path = self.get_route(from_node, to_node)
        if path:
            # Accumulate IDLE time before the IDLE → ASSIGNED transition
            idle_elapsed = self.tracker.flush_idle_time(vehicle_name)

            self.tracker.set_path(vehicle_name, path)
            vehicle.status = "ASSIGNED"
            self._log.log_vehicle_assign(
                self.sim_time, vehicle_name, from_node, to_node,
                len(path), idle_elapsed,
            )
        return path

    def move_vehicle(self, vehicle_name: str) -> bool:
        """Move the vehicle to the next node on its route"""
        vehicle = self.tracker.vehicles.get(vehicle_name)
        from_node = vehicle.current_node if vehicle else ""
        next_node = vehicle.next_node if vehicle else ""

        success = self.tracker.move_vehicle(vehicle_name)

        # ── Move log ──
        blocked_by = None
        if not success and next_node:
            blocked_by = self.tracker.node_occupancy.get(next_node)
        self._log.log_vehicle_move(
            self.sim_time, vehicle_name,
            from_node or "", next_node or "",
            success, blocked_by,
        )
        return success

    def can_move_vehicle(self, vehicle_name: str) -> bool:
        """Check whether the vehicle can move to the next node"""
        return self.tracker.can_move(vehicle_name)

    def detect_deadlock(self) -> List[List[str]]:
        """Deadlock detection"""
        cycles = self.tracker.detect_deadlock()
        if cycles:
            self._log.log_deadlock(self.sim_time, cycles)
        return cycles

    # ─── Detour limit statistics ───────────────────

    def get_detour_stats(self) -> Dict[str, any]:
        """
        Return the detour limit statistics.

        Returns:
            {
                'total_searches': total number of route searches,
                'fallback_count': number of fallbacks to the static route,
                'fallback_ratio': fallback ratio (0.0 ~ 1.0),
                'settings': current detour limit settings,
            }
        """
        pf = self.pathfinder
        total = pf.total_search_count
        fallback = pf.detour_fallback_count
        return {
            'total_searches': total,
            'fallback_count': fallback,
            'fallback_ratio': fallback / total if total > 0 else 0.0,
            'settings': {
                'max_detour_ratio': pf.max_detour_ratio,
                'penalty_weight': pf.penalty_weight,
                'penalty_cap': pf.penalty_cap,
            },
        }

    def reset_detour_stats(self):
        """Reset the detour limit statistics"""
        self.pathfinder.total_search_count = 0
        self.pathfinder.detour_fallback_count = 0

    # ─── Idle Positioning ────────────────────────────────

    def get_idle_reposition_targets(
        self,
        idle_vehicle_names: Optional[List[str]] = None,
        sample_nodes: Optional[List[str]] = None,
        top_k: int = 3,
    ) -> Dict[str, str]:
        """
        Recommend quiet destination nodes for IDLE vehicles to move to.

        Finds the least congested nodes in the whole network by traffic_penalty
        and assigns one to each vehicle. The same node is never assigned to two vehicles.

        Args:
            idle_vehicle_names: target vehicles. None means every IDLE vehicle.
            sample_nodes: candidate node pool. None means every usable node.
            top_k: candidate multiplier per vehicle. Secures vehicle count * top_k candidates.

        Returns:
            {vehicle_name: target_node_name}
        """
        vehicles = idle_vehicle_names or self.tracker.get_idle_vehicles()
        if not vehicles:
            return {}

        nodes = sample_nodes or list(self.network.nodes.keys())
        candidates = sorted(
            [n for n in nodes if self.network.nodes[n].use],
            key=lambda n: self.network.nodes[n].traffic_penalty,
        )[:max(top_k * len(vehicles), 10)]

        assignments: Dict[str, str] = {}
        assigned_nodes: set = set()

        for veh_name in vehicles:
            vehicle = self.tracker.vehicles.get(veh_name)
            if vehicle is None or vehicle.current_node is None:
                continue

            available = [
                n for n in candidates
                if n != vehicle.current_node and n not in assigned_nodes
            ]
            target = self.tracker.get_least_congested_node(
                available, exclude_vehicles={veh_name}
            )
            if target:
                assignments[veh_name] = target
                assigned_nodes.add(target)

        return assignments

    def reposition_idle_vehicles(
        self,
        idle_vehicle_names: Optional[List[str]] = None,
        top_k: int = 3,
    ) -> Dict[str, List[str]]:
        """
        Reposition IDLE vehicles to quiet areas.

        Called in the idle gaps of simulation_step.
        The caller must ensure it only runs when there are no pending transport requests.

        Returns:
            {vehicle_name: assigned_path} — only vehicles that received a route
        """
        targets = self.get_idle_reposition_targets(
            idle_vehicle_names, top_k=top_k
        )
        result: Dict[str, List[str]] = {}

        for veh_name, target_node in targets.items():
            vehicle = self.tracker.vehicles.get(veh_name)
            if vehicle is None or vehicle.current_node is None:
                continue

            from_node = vehicle.current_node
            path = self.get_route(from_node, target_node)
            if not path:
                continue

            # Accumulate IDLE time before the IDLE → REPOSITIONING transition
            idle_elapsed = self.tracker.flush_idle_time(veh_name)

            self.tracker.set_path(veh_name, path)
            vehicle.status = "REPOSITIONING"
            result[veh_name] = path

            self._log.log_vehicle_reposition(
                self.sim_time, veh_name,
                from_node, target_node,
                len(path), idle_elapsed,
            )

        return result


    def get_transition_node_path(self, from_sec_id: int, to_sec_id: int, bridge=None) -> List[str]:
        if bridge is not None:
            return bridge.get_transition_nodes(from_sec_id, to_sec_id)
        return []

    def reset_runtime_state(self):
        self.tracker.reset_runtime_state()
        self.tracker.sim_time = self.sim_time

    # ─── Lookups ───────────────────────────────────

    def get_eq_node(self, eq_name: str) -> Optional[str]:
        """Return the node name corresponding to an equipment name"""
        return self.network.eq_to_node.get(eq_name)

    def get_node_eq(self, node_name: str) -> Optional[str]:
        """Return the equipment name corresponding to a node name"""
        return self.network.node_to_eq.get(node_name)

    def __repr__(self) -> str:
        return (
            f"RouteManager({self.network}, "
            f"vehicles={len(self.tracker.vehicles)})"
        )