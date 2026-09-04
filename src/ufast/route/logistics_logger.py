"""
logistics_logger.py - RouteManager 물류 로그 (CSV)

경로 탐색, OHT 배차, 차량 이동/상태 변화를 CSV 파일로 기록한다.
시뮬레이션 완료 후 save()를 호출하면 3개의 CSV가 생성된다:

  1. route_pathfind_{ts}.csv  - 경로 탐색 결과
  2. route_dispatch_{ts}.csv - OHT 배차 결정
  3. route_vehicle_{ts}.csv  - 차량 이동 및 상태 변화

사용 패턴:
    from ufast.route.logistics_logger import get_logistics_logger

    logger = get_logistics_logger()
    logger.reset()

    # 각 모듈에서 자동 호출됨 (RouteManager, Dispatcher, VehicleTracker)
    logger.log_pathfind(...)
    logger.log_dispatch(...)
    logger.log_vehicle_move(...)

    # 시뮬레이션 종료 시
    logger.save("logs")
"""

from __future__ import annotations
import os
import csv
from datetime import datetime
from typing import List, Optional, Dict, Any


class LogisticsLogger:
    """
    RouteManager 물류 로그 기록기.

    3종류의 CSV 로그를 메모리에 누적한 뒤 save()로 일괄 저장한다.
    DEBUG 레벨: 모든 세부 동작을 기록.
    """

    def __init__(self):
        self._pathfind_rows: List[List[Any]] = []
        self._dispatch_rows: List[List[Any]] = []
        self._vehicle_rows: List[List[Any]] = []
        self._enabled: bool = True
        self._last_dispatch_key = None
        self._last_vehicle_key = None

    def reset(self):
        """로그 초기화 (시뮬레이션 시작 시 호출)"""
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

    # ─── 경로 탐색 로그 ──────────────────────────────────────

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
        경로 탐색 결과를 기록한다.

        Args:
            sim_time: 시뮬레이션 시각 (초)
            from_node: 출발 노드
            to_node: 도착 노드
            path: 탐색된 경로 (노드 리스트)
            cost: 경로 비용 (이동 시간)
            path_length: 경로 노드 수
            is_fallback: 정적 경로 폴백 여부
            static_path_length: 정적 경로 노드 수
            detour_ratio: 우회 비율
            context: 탐색 호출 컨텍스트 (예: DISPATCH_PROBE, SECTION_ROUTE)
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
        """설비 간 경로 탐색 결과 (get_route_by_eq)"""
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

    # ─── 배차 로그 ───────────────────────────────────────────

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
        OHT 배차 결정을 기록한다.

        Args:
            sim_time: 시뮬레이션 시각
            target_section_id: 이송 요청 섹션 ID
            selected_oht: 선택된 OHT 이름 (None이면 배차 실패)
            strategy_name: 사용된 전략 이름
            idle_count: IDLE OHT 수
            total_count: 전체 OHT 수
            reason: 선택 사유 (같은 섹션, 최근접, 폴백 등)
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

    # ─── 차량 이동/상태 로그 ─────────────────────────────────

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
        차량 이동을 기록한다.

        Args:
            sim_time: 시뮬레이션 시각
            vehicle_name: 차량 이름
            from_node: 이전 노드
            to_node: 다음 노드
            success: 이동 성공 여부
            blocked_by: 이동 실패 시 차단한 차량 이름
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
        차량 상태 변화를 기록한다.
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
        """차량 등록을 기록한다."""
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
        """차량 경로 할당을 기록한다."""
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
        Idle Repositioning 이동을 기록한다.

        Args:
            sim_time: 시뮬레이션 시각
            vehicle_name: 차량 이름
            from_node: 현재 노드
            to_node: 재배치 목적지 노드
            path_length: 경로 길이
            idle_elapsed: 재배치 직전까지의 연속 IDLE 시간 (초)
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
        """데드락 감지를 기록한다."""
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
        시뮬레이션 종료 시점의 차량별 IDLE 시간 요약을 기록한다.
        VehicleTracker.get_idle_stats() 결과를 그대로 전달하면 된다.

        Args:
            sim_time: 현재 시뮬레이션 시각
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

    # ─── 통계 조회 ───────────────────────────────────────────

    def get_stats(self) -> Dict[str, int]:
        """누적 로그 건수"""
        return {
            "pathfind_count": len(self._pathfind_rows),
            "dispatch_count": len(self._dispatch_rows),
            "vehicle_event_count": len(self._vehicle_rows),
        }

    # ─── CSV 저장 ────────────────────────────────────────────

    def save(self, output_dir: str) -> List[str]:
        """
        로그를 CSV 파일로 저장한다.

        Args:
            output_dir: 저장 디렉토리

        Returns:
            저장된 파일 경로 리스트
        """
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved: List[str] = []

        # 1. 경로 탐색 로그 (행이 0건이어도 헤더 파일은 항상 생성)
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

        # 2. 배차 로그 (행이 0건이어도 헤더 파일은 항상 생성)
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

        # 3. 차량 이동/상태 로그 (행이 0건이어도 헤더 파일은 항상 생성)
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


# ── 싱글턴 ────────────────────────────────────────────────────

_instance: Optional[LogisticsLogger] = None


def get_logistics_logger() -> LogisticsLogger:
    """LogisticsLogger 싱글턴 반환"""
    global _instance
    if _instance is None:
        _instance = LogisticsLogger()
    return _instance