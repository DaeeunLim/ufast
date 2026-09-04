"""
dispatcher.py - OHT dispatching (assignment strategies)

Strategy module that decides which OHT to assign when a transport request arrives.
Implemented with the strategy pattern so the policy can be swapped.

Usage pattern:
    dispatcher = Dispatcher(route_manager, bridge)
    dispatcher.strategy = NearestIdleStrategy()  # default
    # or
    dispatcher.strategy = CostBasedStrategy()    # cost based

    oht_name = dispatcher.dispatch(from_section_id, oht_dict)
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from ufast.core.components import OHT

from .route_manager import RouteManager
from .bridge import SectionNodeBridge
from .logistics_logger import get_logistics_logger


class OHTInfo(Protocol):
    """Interface an OHT object must satisfy (duck typing)"""
    name: str
    status: str
    current_section_id: int


class DispatchStrategy(ABC):
    """Dispatching strategy interface"""

    @abstractmethod
    def select(
        self,
        target_section_id: int,
        idle_ohts: List[OHTInfo],
        route_manager: RouteManager,
        bridge: SectionNodeBridge,
    ) -> Optional[str]:
        """
        Select the OHT to assign.

        Args:
            target_section_id: section ID where the transport request originated (location of from_eq)
            idle_ohts: list of OHTs in IDLE status
            route_manager: route finder
            bridge: section ↔ node bridge

        Returns:
            Name of the selected OHT, or None if no assignment is possible.
        """
        ...





def _estimate_section_graph_cost(bridge: SectionNodeBridge, from_sec_id: int, to_sec_id: int) -> float:
    """
    Auxiliary cost based on the section graph, used when the RouteManager/bridge node route fails.

    Leaves the existing cost structure of the route package untouched; used only as a fallback
    when evaluating assignment candidates. Without this fallback, every candidate for which
    bridge.estimate_section_route_cost() fails is dropped identically, which can bias the
    result toward repeatedly selecting idle_ohts[0].
    """
    if from_sec_id == to_sec_id:
        return 0.0

    ds = getattr(bridge, "ds", None)
    if ds is None:
        return float("inf")

    sec_index = getattr(ds, "section_id_to_index", {})
    if from_sec_id not in sec_index or to_sec_id not in sec_index:
        return float("inf")

    import heapq

    dist = {from_sec_id: 0.0}
    pq = [(0.0, from_sec_id)]
    visited = set()

    while pq:
        cur_cost, cur_sec_id = heapq.heappop(pq)
        if cur_sec_id in visited:
            continue
        visited.add(cur_sec_id)
        if cur_sec_id == to_sec_id:
            return cur_cost

        idx = sec_index.get(cur_sec_id)
        if idx is None:
            continue
        sec = ds.sections[idx]
        next_sections = list(getattr(sec, "next_sections", []) or [])
        for next_sec_id in next_sections:
            next_idx = sec_index.get(next_sec_id)
            if next_idx is None:
                continue
            next_sec = ds.sections[next_idx]
            length = float(getattr(next_sec, "length", 0.0) or 1.0)
            congestion = 0.0
            try:
                congestion = float(bridge.get_section_congestion(next_sec_id))
            except Exception:
                congestion = 0.0
            new_cost = cur_cost + max(length, 1.0) + congestion * 10.0
            if new_cost < dist.get(next_sec_id, float("inf")):
                dist[next_sec_id] = new_cost
                heapq.heappush(pq, (new_cost, next_sec_id))

    return float("inf")


class NearestIdleStrategy(DispatchStrategy):
    """
    Assign the nearest IDLE OHT (default strategy).

    Priority 1: an IDLE OHT in the same section
    Priority 2: the IDLE OHT with the lowest route cost among all IDLE OHTs

    An improved version of the former VehicleController.assign_oht().
    The old version returned "any IDLE OHT", whereas this strategy
    selects the nearest OHT by route cost.
    """

    def select(
        self,
        target_section_id: int,
        idle_ohts: List[OHTInfo],
        route_manager: RouteManager,
        bridge: SectionNodeBridge,
    ) -> Optional[str]:
        if not idle_ohts:
            return None

        # Priority 1: same section
        same_section = [
            oht for oht in idle_ohts
            if oht.current_section_id == target_section_id
        ]
        if same_section:
            return same_section[0].name

        # Priority 2: evaluate cost using the actual section route
        best_oht: Optional[str] = None
        best_cost = float('inf')

        for oht in idle_ohts:
            _, cost, _ = bridge.estimate_section_route_cost(
                oht.current_section_id,
                target_section_id,
                context="DISPATCH_PROBE",
            )

            # If the bridge/node-based cost estimate fails, fall back to the simulator's section graph.
            # This prevents only a few OHTs from being selected repeatedly and ensures all currently
            # IDLE OHTs are properly compared as assignment candidates.
            if cost < 0:
                cost = _estimate_section_graph_cost(
                    bridge, oht.current_section_id, target_section_id
                )

            if 0 <= cost < best_cost:
                best_cost = cost
                best_oht = oht.name

        # Only if every cost evaluation failed, fall back to the first IDLE OHT as before.
        if best_oht is None and idle_ohts:
            return idle_ohts[0].name

        return best_oht


class SameSectionFirstStrategy(DispatchStrategy):
    """
    Simple strategy identical to the former VehicleController.assign_oht().
    1) an IDLE OHT in the same section
    2) any IDLE OHT
    """

    def select(
        self,
        target_section_id: int,
        idle_ohts: List[OHTInfo],
        route_manager: RouteManager,
        bridge: SectionNodeBridge,
    ) -> Optional[str]:
        if not idle_ohts:
            return None

        for oht in idle_ohts:
            if oht.current_section_id == target_section_id:
                return oht.name

        return idle_ohts[0].name


class CongestionAwareStrategy(DispatchStrategy):
    """
    Congestion-aware assignment strategy.

    Considers not only the route cost but also the congestion of the sections
    along the route. An OHT with a quiet route is preferred over one that
    would have to travel through congested sections.

    score = route_cost + congestion_weight * total_route_congestion
    """

    def __init__(self, congestion_weight: float = 2.0):
        self.congestion_weight = congestion_weight

    def select(
        self,
        target_section_id: int,
        idle_ohts: List[OHTInfo],
        route_manager: RouteManager,
        bridge: SectionNodeBridge,
    ) -> Optional[str]:
        if not idle_ohts:
            return None

        # Same section first
        same_section = [
            oht for oht in idle_ohts
            if oht.current_section_id == target_section_id
        ]
        if same_section:
            return same_section[0].name

        best_oht: Optional[str] = None
        best_score = float('inf')

        for oht in idle_ohts:
            sec_path, cost, _ = bridge.estimate_section_route_cost(
                oht.current_section_id,
                target_section_id,
                context="DISPATCH_PROBE",
            )
            if cost < 0:
                cost = _estimate_section_graph_cost(
                    bridge, oht.current_section_id, target_section_id
                )
                sec_path = []
            if cost == float('inf'):
                continue

            # Sum the congestion along the route
            congestion = sum(
                bridge.get_section_congestion(sid)
                for sid in sec_path
            )

            score = cost + self.congestion_weight * congestion

            if score < best_score:
                best_score = score
                best_oht = oht.name

        if best_oht is None and idle_ohts:
            return idle_ohts[0].name

        return best_oht


class Dispatcher:
    """
    OHT dispatcher.

    The assignment algorithm can be swapped via the strategy pattern.

    Usage:
        dispatcher = Dispatcher(route_manager, bridge)

        # Change strategy
        dispatcher.strategy = CongestionAwareStrategy(congestion_weight=3.0)

        # Assign
        oht_name = dispatcher.dispatch(target_sec_id, oht_dict)
    """

    def __init__(
        self,
        route_manager: RouteManager,
        bridge: SectionNodeBridge,
        strategy: Optional[DispatchStrategy] = None,
    ):
        self.rm = route_manager
        self.bridge = bridge
        self.strategy: DispatchStrategy = strategy or NearestIdleStrategy()
        self._log = get_logistics_logger()

        # Statistics
        self.total_dispatches: int = 0
        self.failed_dispatches: int = 0
        self.same_section_dispatches: int = 0

    def dispatch(
        self,
        target_section_id: int,
        oht_dict: Dict[str, OHTInfo],
    ) -> Optional[str]:
        """
        Select the OHT to assign for a transport request.

        Args:
            target_section_id: section ID where from_eq is located
            oht_dict: {oht_name: OHT} dictionary of all OHTs

        Returns:
            Name of the selected OHT, or None if no assignment is possible.
        """
        self.total_dispatches += 1

        # Filter IDLE OHTs
        idle_ohts = [
            oht for oht in oht_dict.values()
            if oht.status == "IDLE"
        ]

        if not idle_ohts:
            self.failed_dispatches += 1
            self._log.log_dispatch(
                sim_time=self.rm.sim_time,
                target_section_id=target_section_id,
                selected_oht=None,
                strategy_name=type(self.strategy).__name__,
                idle_count=0,
                total_count=len(oht_dict),
                reason="NO_IDLE_OHT",
            )
            return None

        selected = self.strategy.select(
            target_section_id, idle_ohts,
            self.rm, self.bridge,
        )

        # ── Update statistics + determine the reason ──
        reason = ""
        if selected is None:
            self.failed_dispatches += 1
            reason = "NO_IDLE_OHT" if not idle_ohts else "NO_ROUTE"
        elif any(
            oht.name == selected and oht.current_section_id == target_section_id
            for oht in idle_ohts
        ):
            self.same_section_dispatches += 1
            reason = "SAME_SECTION"
        else:
            reason = "NEAREST_COST"

        # ── Logistics log ──
        self._log.log_dispatch(
            sim_time=self.rm.sim_time,
            target_section_id=target_section_id,
            selected_oht=selected,
            strategy_name=type(self.strategy).__name__,
            idle_count=len(idle_ohts),
            total_count=len(oht_dict),
            reason=reason,
        )

        return selected

    def get_stats(self) -> Dict[str, any]:
        """Assignment statistics"""
        return {
            'total': self.total_dispatches,
            'failed': self.failed_dispatches,
            'same_section': self.same_section_dispatches,
            'success_rate': (
                (self.total_dispatches - self.failed_dispatches)
                / self.total_dispatches
                if self.total_dispatches > 0 else 0.0
            ),
        }

    def reset_stats(self):
        self.total_dispatches = 0
        self.failed_dispatches = 0
        self.same_section_dispatches = 0
