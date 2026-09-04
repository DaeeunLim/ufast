"""
vehicle_tracker.py - node-level vehicle tracking and deadlock detection

Manages the node occupancy state of OHTs in the simulation and
detects deadlocks using a wait-for graph.

A simulation-oriented redesign of the Java RouteManager's real-time vehicle management.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .graph import Network
from .logistics_logger import get_logistics_logger


@dataclass
class VehicleState:
    name: str
    current_node: Optional[str] = None
    status: str = "IDLE"  # IDLE, ASSIGNED, LOADED, MOVING, REPOSITIONING
    destination_node: Optional[str] = None
    path: List[str] = field(default_factory=list)
    path_index: int = 0
    idle_since: float = 0.0
    total_idle_time: float = 0.0
    idle_count: int = 0

    @property
    def next_node(self) -> Optional[str]:
        if self.path and self.path_index + 1 < len(self.path):
            return self.path[self.path_index + 1]
        return None

    @property
    def has_path(self) -> bool:
        return len(self.path) > 0 and self.path_index < len(self.path)


class VehicleTracker:
    def __init__(self, network: Network):
        self.network = network
        self.vehicles: Dict[str, VehicleState] = {}
        self._log = get_logistics_logger()
        self.sim_time: float = 0.0
        self.node_occupancy: Dict[str, Optional[str]] = {}
        self.node_reservations: Dict[str, tuple[str, float]] = {}
        self.vehicle_reserved_nodes: Dict[str, Set[str]] = {}
        self.conflict_zone_reservations: Dict[str, tuple[str, float]] = {}
        self.vehicle_reserved_zones: Dict[str, Set[str]] = {}

    def _purge_expired(self):
        expired_nodes = [n for n, (_, until) in self.node_reservations.items() if until <= self.sim_time]
        for node in expired_nodes:
            owner, _ = self.node_reservations.pop(node, (None, 0.0))
            if owner is not None:
                self.vehicle_reserved_nodes.get(owner, set()).discard(node)
        expired_zones = [z for z, (_, until) in self.conflict_zone_reservations.items() if until <= self.sim_time]
        for zone in expired_zones:
            owner, _ = self.conflict_zone_reservations.pop(zone, (None, 0.0))
            if owner is not None:
                self.vehicle_reserved_zones.get(owner, set()).discard(zone)

    def reset_runtime_state(self):
        self.node_occupancy.clear()
        self.node_reservations.clear()
        self.vehicle_reserved_nodes.clear()
        self.conflict_zone_reservations.clear()
        self.vehicle_reserved_zones.clear()
        for v in self.vehicles.values():
            v.path = []
            v.path_index = 0
            v.destination_node = None

    def register_vehicle(self, name: str, initial_node: Optional[str] = None, status: str = "IDLE") -> VehicleState:
        vehicle = VehicleState(name=name, current_node=initial_node, status=status, idle_since=self.sim_time)
        self.vehicles[name] = vehicle
        # Note: current_node is only the "representative node of the current section", not a
        # single node that can actually be occupied. In the section/buffer simulator several OHTs
        # may share one section, so marking node_occupancy as a permanent occupancy here would
        # over-block the subsequent reservation stage.
        self.vehicle_reserved_nodes.setdefault(name, set())
        self.vehicle_reserved_zones.setdefault(name, set())
        return vehicle

    def unregister_vehicle(self, name: str):
        vehicle = self.vehicles.pop(name, None)
        self.release_reservations(name)
        self.release_conflict_zones(name)
        if vehicle and vehicle.current_node:
            if self.node_occupancy.get(vehicle.current_node) == name:
                self.node_occupancy[vehicle.current_node] = None

    def set_path(self, vehicle_name: str, path: List[str]):
        vehicle = self.vehicles.get(vehicle_name)
        if vehicle is None:
            return
        vehicle.path = path
        vehicle.path_index = 0
        if path:
            vehicle.destination_node = path[-1]

    def is_node_occupied(self, node_name: str) -> bool:
        self._purge_expired()
        return self.node_occupancy.get(node_name) is not None

    def get_occupying_vehicle(self, node_name: str) -> Optional[str]:
        self._purge_expired()
        return self.node_occupancy.get(node_name)

    def occupy_node(self, vehicle_name: str, node_name: str):
        self._purge_expired()
        self.node_occupancy[node_name] = vehicle_name

    def release_node(self, vehicle_name: str, node_name: Optional[str]):
        if node_name and self.node_occupancy.get(node_name) == vehicle_name:
            self.node_occupancy[node_name] = None

    def can_reserve_path(self, vehicle_name: str, node_list: List[str]) -> bool:
        self._purge_expired()
        # In this simulator current_node is the section's representative node, so node_occupancy
        # is not used as a hard blocking condition. Actual collision avoidance is handled by
        # conflict zone reservations and the section buffer (front/slot) constraints.
        for node in node_list:
            res = self.node_reservations.get(node)
            if res is not None and res[0] != vehicle_name:
                return False
        return True

    def reserve_path(self, vehicle_name: str, node_list: List[str], until_time: float) -> bool:
        self._purge_expired()
        if not self.can_reserve_path(vehicle_name, node_list):
            return False
        owned = self.vehicle_reserved_nodes.setdefault(vehicle_name, set())
        for node in node_list:
            self.node_reservations[node] = (vehicle_name, until_time)
            owned.add(node)
        return True

    def release_reservations(self, vehicle_name: str):
        nodes = list(self.vehicle_reserved_nodes.get(vehicle_name, set()))
        for node in nodes:
            res = self.node_reservations.get(node)
            if res is not None and res[0] == vehicle_name:
                self.node_reservations.pop(node, None)
        self.vehicle_reserved_nodes[vehicle_name] = set()

    def can_reserve_conflict_zones(self, vehicle_name: str, zones: List[str]) -> bool:
        self._purge_expired()
        for zone in zones:
            res = self.conflict_zone_reservations.get(zone)
            if res is not None and res[0] != vehicle_name:
                return False
        return True

    def reserve_conflict_zones(self, vehicle_name: str, zones: List[str], until_time: float) -> bool:
        self._purge_expired()
        if not self.can_reserve_conflict_zones(vehicle_name, zones):
            return False
        owned = self.vehicle_reserved_zones.setdefault(vehicle_name, set())
        for zone in zones:
            self.conflict_zone_reservations[zone] = (vehicle_name, until_time)
            owned.add(zone)
        return True

    def release_conflict_zones(self, vehicle_name: str):
        zones = list(self.vehicle_reserved_zones.get(vehicle_name, set()))
        for zone in zones:
            res = self.conflict_zone_reservations.get(zone)
            if res is not None and res[0] == vehicle_name:
                self.conflict_zone_reservations.pop(zone, None)
        self.vehicle_reserved_zones[vehicle_name] = set()

    def can_move(self, vehicle_name: str) -> bool:
        vehicle = self.vehicles.get(vehicle_name)
        if vehicle is None or not vehicle.has_path:
            return False
        next_node = vehicle.next_node
        if next_node is None:
            return False
        self._purge_expired()
        res = self.node_reservations.get(next_node)
        if res is not None and res[0] != vehicle_name:
            return False
        return True

    def move_vehicle(self, vehicle_name: str) -> bool:
        vehicle = self.vehicles.get(vehicle_name)
        if vehicle is None or not vehicle.has_path:
            return False
        next_node = vehicle.next_node
        if next_node is None or not self.can_move(vehicle_name):
            return False
        old_node = vehicle.current_node
        self.release_node(vehicle_name, old_node)
        vehicle.current_node = next_node
        vehicle.path_index += 1
        self.occupy_node(vehicle_name, next_node)
        if next_node == vehicle.destination_node:
            old_status = vehicle.status
            vehicle.status = "IDLE"
            vehicle.path = []
            vehicle.path_index = 0
            vehicle.destination_node = None
            vehicle.idle_since = self.sim_time
            vehicle.idle_count += 1
            self._log.log_vehicle_status(self.sim_time, vehicle_name, old_status, "IDLE", current_node=next_node, destination="(arrived)")
        return True

    def place_vehicle(self, vehicle_name: str, node_name: str):
        vehicle = self.vehicles.get(vehicle_name)
        if vehicle is None:
            return
        self._purge_expired()
        old_node = vehicle.current_node
        self.release_node(vehicle_name, old_node)
        vehicle.current_node = node_name
        # Only update current_node as the representative position; do not mark a permanent node occupancy.

    def get_available_next_nodes(self, node_name: str) -> List[str]:
        self._purge_expired()
        neighbors = self.network.get_neighbors(node_name)
        return [name for name, _ in neighbors if self.node_reservations.get(name) is None]

    def detect_deadlock(self) -> List[List[str]]:
        self._purge_expired()
        wait_for: Dict[str, str] = {}
        for name, vehicle in self.vehicles.items():
            if not vehicle.has_path:
                continue
            next_node = vehicle.next_node
            if next_node is None:
                continue
            blocking_vehicle = self.node_occupancy.get(next_node)
            if blocking_vehicle is not None and blocking_vehicle != name:
                wait_for[name] = blocking_vehicle
                continue
            res = self.node_reservations.get(next_node)
            if res is not None and res[0] != name:
                wait_for[name] = res[0]
        visited: Set[str] = set()
        in_stack: Set[str] = set()
        cycles: List[List[str]] = []
        def dfs(node: str, path: List[str]):
            if node in in_stack:
                cycle_start = path.index(node)
                cycles.append(path[cycle_start:])
                return
            if node in visited:
                return
            visited.add(node)
            in_stack.add(node)
            path.append(node)
            next_vehicle = wait_for.get(node)
            if next_vehicle:
                dfs(next_vehicle, path)
            path.pop()
            in_stack.discard(node)
        for vehicle_name in wait_for:
            if vehicle_name not in visited:
                dfs(vehicle_name, [])
        return cycles

    def get_status_summary(self) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for vehicle in self.vehicles.values():
            summary[vehicle.status] = summary.get(vehicle.status, 0) + 1
        return summary

    def get_occupied_nodes(self) -> List[str]:
        self._purge_expired()
        return [node for node, vehicle in self.node_occupancy.items() if vehicle is not None]

    def get_idle_vehicles(self) -> List[str]:
        return [name for name, v in self.vehicles.items() if v.status == "IDLE" and not v.has_path]

    def get_least_congested_node(self, candidate_nodes: List[str], exclude_vehicles: Optional[Set[str]] = None) -> Optional[str]:
        self._purge_expired()
        best_node = None
        best_penalty = float('inf')
        for node_name in candidate_nodes:
            occupant = self.node_occupancy.get(node_name)
            if occupant is not None:
                if exclude_vehicles is None or occupant not in exclude_vehicles:
                    continue
            node = self.network.nodes.get(node_name)
            if node is None or not node.use:
                continue
            if node.traffic_penalty < best_penalty:
                best_penalty = node.traffic_penalty
                best_node = node_name
        return best_node

    def get_current_idle_time(self, vehicle_name: str) -> float:
        vehicle = self.vehicles.get(vehicle_name)
        if vehicle is None or vehicle.status != "IDLE":
            return 0.0
        return self.sim_time - vehicle.idle_since

    def flush_idle_time(self, vehicle_name: str) -> float:
        vehicle = self.vehicles.get(vehicle_name)
        if vehicle is None or vehicle.status != "IDLE":
            return 0.0
        elapsed = self.sim_time - vehicle.idle_since
        vehicle.total_idle_time += elapsed
        vehicle.idle_since = 0.0
        return elapsed

    def get_idle_stats(self) -> Dict[str, Dict[str, float]]:
        stats: Dict[str, Dict[str, float]] = {}
        for name, vehicle in self.vehicles.items():
            current = self.get_current_idle_time(name)
            total = vehicle.total_idle_time + current
            ratio = total / self.sim_time if self.sim_time > 0 else 0.0
            stats[name] = {
                'total_idle_time': total,
                'current_idle_time': current,
                'idle_count': vehicle.idle_count,
                'idle_ratio': ratio,
            }
        return stats
