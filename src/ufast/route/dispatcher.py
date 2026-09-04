"""
dispatcher.py - OHT 디스패칭 (배차 전략)

이송 요청 발생 시 어떤 OHT를 배차할지 결정하는 전략 모듈.
전략 패턴으로 구현하여 교체 가능하게 한다.

사용 패턴:
    dispatcher = Dispatcher(route_manager, bridge)
    dispatcher.strategy = NearestIdleStrategy()  # 기본
    # 또는
    dispatcher.strategy = CostBasedStrategy()    # 비용 기반

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
    """OHT 객체가 만족해야 하는 인터페이스 (duck typing)"""
    name: str
    status: str
    current_section_id: int


class DispatchStrategy(ABC):
    """디스패칭 전략 인터페이스"""

    @abstractmethod
    def select(
        self,
        target_section_id: int,
        idle_ohts: List[OHTInfo],
        route_manager: RouteManager,
        bridge: SectionNodeBridge,
    ) -> Optional[str]:
        """
        배차할 OHT 선택.

        Args:
            target_section_id: 이송 요청이 발생한 섹션 ID (from_eq가 있는 곳)
            idle_ohts: IDLE 상태의 OHT 목록
            route_manager: 경로 탐색기
            bridge: 섹션↔노드 브릿지

        Returns:
            선택된 OHT의 이름. 배차 불가능하면 None.
        """
        ...





def _estimate_section_graph_cost(bridge: SectionNodeBridge, from_sec_id: int, to_sec_id: int) -> float:
    """
    RouteManager/bridge 노드 경로가 실패할 때 사용하는 섹션 그래프 기반 보조 비용.

    기존 route 패키지의 cost 구조는 그대로 두고, 배차 후보 평가에서만 fallback으로 사용한다.
    이 fallback이 없으면 bridge.estimate_section_route_cost()가 실패한 후보들이 모두 동일하게
    탈락하고, 결과적으로 idle_ohts[0]만 반복 선택되는 편향이 생길 수 있다.
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
    가장 가까운 IDLE OHT 배차 (기본 전략).

    1순위: 같은 섹션의 IDLE OHT
    2순위: 전체 IDLE OHT 중 경로 비용이 가장 낮은 OHT

    기존 VehicleController.assign_oht()를 개선한 버전.
    기존은 "아무 IDLE OHT"를 반환했지만,
    이 전략은 경로 비용을 기준으로 가장 가까운 OHT를 선택한다.
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

        # 1순위: 같은 섹션
        same_section = [
            oht for oht in idle_ohts
            if oht.current_section_id == target_section_id
        ]
        if same_section:
            return same_section[0].name

        # 2순위: 실제 section route 기준 비용 평가
        best_oht: Optional[str] = None
        best_cost = float('inf')

        for oht in idle_ohts:
            _, cost, _ = bridge.estimate_section_route_cost(
                oht.current_section_id,
                target_section_id,
                context="DISPATCH_PROBE",
            )

            # bridge/node 기반 비용 산정이 실패하면 기존 simulator section graph로 보조 평가한다.
            # 이렇게 해야 일부 OHT만 반복 선택되는 현상을 막고, 현재 존재하는 IDLE OHT 전체가
            # 배차 후보로 정상 비교된다.
            if cost < 0:
                cost = _estimate_section_graph_cost(
                    bridge, oht.current_section_id, target_section_id
                )

            if 0 <= cost < best_cost:
                best_cost = cost
                best_oht = oht.name

        # 모든 비용 평가가 실패한 경우에만 기존 동작처럼 첫 IDLE OHT로 폴백한다.
        if best_oht is None and idle_ohts:
            return idle_ohts[0].name

        return best_oht


class SameSectionFirstStrategy(DispatchStrategy):
    """
    기존 VehicleController.assign_oht()와 동일한 단순 전략.
    1) 같은 섹션의 IDLE OHT
    2) 아무 IDLE OHT
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
    혼잡도 인식 배차 전략.

    경로 비용뿐만 아니라, 경로상 섹션의 혼잡도를 고려한다.
    혼잡한 경로를 통과해야 하는 OHT보다
    한적한 경로의 OHT를 우선 선택한다.

    score = route_cost + congestion_weight * 총_경로_혼잡도
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

        # 같은 섹션 우선
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

            # 경로상 혼잡도 합산
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
    OHT 디스패처.

    전략 패턴으로 배차 알고리즘을 교체할 수 있다.

    Usage:
        dispatcher = Dispatcher(route_manager, bridge)

        # 전략 변경
        dispatcher.strategy = CongestionAwareStrategy(congestion_weight=3.0)

        # 배차
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

        # 통계
        self.total_dispatches: int = 0
        self.failed_dispatches: int = 0
        self.same_section_dispatches: int = 0

    def dispatch(
        self,
        target_section_id: int,
        oht_dict: Dict[str, OHTInfo],
    ) -> Optional[str]:
        """
        이송 요청에 대해 배차할 OHT를 선택.

        Args:
            target_section_id: from_eq가 있는 섹션 ID
            oht_dict: {oht_name: OHT} 전체 OHT 딕셔너리

        Returns:
            선택된 OHT 이름. 배차 불가능하면 None.
        """
        self.total_dispatches += 1

        # IDLE OHT 필터링
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

        # ── 통계 갱신 + 사유 판단 ──
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

        # ── 물류 로그 ──
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
        """배차 통계"""
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
