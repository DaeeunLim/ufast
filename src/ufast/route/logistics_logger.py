"""
logistics_logger.py - RouteManager logistics log (CSV)

Records route searches, OHT assignments, and vehicle moves/status changes to CSV files.
Calling save() after the simulation completes produces three CSV files:

  1. route_pathfind_{ts}.csv  - route search results
  2. route_dispatch_{ts}.csv - OHT assignment decisions
  3. route_vehicle_{ts}.csv  - vehicle moves and status changes

Usage pattern:
    from ufast.route.logistics_logger import get_logistics_logger

    logger = get_logistics_logger()
    logger.reset()

    # Called automatically by each module (RouteManager, Dispatcher, VehicleTracker)
    logger.log_pathfind(...)
    logger.log_dispatch(...)
    logger.log_vehicle_move(...)

    # At the end of the simulation
    logger.save("logs")
"""

from __future__ import annotations
import os
import csv
from datetime import datetime
from typing import List, Optional, Dict, Any


class LogisticsLogger:
    """
    RouteManager logistics log recorder.

    Accumulates three kinds of CSV logs in memory and writes them all at once with save().
    DEBUG level: records every detailed action.
    """

    def __init__(self):
        self._pathfind_rows: List[List[Any]] = []
        self._dispatch_rows: List[List[Any]] = []
        self._vehicle_rows: List[List[Any]] = []
        self._enabled: bool = True
        self._last_dispatch_key = None
        self._last_vehicle_key = None

    def reset(self):
        """Clear the logs (call at simulation start)"""
        self._pathfind_rows.clear()
        self._dispatch_rows.clear()
        self._vehicle_rows.clear()
        self._last_dispatch_key = None
        self._last_vehicle_key = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    # ─── Route search log ────────────────────────────────────

    def log_pathfind(
        self,
        sim_time: float,
        from_node: str,
        to_node: str,
        path: List[str],
        cost: float,
        path_length: int,
        is_fallback: bool = False,
        static_path_length: int = 0,
        detour_ratio: float = 0.0,
        context: str = "GENERAL",
    ):
        """
        Record a route search result.

        Args:
            sim_time: simulation time (seconds)
            from_node: origin node
            to_node: destination node
            path: the path found (list of nodes)
            cost: route cost (travel time)
            path_length: number of nodes in the path
            is_fallback: whether the static-route fallback was used
            static_path_length: number of nodes in the static path
            detour_ratio: detour ratio
            context: context of the search call (e.g. DISPATCH_PROBE, SECTION_ROUTE)
        """
        if not self._enabled:
            return

        self._pathfind_rows.append([
            f"{sim_time:.2f}",
            from_node,
            to_node,
            "SUCCESS" if path else "FAIL",
            path_length,
            f"{cost:.4f}" if cost >= 0 else "",
            "Y" if is_fallback else "N",
            static_path_length if static_path_length > 0 else "",
            f"{detour_ratio:.2f}" if detour_ratio > 0 else "",
            context,
            " → ".join(path[:10]) + ("..." if len(path) > 10 else "") if path else "",
        ])

    def log_pathfind_by_eq(
        self,
        sim_time: float,
        from_eq: str,
        to_eq: str,
        from_node: str,
        to_node: str,
        path_length: int,
        cost: float,
    ):
        """Equipment-to-equipment route search result (get_route_by_eq)"""
        if not self._enabled:
            return

        self._pathfind_rows.append([
            f"{sim_time:.2f}",
            f"{from_node} ({from_eq})",
            f"{to_node} ({to_eq})",
            "SUCCESS" if path_length > 0 else "FAIL",
            path_length,
            f"{cost:.4f}" if cost >= 0 else "",
            "N",
            "",
            "",
            "EQ_ROUTE",
            f"EQ: {from_eq} → {to_eq}",
        ])

    # ─── Assignment log ──────────────────────────────────────

    def log_dispatch(
        self,
        sim_time: float,
        target_section_id: int,
        selected_oht: Optional[str],
        strategy_name: str,
        idle_count: int,
        total_count: int,
        reason: str = "",
    ):
        """
        Record an OHT assignment decision.

        Args:
            sim_time: simulation time
            target_section_id: section ID of the transport request
            selected_oht: name of the selected OHT (None means assignment failed)
            strategy_name: name of the strategy used
            idle_count: number of IDLE OHTs
            total_count: total number of OHTs
            reason: selection reason (same section, nearest, fallback, etc.)
        """
        if not self._enabled:
            return

        row = [
            f"{sim_time:.2f}",
            target_section_id,
            selected_oht or "(FAIL)",
            "SUCCESS" if selected_oht else "FAIL",
            strategy_name,
            idle_count,
            total_count,
            reason,
        ]
        key = tuple(row)
        if key == self._last_dispatch_key:
            return
        self._last_dispatch_key = key
        self._dispatch_rows.append(row)

    # ─── Vehicle move/status log ─────────────────────────────

    def log_vehicle_move(
        self,
        sim_time: float,
        vehicle_name: str,
        from_node: str,
        to_node: str,
        success: bool,
        blocked_by: Optional[str] = None,
    ):
        """
        Record a vehicle move.

        Args:
            sim_time: simulation time
            vehicle_name: vehicle name
            from_node: previous node
            to_node: next node
            success: whether the move succeeded
            blocked_by: name of the blocking vehicle if the move failed
        """
        if not self._enabled:
            return

        row = [
            f"{sim_time:.2f}",
            "MOVE",
            vehicle_name,
            from_node,
            to_node,
            "OK" if success else "BLOCKED",
            blocked_by or "",
            "",
            "",
            "",
        ]
        key = tuple(row)
        if key == self._last_vehicle_key:
            return
        self._last_vehicle_key = key
        self._vehicle_rows.append(row)

    def log_vehicle_status(
        self,
        sim_time: float,
        vehicle_name: str,
        old_status: str,
        new_status: str,
        current_node: str = "",
        destination: str = "",
        path_length: int = 0,
    ):
        """
        Record a vehicle status change.
        """
        if not self._enabled:
            return

        row = [
            f"{sim_time:.2f}",
            "STATUS",
            vehicle_name,
            current_node,
            destination,
            f"{old_status} → {new_status}",
            "",
            new_status,
            path_length,
            "",
        ]
        key = tuple(row)
        if key == self._last_vehicle_key:
            return
        self._last_vehicle_key = key
        self._vehicle_rows.append(row)

    def log_vehicle_register(
        self,
        sim_time: float,
        vehicle_name: str,
        node: str = "",
    ):
        """Record a vehicle registration."""
        if not self._enabled:
            return

        row = [
            f"{sim_time:.2f}",
            "REGISTER",
            vehicle_name,
            node,
            "",
            "REGISTERED",
            "",
            "IDLE",
            0,
            "",
        ]
        key = tuple(row)
        if key == self._last_vehicle_key:
            return
        self._last_vehicle_key = key
        self._vehicle_rows.append(row)

    def log_vehicle_assign(
        self,
        sim_time: float,
        vehicle_name: str,
        from_node: str,
        to_node: str,
        path_length: int,
        idle_elapsed: float = 0.0,
    ):
        """Record a vehicle route assignment."""
        if not self._enabled:
            return

        row = [
            f"{sim_time:.2f}",
            "ASSIGN",
            vehicle_name,
            from_node,
            to_node,
            "ASSIGNED",
            "",
            "ASSIGNED",
            path_length,
            f"{idle_elapsed:.2f}",
        ]
        key = tuple(row)
        if key == self._last_vehicle_key:
            return
        self._last_vehicle_key = key
        self._vehicle_rows.append(row)

    def log_vehicle_reposition(
        self,
        sim_time: float,
        vehicle_name: str,
        from_node: str,
        to_node: str,
        path_length: int,
        idle_elapsed: float = 0.0,
    ):
        """
        Record an idle repositioning move.

        Args:
            sim_time: simulation time
            vehicle_name: vehicle name
            from_node: current node
            to_node: repositioning destination node
            path_length: path length
            idle_elapsed: continuous IDLE time right before repositioning (seconds)
        """
        if not self._enabled:
            return

        row = [
            f"{sim_time:.2f}",
            "REPOSITION",
            vehicle_name,
            from_node,
            to_node,
            "REPOSITIONING",
            "",
            "REPOSITIONING",
            path_length,
            f"{idle_elapsed:.2f}",
        ]
        key = tuple(row)
        if key == self._last_vehicle_key:
            return
        self._last_vehicle_key = key
        self._vehicle_rows.append(row)

    def log_deadlock(
        self,
        sim_time: float,
        cycles: List[List[str]],
    ):
        """Record a deadlock detection."""
        if not self._enabled:
            return

        for i, cycle in enumerate(cycles):
            self._vehicle_rows.append([
                f"{sim_time:.2f}",
                "DEADLOCK",
                f"cycle_{i}",
                "",
                "",
                f"DEADLOCK ({len(cycle)} vehicles)",
                " → ".join(cycle),
                "DEADLOCK",
                len(cycle),
                "",
            ])

    def log_idle_summary(
        self,
        sim_time: float,
        idle_stats: Dict[str, Dict[str, float]],
    ):
        """
        Record the per-vehicle IDLE time summary at the end of the simulation.
        Pass the result of VehicleTracker.get_idle_stats() directly.

        Args:
            sim_time: current simulation time
            idle_stats: {vehicle_name: {total_idle_time, current_idle_time,
                                        idle_count, idle_ratio}}
        """
        if not self._enabled:
            return

        for veh_name, stat in idle_stats.items():
            self._vehicle_rows.append([
                f"{sim_time:.2f}",
                "IDLE_SUMMARY",
                veh_name,
                "",
                "",
                f"total={stat['total_idle_time']:.2f}s "
                f"ratio={stat['idle_ratio']:.1%} "
                f"count={int(stat['idle_count'])}",
                "",
                "IDLE_SUMMARY",
                "",
                f"{stat['current_idle_time']:.2f}",
            ])

    # ─── Statistics ──────────────────────────────────────────

    def get_stats(self) -> Dict[str, int]:
        """Accumulated log row counts"""
        return {
            "pathfind_count": len(self._pathfind_rows),
            "dispatch_count": len(self._dispatch_rows),
            "vehicle_event_count": len(self._vehicle_rows),
        }

    # ─── CSV output ──────────────────────────────────────────

    def save(self, output_dir: str) -> List[str]:
        """
        Save the logs as CSV files.

        Args:
            output_dir: output directory

        Returns:
            List of saved file paths
        """
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved: List[str] = []

        # 1. Route search log (a header-only file is always written even with zero rows)
        path = os.path.join(output_dir, f"route_pathfind_{ts}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "sim_time", "from_node", "to_node", "result",
                "path_length", "cost", "is_fallback",
                "static_path_length", "detour_ratio", "context", "path_preview",
            ])
            if self._pathfind_rows:
                writer.writerows(self._pathfind_rows)
        saved.append(path)

        # 2. Assignment log (a header-only file is always written even with zero rows)
        path = os.path.join(output_dir, f"route_dispatch_{ts}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "sim_time", "target_section_id", "selected_oht", "result",
                "strategy", "idle_count", "total_count", "reason",
            ])
            if self._dispatch_rows:
                writer.writerows(self._dispatch_rows)
        saved.append(path)

        # 3. Vehicle move/status log (a header-only file is always written even with zero rows)
        path = os.path.join(output_dir, f"route_vehicle_{ts}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "sim_time", "event_type", "vehicle_name",
                "from_node", "to_node", "detail",
                "blocked_by_or_cycle", "status", "path_length",
                "idle_elapsed",
            ])
            if self._vehicle_rows:
                writer.writerows(self._vehicle_rows)
        saved.append(path)

        return saved


# ── Singleton ─────────────────────────────────────────────────

_instance: Optional[LogisticsLogger] = None


def get_logistics_logger() -> LogisticsLogger:
    """Return the LogisticsLogger singleton"""
    global _instance
    if _instance is None:
        _instance = LogisticsLogger()
    return _instance