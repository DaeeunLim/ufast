"""
route_manager.py - 통합 API

Network, PathFinder, VehicleTracker를 조합한 고수준 인터페이스.
Java RouteManager + Rail.cLARouteManger의 통합 사용 패턴을 제공한다.

시뮬레이터의 EventHandler가 이 클래스를 통해 경로를 탐색한다.
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
    AMHS 레일 네트워크 경로 관리자.

    Java의 RouteManager + Rail.cLARouteManger 패턴을 통합.
    시뮬레이터에서의 사용 패턴:

        # 초기화
        rm = RouteManager()
        rm.load_from_rail("layout.rail")
        rm.initialize()

        # 경로 탐색
        path = rm.get_route("node_1", "node_100")
        path = rm.get_route_by_eq("EQ_A", "EQ_B")

        # 차량 관리 (시뮬레이션용)
        rm.register_vehicle("OHT_1", "node_10")
        rm.assign_route("OHT_1", "node_100")
    """

    def __init__(self):
        self.network: Network = Network()
        self.pathfinder: PathFinder = PathFinder(self.network)
        self.tracker: VehicleTracker = VehicleTracker(self.network)

        # 설비간 비용 테이블 캐시
        self._eq_cost_table: Optional[Dict[str, Tuple[float, float]]] = None

        # 우회 제한 통계
        self._detour_fallback_count: int = 0
        self._total_route_count: int = 0

        # 물류 로거 & 현재 시뮬레이션 시각 (외부에서 갱신)
        self._log = get_logistics_logger()
        self._sim_time: float = 0.0

        # DISPATCH_PROBE/TRANSITION_PROBE는 내부 후보 평가용 반복 탐색이다.
        # 대형 layout에서는 이 probe 로그가 수천~수만 행으로 커져 GUI 정지를 유발하므로 기본적으로 생략한다.
        # 실제 배차/차량/최종 경로 기능은 유지된다.
        self.log_probe_pathfind: bool = False

    @property
    def sim_time(self) -> float:
        return self._sim_time

    @sim_time.setter
    def sim_time(self, value: float):
        self._sim_time = value
        # VehicleTracker의 시각도 동기화
        self.tracker.sim_time = value
        # Pathfinder 동적 캐시 TTL 판단을 위해 시각 동기화
        self.pathfinder._sim_time = value

    # ─── 초기화 ────────────────────────────────

    def load_from_rail(self, filepath: str) -> Network:
        """
        .rail 파일에서 네트워크 로드.
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
        """이미 구축된 Network 객체 사용"""
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
        네트워크 초기화 (이동 시간 계산).
        Java: Rail.initRouteManger()
        """
        self.pathfinder.initialize(
            line_speed=line_speed,
            curve_speed=curve_speed,
            vehicle_length=vehicle_length,
            vehicle_width=vehicle_width,
        )

    def set_custom_cost_function(self, cost_function):
        """외부 routing cost function을 주입한다. None이면 기존 비용식 그대로 사용한다."""
        self.pathfinder.set_custom_cost_function(cost_function)

    def configure_detour_limit(
        self,
        max_detour_ratio: float = 1.5,
        penalty_weight: float = 0.5,
        penalty_cap: float = 3.0,
    ):
        """
        우회 제한 파라미터 설정 (A+B 방식).

        Args:
            max_detour_ratio: 정적 경로 대비 최대 우회 비율.
                동적 경로의 노드 수가 정적 경로 * ratio를 넘으면
                penalty를 무시한 정적 경로로 폴백한다.
                0이면 비활성 (우회 무제한). 기본값 1.5.
            penalty_weight: traffic_penalty 영향 비율 (0.0~1.0).
                0.0이면 penalty 완전 무시, 1.0이면 원본 그대로.
                기본값 0.5 (penalty 영향을 절반으로 감쇠).
            penalty_cap: penalty 상한값.
                raw_penalty가 아무리 높아도 effective penalty는
                1 + weight * cap 을 넘지 않는다. 기본값 3.0.

        예시:
            # 보수적: 약간의 우회만 허용, penalty 영향 작게
            rm.configure_detour_limit(1.3, 0.3, 2.0)

            # 관대: 우회를 넉넉히 허용, penalty 영향 크게
            rm.configure_detour_limit(2.0, 0.8, 5.0)

            # 비활성: 기존 동작과 동일 (우회 제한 없음)
            rm.configure_detour_limit(0, 1.0, 999)
        """
        self.pathfinder.max_detour_ratio = max_detour_ratio
        self.pathfinder.penalty_weight = penalty_weight
        self.pathfinder.penalty_cap = penalty_cap
        self.pathfinder.clear_static_cache()

    # ─── 경로 탐색 ────────────────────────────────

    def get_route(
        self,
        from_node: str,
        to_node: str,
        context: str = "GENERAL",
    ) -> List[str]:
        """
        노드 간 최단 경로 탐색 (우회 제한 적용).

        max_detour_ratio가 설정되어 있으면 과도한 우회 시
        정적 경로로 폴백한다. 폴백 빈도는 get_detour_stats()로 확인.

        Returns:
            노드 이름 리스트. 경로 없으면 빈 리스트.
        """
        path, _ = self.get_route_with_cost(from_node, to_node, context=context)
        return path

    def get_route_with_cost(
        self,
        from_node: str,
        to_node: str,
        context: str = "GENERAL",
    ) -> Tuple[List[str], float]:
        """경로와 이동 시간 반환"""
        path, cost = self.pathfinder.path_search(from_node, to_node)

        # ── 물류 로그 기록 ──
        # 내부 probe 탐색은 기능 검증용 핵심 로그가 아니라 후보 평가용 반복 호출이다.
        # 생략하지 않으면 대형 SMAT2022에서 path 문자열 생성/메모리 누적 자체가 주기적 멈춤을 만든다.
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
        설비 간 최단 경로 탐색.
        설비명을 노드명으로 변환 후 경로 탐색.
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
        설비 간 이동 시간과 거리 반환.
        캐시된 비용 테이블이 있으면 그것을 사용.

        Returns:
            (time, distance). 경로 없으면 (inf, inf).
        """
        from_node = self.network.eq_to_node.get(from_eq)
        to_node = self.network.eq_to_node.get(to_eq)

        if from_node is None or to_node is None:
            return (float('inf'), float('inf'))

        # 캐시된 테이블 확인
        if self._eq_cost_table:
            key = f"{from_node}_{to_node}"
            if key in self._eq_cost_table:
                return self._eq_cost_table[key]

        _, cost = self.pathfinder.path_search(from_node, to_node)
        if cost < 0:
            return (float('inf'), float('inf'))
        return (cost, 0.0)  # distance는 별도 계산 필요

    # ─── 비용 테이블 ────────────────────────────────

    def build_eq_cost_table(
        self,
        eq_names: Optional[List[str]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        """
        설비 간 비용 테이블 생성 및 캐시.
        Java: Rail.makeFromToCostTable()
        """
        self._eq_cost_table = self.pathfinder.build_eq_cost_table(eq_names)
        return self._eq_cost_table

    # ─── 경로 변환 ────────────────────────────────

    def node_path_to_section_path(self, node_path: List[str]) -> List[str]:
        """
        노드 경로를 섹션 경로로 변환.

        연속된 두 노드(cur, next)를 모두 포함하는 섹션만 선택한다.
        분기점 노드가 여러 섹션에 속할 때 잘못된 섹션이 끼어드는 문제를 방지.

        Args:
            node_path: 노드 이름 리스트

        Returns:
            섹션 이름 리스트 (중복 제거, 순서 유지)
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

            # 두 노드가 공통으로 속한 섹션만 선택
            cur_secs  = set(cur_node.section_list)
            next_secs = set(next_node.section_list)
            common    = cur_secs & next_secs

            for sec_name in cur_node.section_list:   # 순서 유지를 위해 cur 기준 순회
                if sec_name in common and sec_name not in seen:
                    section_path.append(sec_name)
                    seen.add(sec_name)

        return section_path

    # ─── 차량 관리 (시뮬레이션용) ────────────────────

    def register_vehicle(
        self,
        name: str,
        node_name: Optional[str] = None,
    ) -> VehicleState:
        """차량 등록"""
        state = self.tracker.register_vehicle(name, node_name)
        self._log.log_vehicle_register(self.sim_time, name, node_name or "")
        return state

    def assign_route(
        self,
        vehicle_name: str,
        to_node: str,
    ) -> List[str]:
        """
        차량에 목적지까지의 경로 할당.

        Returns:
            할당된 경로. 경로 없으면 빈 리스트.
        """
        vehicle = self.tracker.vehicles.get(vehicle_name)
        if vehicle is None or vehicle.current_node is None:
            return []

        from_node = vehicle.current_node
        path = self.get_route(from_node, to_node)
        if path:
            # IDLE → ASSIGNED 전환 전에 IDLE 시간 누적
            idle_elapsed = self.tracker.flush_idle_time(vehicle_name)

            self.tracker.set_path(vehicle_name, path)
            vehicle.status = "ASSIGNED"
            self._log.log_vehicle_assign(
                self.sim_time, vehicle_name, from_node, to_node,
                len(path), idle_elapsed,
            )
        return path

    def move_vehicle(self, vehicle_name: str) -> bool:
        """차량을 경로상 다음 노드로 이동"""
        vehicle = self.tracker.vehicles.get(vehicle_name)
        from_node = vehicle.current_node if vehicle else ""
        next_node = vehicle.next_node if vehicle else ""

        success = self.tracker.move_vehicle(vehicle_name)

        # ── 이동 로그 ──
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
        """차량이 다음 노드로 이동 가능한지 확인"""
        return self.tracker.can_move(vehicle_name)

    def detect_deadlock(self) -> List[List[str]]:
        """데드락 탐지"""
        cycles = self.tracker.detect_deadlock()
        if cycles:
            self._log.log_deadlock(self.sim_time, cycles)
        return cycles

    # ─── 우회 제한 통계 ────────────────────────────

    def get_detour_stats(self) -> Dict[str, any]:
        """
        우회 제한 통계 반환.

        Returns:
            {
                'total_searches': 전체 경로 탐색 횟수,
                'fallback_count': 정적 경로로 폴백된 횟수,
                'fallback_ratio': 폴백 비율 (0.0 ~ 1.0),
                'settings': 현재 우회 제한 설정,
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
        """우회 제한 통계 초기화"""
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
        IDLE 차량들에게 이동할 한산한 목적지 노드를 추천.

        traffic_penalty 기준으로 전체 네트워크에서 혼잡도가 낮은 노드를
        찾아 각 차량에 배정한다. 같은 노드를 두 차량에 중복 배정하지 않는다.

        Args:
            idle_vehicle_names: 대상 차량 목록. None이면 전체 IDLE 차량.
            sample_nodes: 후보 노드 풀. None이면 전체 사용 가능 노드.
            top_k: 차량 1대당 후보 배수. 차량 수 * top_k 개의 후보를 확보.

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
        IDLE 차량들을 한산한 구간으로 재배치.

        simulation_step의 빈 틈에서 호출.
        배차 요청이 없을 때만 실행하도록 호출부에서 조건 처리 필요.

        Returns:
            {vehicle_name: assigned_path} — 경로 할당된 차량만 포함
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

            # IDLE → REPOSITIONING 전환 전에 IDLE 시간 누적
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

    # ─── 정보 조회 ────────────────────────────────

    def get_eq_node(self, eq_name: str) -> Optional[str]:
        """설비명에 대응하는 노드명 반환"""
        return self.network.eq_to_node.get(eq_name)

    def get_node_eq(self, node_name: str) -> Optional[str]:
        """노드명에 대응하는 설비명 반환"""
        return self.network.node_to_eq.get(node_name)

    def __repr__(self) -> str:
        return (
            f"RouteManager({self.network}, "
            f"vehicles={len(self.tracker.vehicles)})"
        )