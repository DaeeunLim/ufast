"""
pathfinder.py - Dijkstra 경로 탐색

Java RouteManager의 PathSearch, CostSearch를 포팅.
heapq 기반 우선순위 큐로 O(log n) 삽입 (Java의 O(n) 대비 개선).
"""

from __future__ import annotations
import heapq
from typing import Dict, List, Optional, Tuple

from .graph import Network, Node, NetworkSection


class PathFinder:
    """
    Dijkstra 기반 경로 탐색기.

    Java RouteManager의 PathSearch/CostSearch를 포팅하되,
    heapq를 사용하여 성능을 개선한다.

    우회 제한 기능 (A+B 방식):
      A) max_detour_ratio: 동적 경로가 정적 최단 경로 대비 이 비율을 넘으면
         penalty를 무시한 정적 경로로 폴백한다.
      B) penalty_weight / penalty_cap: traffic_penalty의 영향을 제한한다.
         effective_penalty = 1 + weight * min(raw_penalty - 1, cap)

    Usage:
        finder = PathFinder(network)
        finder.initialize(line_speed=2.6667, curve_speed=0.8)
        path, cost = finder.path_search("node_1", "node_100")

        # 우회 제한 설정
        finder.penalty_weight = 0.5   # penalty 영향 50%로 감쇠
        finder.penalty_cap = 3.0      # penalty 최대 3.0배까지만
        finder.max_detour_ratio = 1.5  # 정적 경로 대비 1.5배까지만 허용
    """

    MAX_COST = 1e15

    def __init__(self, network: Network):
        self.network = network
        self.line_speed: float = 2.6667   # m/s (Java 기본값)
        self.curve_speed: float = 0.8     # m/s
        self.vehicle_length: float = 1.0
        self.vehicle_width: float = 1.0

        # ─── 우회 제한 파라미터 (B: Bounded Penalty) ───
        # effective_penalty = 1 + penalty_weight * min(raw_penalty - 1, penalty_cap)
        # raw_penalty=1.0(기본)이면 effective=1.0 (영향 없음)
        self.penalty_weight: float = 1.0  # 0.0~1.0, penalty 영향 비율
        self.penalty_cap: float = 5.0     # penalty 상한값 (raw_penalty - 1 기준)

        # ─── 우회 제한 파라미터 (A: Max Detour Ratio) ───
        self.max_detour_ratio: float = 0.0  # 0이면 비활성. 1.5 = 정적 경로의 1.5배까지 허용

        # ─── 정적 경로 캐시 ───
        # {(from_node, to_node): (path, cost)}
        self._static_cache: Dict[Tuple[str, str], Tuple[List[str], float]] = {}

        # ─── 동적 경로 캐시 (penalty 반영, TTL 기반) ───
        # {(from_node, to_node): (sim_time_cached, path, cost)}
        # TTL 내에 동일 쌍의 경로 재요청 시 Dijkstra를 건너뛴다.
        self._dynamic_cache: Dict[Tuple[str, str], Tuple[float, List[str], float]] = {}
        self._dynamic_cache_ttl: float = 3.0    # sim-seconds
        self._dynamic_cache_max: int = 4096     # 메모리 상한 (항목 수)
        self._sim_time: float = 0.0             # route_manager가 동기화

        # ─── 우회 제한 통계 ───
        self.detour_fallback_count: int = 0
        self.total_search_count: int = 0
        self.cache_hit_count: int = 0

        # 외부 routing cost function. None이면 기존 비용식 그대로 사용한다.
        self.custom_cost_function = None

        # ─── scipy C 다익스트라 엔진 (fast_pathfinder) ───
        # 최초 cost_search 호출 시 lazy 구축. scipy 미설치나
        # UFAST_FAST_ROUTE=0 환경변수 시 순정 파이썬 경로 사용.
        self._fast_engine = None
        self._fast_engine_disabled = False

    def initialize(
        self,
        line_speed: float = 2.6667,
        curve_speed: float = 0.8,
        vehicle_length: float = 1.0,
        vehicle_width: float = 1.0,
    ):
        """
        네트워크 초기화: 노드 간 이동 시간 계산.
        Java: RouteManager.Initialize()

        각 섹션의 인접 노드 쌍에 대해 move_in_time = distance / speed 계산.
        LINE 섹션은 line_speed, CURVE 섹션은 curve_speed 사용.
        """
        self.line_speed = line_speed
        self.curve_speed = curve_speed
        self.vehicle_length = vehicle_length
        self.vehicle_width = vehicle_width

        # 속도/이동시간이 바뀌므로 C 엔진은 다음 쿼리에서 재구축
        self._fast_engine = None

        # 섹션별 노드 쌍에 대해 이동 시간 계산
        for section in self.network.sections.values():
            section.make_hash_table()

            speed = (self.line_speed
                     if section.section_type == "LINE"
                     else self.curve_speed)

            if speed <= 0:
                continue

            # 섹션 내 연속 노드 쌍
            for j in range(1, section.node_count):
                prev_name = section.get_node(j - 1)
                curr_name = section.get_node(j)
                if prev_name is None or curr_name is None:
                    continue

                prev_node = self.network.nodes.get(prev_name)
                curr_node = self.network.nodes.get(curr_name)
                if prev_node is None or curr_node is None:
                    continue

                distance = prev_node.get_length(curr_node)
                move_in_time = distance / speed

                # 정방향: prev → curr
                curr_node.set_move_in_time(move_in_time, prev_name, forward=True)
                # 역방향: curr → prev
                prev_node.set_move_in_time(move_in_time, curr_name, forward=False)

    def _get_fast_engine(self):
        """C 다익스트라 엔진 반환. 사용 불가 조건이면 None (순정 경로 사용)."""
        if self.custom_cost_function is not None or self._fast_engine_disabled:
            return None
        if self._fast_engine is None:
            import os
            if os.environ.get("UFAST_FAST_ROUTE", "1") == "0":
                self._fast_engine_disabled = True
                return None
            try:
                from .fast_pathfinder import FastCostEngine
                self._fast_engine = FastCostEngine(self)
            except ImportError:
                print("[FastRoute] scipy 미설치 → 순정 파이썬 cost_search 사용")
                self._fast_engine_disabled = True
                return None
        return self._fast_engine

    def notify_penalty_changed(self, node_names) -> None:
        """traffic_penalty 변경 통지 (bridge 훅). 엔진 가중치 무효화."""
        if self._fast_engine is not None:
            self._fast_engine.mark_dirty(node_names)

    def notify_all_penalties_changed(self) -> None:
        if self._fast_engine is not None:
            self._fast_engine.mark_all_dirty()

    def _effective_penalty(self, raw_penalty: float) -> float:
        """
        Bounded penalty 계산 (방안 B).

        raw_penalty가 1.0(기본)이면 결과도 1.0.
        raw_penalty가 높을수록 비용이 올라가지만, cap과 weight로 제한된다.

        공식: 1 + weight * min(raw - 1, cap)
        예) raw=4.0, weight=0.5, cap=3.0 → 1 + 0.5 * min(3.0, 3.0) = 2.5
        예) raw=10.0, weight=0.5, cap=3.0 → 1 + 0.5 * min(9.0, 3.0) = 2.5 (capped)
        예) raw=1.0 → 1.0 (no penalty)
        """
        if raw_penalty <= 1.0:
            return 1.0
        excess = raw_penalty - 1.0
        bounded = min(excess, self.penalty_cap)
        return 1.0 + self.penalty_weight * bounded

    def clear_static_cache(self):
        """정적 경로 캐시 초기화. 네트워크 구조 변경 시 호출."""
        self._static_cache.clear()
        self._dynamic_cache.clear()   # 정적 캐시 초기화 시 동적 캐시도 함께 비운다

    def clear_dynamic_cache(self):
        """동적 경로 캐시만 초기화. 네트워크 penalty 대폭 변경 시 호출."""
        self._dynamic_cache.clear()

    def set_custom_cost_function(self, cost_function):
        """외부 routing cost function을 주입한다. None이면 기존 비용식으로 복귀한다."""
        self.custom_cost_function = cost_function
        self.clear_static_cache()

    def _calculate_edge_cost(self, move_time: float, raw_penalty: float, section: NetworkSection, from_node: Node, to_node: Node, context: str = "") -> float:
        """엣지 비용 계산. 커스텀 함수가 없으면 기존 move_time * effective_penalty를 그대로 사용."""
        default_cost = move_time * self._effective_penalty(raw_penalty)
        fn = self.custom_cost_function
        if fn is None:
            return default_cost

        func = getattr(fn, "calculate_cost", None)
        if not callable(func):
            func = getattr(fn, "cost", None)
        if not callable(func) and callable(fn):
            func = fn
        if not callable(func):
            return default_cost

        call_specs = [
            ((), {
                "move_time": move_time,
                "raw_penalty": raw_penalty,
                "effective_penalty": self._effective_penalty(raw_penalty),
                "section": section,
                "from_node": from_node,
                "to_node": to_node,
                "default_cost": default_cost,
                "context": context,
            }),
            ((move_time, raw_penalty, section, from_node, to_node, default_cost), {}),
            ((move_time, raw_penalty, default_cost), {}),
            ((move_time, raw_penalty), {}),
        ]
        last_error = None
        for args, kwargs in call_specs:
            try:
                value = func(*args, **kwargs)
                return float(value)
            except TypeError as e:
                last_error = e
                continue
            except Exception as e:
                print(f"[RoutingCost] ⚠️ 커스텀 cost function 오류 → 기본 비용 사용: {e}")
                return default_cost
        if last_error is not None:
            print(f"[RoutingCost] ⚠️ 커스텀 cost function 시그니처 불일치 → 기본 비용 사용: {last_error}")
        return default_cost

    def path_search_static(
        self,
        from_node_name: str,
        to_node_name: str,
    ) -> Tuple[List[str], float]:
        """
        정적 최단 경로 탐색 (penalty 무시).

        traffic_penalty를 무시하고 순수 이동 시간만으로 경로를 찾는다.
        결과를 캐시하여 동일 쌍에 대해 재계산하지 않는다.
        detour ratio 비교의 기준선으로 사용된다 (방안 A).
        """
        cache_key = (from_node_name, to_node_name)
        if cache_key in self._static_cache:
            return self._static_cache[cache_key]

        # penalty 파라미터를 임시로 비활성화하고 탐색
        saved_weight = self.penalty_weight
        saved_cap = self.penalty_cap
        self.penalty_weight = 0.0  # penalty 완전 무시 → effective=1.0

        path, cost = self._dijkstra(from_node_name, to_node_name)

        self.penalty_weight = saved_weight
        self.penalty_cap = saved_cap

        self._static_cache[cache_key] = (path, cost)
        return path, cost

    def _reset_search(self):
        """모든 노드의 Dijkstra 상태 초기화. Java: ResetRouteSearch()"""
        for node in self.network.nodes.values():
            node.reset_search(self.MAX_COST)

    def path_search(
        self,
        from_node_name: str,
        to_node_name: str,
    ) -> Tuple[List[str], float]:
        """
        1:1 최단 경로 탐색 (우회 제한 적용).

        동작 흐름:
          1. Bounded penalty(B)가 적용된 동적 경로를 탐색한다.
          2. max_detour_ratio(A)가 설정되어 있으면:
             - 정적 최단 경로(penalty 무시)를 구한다 (캐시 활용).
             - 동적 경로의 노드 수가 정적 경로의 ratio배를 넘으면
               정적 경로로 폴백한다.

        Returns:
            (경로 노드 이름 리스트, 총 이동 시간)
            경로를 찾지 못하면 ([], -1.0)
        """
        self.total_search_count += 1

        # ── 동적 캐시 조회 ──────────────────────────────────
        # 동일한 (from, to) 쌍을 TTL 이내에 재요청하면 Dijkstra를 건너뛴다.
        # penalty가 자주 바뀌어도 TTL 내에는 이전 결과를 재활용해 CPU 절감.
        _cache_key = (from_node_name, to_node_name)
        _cached = self._dynamic_cache.get(_cache_key)
        if _cached is not None:
            _ct, _cp, _cc = _cached
            if self._sim_time - _ct <= self._dynamic_cache_ttl:
                self.cache_hit_count += 1
                return _cp, _cc

        # 동적 경로 탐색 (bounded penalty 적용)
        dynamic_path, dynamic_cost = self._dijkstra(from_node_name, to_node_name)

        if not dynamic_path:
            return [], -1.0

        # 방안 A: detour ratio 검사
        if self.max_detour_ratio > 0:
            static_path, static_cost = self.path_search_static(
                from_node_name, to_node_name,
            )

            if static_path and len(static_path) > 1:
                # 노드 수 기준으로 우회 비율 판단
                ratio = len(dynamic_path) / len(static_path)

                if ratio > self.max_detour_ratio:
                    # 과도한 우회 → 정적 경로로 폴백
                    self.detour_fallback_count += 1
                    dynamic_path, dynamic_cost = static_path, static_cost

        # ── 동적 캐시 저장 ──────────────────────────────────
        if len(self._dynamic_cache) >= self._dynamic_cache_max:
            # 간단한 크기 제한: 가장 오래된 항목 1/4 제거
            cutoff = self._sim_time - self._dynamic_cache_ttl
            stale = [k for k, v in self._dynamic_cache.items() if v[0] <= cutoff]
            for k in stale:
                del self._dynamic_cache[k]
            # 그래도 크면 전체 비우기
            if len(self._dynamic_cache) >= self._dynamic_cache_max:
                self._dynamic_cache.clear()
        self._dynamic_cache[_cache_key] = (self._sim_time, dynamic_path, dynamic_cost)

        return dynamic_path, dynamic_cost

    def _dijkstra(
        self,
        from_node_name: str,
        to_node_name: str,
    ) -> Tuple[List[str], float]:
        """
        핵심 Dijkstra 알고리즘.
        Java: RouteManager.PathSearch()

        bounded penalty(_effective_penalty)가 적용된 비용으로 탐색한다.
        """
        from_node = self.network.nodes.get(from_node_name)
        to_node = self.network.nodes.get(to_node_name)

        if from_node is None or to_node is None:
            return [], -1.0

        # 동일 노드
        if from_node_name == to_node_name:
            return [from_node_name], 0.0

        self._reset_search()

        # heapq: (arrived_time, node_name)
        heap: List[Tuple[float, str]] = []

        from_node.arrived_time = 0.0
        from_node.arrived_length = 0.0
        heapq.heappush(heap, (0.0, from_node_name))

        arrived = False

        while heap:
            curr_time, curr_name = heapq.heappop(heap)
            curr_node = self.network.nodes[curr_name]

            # 이미 더 좋은 경로가 갱신됨
            if curr_time > curr_node.arrived_time:
                continue

            # 도착 확인
            if curr_name == to_node_name:
                arrived = True
                break

            # 현재 노드가 속한 모든 섹션 탐색
            for section_name in curr_node.section_list:
                section = self.network.sections.get(section_name)
                if section is None:
                    continue

                pos = section.find_node(curr_name)
                if pos < 0:
                    continue

                # 정방향 탐색 (pos+1, pos+2, ...)
                self._explore_direction(
                    section, pos, 1, curr_node, heap,
                    from_node_name, to_node_name,
                )

                # 양방향 섹션이면 역방향도 탐색
                if section.two_way:
                    self._explore_direction(
                        section, pos, -1, curr_node, heap,
                        from_node_name, to_node_name,
                    )

        if not arrived:
            return [], -1.0

        # 경로 역추적
        path = []
        node_name = to_node_name
        while node_name is not None:
            path.append(node_name)
            node_name = self.network.nodes[node_name].prev_node
        path.reverse()

        return path, to_node.arrived_time

    def _explore_direction(
        self,
        section: NetworkSection,
        start_pos: int,
        direction: int,  # +1 = forward, -1 = backward
        search_node: Node,
        heap: List[Tuple[float, str]],
        from_node_name: str,
        to_node_name: str,
    ):
        """
        섹션 내 한 방향으로 인접 노드 탐색.
        Java PathSearch의 내부 for 루프를 포팅.
        """
        d_time = search_node.arrived_time

        # ── perf(#2): inner-loop 지역화 — node_count property / get_node 메서드
        # 호출(각 수억 회)을 리스트 직접 인덱싱으로, prev_node 재조회를
        # carry-forward 로, speed 계산을 루프 밖으로. 결과 동일(순수 리팩터).
        node_list = section.node_list
        n = len(node_list)
        gnodes = self.network.nodes
        speed = self.line_speed if section.section_type == "LINE" else self.curve_speed
        push = heapq.heappush

        prev_name = node_list[start_pos]
        prev_node = gnodes.get(prev_name)
        if prev_node is None:
            return
        j = start_pos + direction

        while 0 <= j < n:
            next_name = node_list[j]

            next_node = gnodes.get(next_name)
            if next_node is None:
                break

            # 출발 노드로 되돌아가는 것 방지 (Java: if(Node == FromNode) break)
            if next_name == from_node_name:
                break

            # 이동 시간 계산
            move_time = prev_node.move_in_times.get(next_name)
            if move_time is None:
                # move_in_time이 설정되지 않은 경우 거리 기반 계산
                distance = prev_node.get_length(next_node)
                move_time = distance / speed if speed > 0 else self.MAX_COST

            # traffic penalty 반영 (bounded) 또는 커스텀 routing cost function 적용
            edge_cost = self._calculate_edge_cost(
                move_time, next_node.traffic_penalty, section, prev_node, next_node, context="path_search"
            )
            new_time = d_time + edge_cost

            if next_node.set_arrived_time(new_time, prev_name):
                d_time = new_time
                prev_name = next_name
                prev_node = next_node  # carry-forward (was gnodes.get(prev_name))

                # NOTE:
                # 기존 구현은 섹션 끝 노드만 heap에 넣었기 때문에,
                # section 중간의 junction / branching node에서 다른 섹션으로
                # 확장하지 못하고 reachable 경로도 FAIL이 나는 문제가 있었다.
                #
                # Dijkstra 정합성을 우선하여 "갱신된 모든 노드"를 heap에 넣는다.
                # stale entry는 상위 루프의
                #   if curr_time > curr_node.arrived_time: continue
                # 로 자연스럽게 걸러진다.
                push(heap, (new_time, next_name))

                if next_name == to_node_name:
                    return
            else:
                break  # 더 좋은 경로가 이미 존재

            j += direction

    def cost_search(
        self,
        from_node_name: str,
        to_node_names: List[str],
        context: str = "cost_search",
    ) -> Dict[str, Tuple[float, float, List[str]]]:
        """
        1:N Dijkstra - 한 출발 노드에서 여러 도착 노드까지의 비용 계산.
        Java: RouteManager.CostSearch()

        Args:
            from_node_name: 출발 노드 이름
            to_node_names: 도착 노드 이름 리스트

        Returns:
            {to_node_name: (time, distance, path)} 딕셔너리.
            도달 불가능한 노드는 (MAX_COST, MAX_COST, []) 반환.
        """
        engine = self._get_fast_engine()
        if engine is not None:
            return engine.cost_search(from_node_name, to_node_names)

        from_node = self.network.nodes.get(from_node_name)
        if from_node is None:
            return {}

        # 찾아야 할 노드 집합
        remaining = set()
        for name in to_node_names:
            if name != from_node_name and name in self.network.nodes:
                remaining.add(name)

        self._reset_search()

        heap: List[Tuple[float, str]] = []
        from_node.arrived_time = 0.0
        from_node.arrived_length = 0.0
        heapq.heappush(heap, (0.0, from_node_name))

        while heap and remaining:
            curr_time, curr_name = heapq.heappop(heap)
            curr_node = self.network.nodes[curr_name]

            if curr_time > curr_node.arrived_time:
                continue

            # 도착 노드 발견
            remaining.discard(curr_name)

            # 모든 인접 섹션 탐색
            for section_name in curr_node.section_list:
                section = self.network.sections.get(section_name)
                if section is None:
                    continue

                pos = section.find_node(curr_name)
                if pos < 0:
                    continue

                # 정방향
                self._cost_explore_direction(
                    section, pos, 1, curr_node, heap, remaining, context,
                )

                # 양방향이면 역방향도
                if section.two_way:
                    self._cost_explore_direction(
                        section, pos, -1, curr_node, heap, remaining, context,
                    )

        # 결과 수집
        results: Dict[str, Tuple[float, float, List[str]]] = {}
        for name in to_node_names:
            if name == from_node_name:
                results[name] = (0.0, 0.0, [from_node_name])
                continue

            node = self.network.nodes.get(name)
            if node is None or node.arrived_time >= self.MAX_COST:
                results[name] = (self.MAX_COST, self.MAX_COST, [])
                continue

            # 경로 역추적
            path = []
            trace_name = name
            while trace_name is not None:
                path.append(trace_name)
                trace_name = self.network.nodes[trace_name].prev_node
            path.reverse()

            results[name] = (node.arrived_time, node.arrived_length, path)

        return results

    def _cost_explore_direction(
        self,
        section: NetworkSection,
        start_pos: int,
        direction: int,
        search_node: Node,
        heap: List[Tuple[float, str]],
        remaining: set,
        context: str = "cost_search",
    ):
        """
        CostSearch용 방향 탐색.
        PathSearch와 유사하지만 도착 노드가 여러 개이고
        거리(length)도 함께 추적한다.
        """
        d_time = search_node.arrived_time
        d_length = search_node.arrived_length

        prev_name = section.get_node(start_pos)
        j = start_pos + direction

        while 0 <= j < section.node_count:
            next_name = section.get_node(j)
            if next_name is None:
                break

            next_node = self.network.nodes.get(next_name)
            prev_node = self.network.nodes.get(prev_name)
            if next_node is None or prev_node is None:
                break

            # 이동 시간 및 거리 계산
            move_time = prev_node.move_in_times.get(next_name)
            distance = prev_node.get_length(next_node)

            if move_time is None:
                speed = (self.line_speed
                         if section.section_type == "LINE"
                         else self.curve_speed)
                move_time = distance / speed if speed > 0 else self.MAX_COST

            edge_cost = self._calculate_edge_cost(
                move_time, next_node.traffic_penalty, section, prev_node, next_node, context=context
            )
            new_time = d_time + edge_cost
            new_length = d_length + distance

            if next_node.set_cost_arrived_time(new_time, new_length, prev_name):
                d_time = new_time
                d_length = new_length
                prev_name = next_name

                # Dijkstra 정합성: target 의 최단거리는 heap pop 시점에만 확정된다.
                # 발견(relaxation) 시점에 remaining.discard / early-return 하면
                # 비최적(더 긴) 경로로 종료될 수 있다 → 상위 cost_search 루프에서
                # pop 시 discard(curr_name) 하는 것만 신뢰한다.
                heapq.heappush(heap, (new_time, next_name))
            else:
                break

            j += direction

    def build_eq_cost_table(
        self,
        eq_names: Optional[List[str]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        """
        설비 간 비용 테이블 생성.
        Java: Rail.makeFromToCostTable()

        Args:
            eq_names: 설비 이름 리스트. None이면 모든 매핑된 설비.

        Returns:
            {"from_node_to_node": (time, distance)} 딕셔너리
        """
        if eq_names is None:
            eq_names = list(self.network.eq_to_node.keys())

        # 설비 → 노드 변환
        eq_node_pairs = []
        for eq in eq_names:
            node_name = self.network.eq_to_node.get(eq)
            if node_name and node_name in self.network.nodes:
                eq_node_pairs.append((eq, node_name))

        to_node_names = [node_name for _, node_name in eq_node_pairs]

        cost_table: Dict[str, Tuple[float, float]] = {}

        for from_eq, from_node in eq_node_pairs:
            results = self.cost_search(from_node, to_node_names)

            for to_eq, to_node in eq_node_pairs:
                if to_node in results:
                    time, length, _ = results[to_node]
                    key = f"{from_node}_{to_node}"
                    cost_table[key] = (time, length)

        return cost_table
