"""
시뮬레이션 컨트롤러: OHT 관리(VehicleController) + 이벤트 처리(EventHandler)
"""
import heapq
import os
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple
from ufast.core.data_set import SimulatorDataSet, Event
from ufast.common.fromto_parser import generate_fixed_interval_events

# 재배치 로그는 건당 1줄이라 콘솔을 압도한다 — 디버깅 시에만 켠다.
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
        self.route_manager = route_manager  # route 패키지 RouteManager (None이면 fallback)
        self.bridge = bridge                # SectionNodeBridge (None이면 fallback)
        self.dispatcher = None              # Dispatcher (start_simulation에서 주입)
        self.routing_strategy = None        # 외부 Routing 전략(.py/.pkl), None이면 기존 로직
        self.assignment_strategy = None     # 외부 Assignment 전략(.py/.pkl), None이면 기존 로직
        # 기존 idle repositioning 기능은 유지하되, 실제 LOT 요청 배차 시에는
        # REPOSITIONING 차량도 "유휴 이동 중인 차량"으로 보아 필요 시 즉시 회수한다.
        # 기본 Dispatcher/NearestIdleStrategy는 그대로 두고, 그 위에 얹는 보조 선택 계층이다.
        self.allow_repositioning_dispatch_preemption = True
        self.repositioning_dispatch_margin = 0.0

    def set_custom_strategies(self, routing_strategy=None, assignment_strategy=None):
        """시뮬레이션 시작 시 UI에서 선택한 외부 전략을 주입한다."""
        self.routing_strategy = routing_strategy
        self.assignment_strategy = assignment_strategy

    def _custom_strategy_name(self, strategy) -> str:
        if describe_strategy is not None:
            return describe_strategy(strategy)
        return type(strategy).__name__ if strategy is not None else "Default"

    def init(self):
        """OHT 생성 후 섹션에 분산 배치"""
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
        배차 후보 OHT가 target section까지 도달하는 대략 비용을 계산한다.
        기존 routing/bridge 로직을 바꾸지 않고 후보 비교에만 사용한다.

        [최적화] 배차 비용은 절대값이 아닌 후보 간 상대 비교용이므로
        traffic penalty를 무시한 정적(캐시) 경로를 사용한다.
        path_search_static 결과는 영구 캐시되어 반복 호출 비용이 거의 0이다.
        """
        if oht.current_section_id == target_section_id:
            return 0.0

        if self.bridge is not None:
            try:
                # 정적(penalty 무시) 캐시 경로 사용 → Dijkstra 재수행 없음
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
        LOT 요청 배차 시 REPOSITIONING 차량을 추가 후보로 본다.

        핵심 원칙:
        - 기존 idle repositioning 기능은 유지한다.
        - 기존 Dispatcher/NearestIdleStrategy도 그대로 사용한다.
        - 다만 REPOSITIONING은 실제로는 유휴 차량의 이동 상태이므로,
          가까운 차량이 이미 지나가고 있으면 해당 유휴 이동을 중단하고 작업에 투입한다.
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

        # IDLE 후보가 없거나, REPOSITIONING 후보가 더 가까우면 회수한다.
        if idle_candidate is None or best_repo_cost <= idle_cost + self.repositioning_dispatch_margin:
            self._interrupt_reposition(best_repo)
            return best_repo

        return None

    def _get_rm_node_for_section(self, sec_id: int) -> Optional[str]:
        """
        섹션 ID → RouteManager 노드명.
        bridge.section_entry_node를 우선 사용 (rm.network와 동일한 노드명 체계).
        없으면 buf.initial_node에서 N_ 제거 후 시도.
        """
        bridge = self.bridge
        if bridge:
            node = bridge.section_entry_node.get(sec_id)
            if node and node in self.route_manager.network.nodes:
                return node

        # fallback: buf.initial_node에서 N_ 제거
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
        초기 OHT가 한 구역에 몰리지 않도록 섹션 중심 좌표를 이용해
        공간적으로 분산된 순서를 만든다.
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
        OHT를 공간적으로 분산되도록 초기 배치한다.
        기존 로그/생성 흐름은 유지하되, 초기 OHT가 한 영역에 몰리지 않게
        discrete한 bin 순서로 섹션을 순회한다.
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
        target_section_id 근처의 IDLE OHT를 찾아 반환한다.
        Dispatcher가 있으면 위임, 없으면 기존 로직 사용.
        """
        # 외부 Assignment 전략이 있으면 우선 적용한다.
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
                    print(f"[ASSIGNMENT] ⚠️  커스텀 전략 반환값 사용 불가 → 기본 배차 fallback: {selected}")
                except Exception as e:
                    print(f"[ASSIGNMENT] ⚠️  커스텀 전략 오류 → 기본 배차 fallback: {e}")

        # ✅ Dispatcher 사용 (NearestIdleStrategy 등)
        if self.dispatcher is not None:
            oht_name = self.dispatcher.dispatch(target_section_id, self.oht_list)
            selected_idle = self.oht_list.get(oht_name) if oht_name else None

            # 추가 계층: 기본 Dispatcher가 IDLE만 보더라도, REPOSITIONING은 유휴 이동 상태이므로
            # LOT 요청에 더 가까우면 기존 repositioning을 중단하고 작업에 투입한다.
            repo_candidate = self._select_repositioning_candidate_for_dispatch(target_section_id, selected_idle)
            if repo_candidate is not None:
                return repo_candidate

            if selected_idle is not None:
                return selected_idle

        ds = self.data_set

        # 1. 같은 섹션의 IDLE OHT
        for oht in self.oht_list.values():
            if oht.status == "IDLE" and oht.current_section_id == target_section_id:
                repo_candidate = self._select_repositioning_candidate_for_dispatch(target_section_id, oht)
                return repo_candidate if repo_candidate is not None else oht

        # 2. 인접 섹션 IDLE OHT
        target_idx = ds.section_id_to_index.get(target_section_id)
        if target_idx is not None:
            target_sec = ds.sections[target_idx]
            adjacent = set(target_sec.next_sections + target_sec.prev_sections)
            for oht in self.oht_list.values():
                if oht.status == "IDLE" and oht.current_section_id in adjacent:
                    repo_candidate = self._select_repositioning_candidate_for_dispatch(target_section_id, oht)
                    return repo_candidate if repo_candidate is not None else oht

        # 3. 아무 IDLE OHT
        for oht in self.oht_list.values():
            if oht.status == "IDLE":
                repo_candidate = self._select_repositioning_candidate_for_dispatch(target_section_id, oht)
                return repo_candidate if repo_candidate is not None else oht

        # 4. IDLE 없으면 REPOSITIONING OHT 즉시 중단하고 배차
        #    같은 섹션 → 인접 섹션 → 아무 REPOSITIONING 순으로 선택
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
        REPOSITIONING 중인 OHT를 즉시 중단하고 IDLE로 전환.
        assign_oht()에서 IDLE OHT가 없을 때 호출한다.
        """
        oht.status = "IDLE"
        oht.path_in_section_ids = []
        oht.destination_eq = None

        # VehicleTracker 상태도 동기화
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
        경로 탐색 우선순위:
        1) bridge.find_section_route() — 섹션↔노드 매핑 + traffic_penalty 반영
        2) route_manager 직접 탐색 (bridge 없을 때)
        3) 기존 섹션 Dijkstra (fallback)
        """
        if from_sec_id == to_sec_id:
            return []

        # 외부 Routing 전략이 있으면 우선 적용한다.
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
                print(f"[ROUTING] ⚠️  커스텀 경로 없음/무효 → 기본 routing fallback: {from_sec_id} → {to_sec_id}")
            except Exception as e:
                print(f"[ROUTING] ⚠️  커스텀 전략 오류 → 기본 routing fallback: {e}")

        # ── 1) bridge 사용 ─────────────────────────────────
        if self.bridge is not None:
            result = self.bridge.find_section_route(from_sec_id, to_sec_id)
            if result:
                return result
            print(f"[ROUTING] ⚠️  bridge 경로 없음 → fallback: {from_sec_id} → {to_sec_id}")

        # ── 2) route_manager 직접 탐색 ────────────────────
        elif self.route_manager is not None:
            result = self._get_route_via_node(from_sec_id, to_sec_id)
            if result:
                return result
            print(f"[ROUTING] ⚠️  route 패키지 경로 없음 → fallback: {from_sec_id} → {to_sec_id}")

        # ── 3) fallback: 기존 섹션 Dijkstra ───────────────
        return self._get_route_by_section(from_sec_id, to_sec_id)

    def _get_route_via_node(self, from_sec_id: int, to_sec_id: int) -> List[int]:
        """route 패키지 RouteManager로 노드 기반 경로 탐색 후 섹션 ID 리스트 반환"""
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
        """섹션 ID → RouteManager 노드명 (EQ 매핑 우선, 없으면 버퍼 initial_node)"""
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
        """NetworkSection 이름(숫자 문자열) → ds.sections의 section_id 변환"""
        try:
            sec_id = int(name)
            if sec_id in self.data_set.section_id_to_index:
                return sec_id
        except ValueError:
            pass
        return None

    def _get_route_by_section(self, from_sec_id: int, to_sec_id: int) -> List[int]:
        """기존 섹션 단위 Dijkstra"""
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


# ─── OHT 기본 속도 (mm/s) ───
OHT_SPEED = 1000.0


class EventHandler:
    def __init__(self, vehicle_controller: VehicleController):
        self.vc = vehicle_controller
        self.ds = SimulatorDataSet.get_instance()
        # ── reposition 쿨다운 (C) ──
        # 각 OHT가 마지막으로 IDLE이 된 시각.
        # 요구사항: IDLE OHT는 레일 위에 멈춰 있으면 안 됨(통행 차단/충돌).
        # 따라서 쿨다운은 "배차 우선권 확보"를 위한 최소한으로만 둔다.
        # 이 시간 동안 근처 LOT이 들어오면 assign_oht가 잡아감.
        self._oht_idle_since: Dict[str, float] = {}
        # IDLE 후 reposition을 허용하기까지의 최소 대기 시간(초)
        # 짧게 둬서 "계속 움직이는" 요구사항을 만족시킨다.
        self.REPOSITION_COOLDOWN: float = 0.0
        # IDLE/유휴 이동 재시도 간격
        self.REPOSITION_RETRY_DELAY: float = 0.35
        # reposition_idle_ohts() 라운드로빈용 포인터
        self._repo_rr_index: int = 0
        # 유휴 재배치가 두 섹션 사이를 왕복(ping-pong)하지 않도록 최근 방문 이력 보관
        self._repo_recent_sections: Dict[str, deque] = defaultdict(lambda: deque(maxlen=8))
        self.REPOSITION_BACKTRACK_PENALTY: float = 100.0
        self.REPOSITION_RECENT_PENALTY: float = 12.0
        self.idle_positioning_strategy = None  # 외부 Idle Positioning 전략, None이면 기존 로직
        # 하이브리드 시뮬레이터 모드. from_to_only는 기존 동작을 유지한다.
        self.simulation_mode: str = "from_to_only"
        self.default_processing_time: float = 60.0
        self._lot_process_start_times: Dict[str, float] = {}

    def configure_simulation_mode(self, simulation_mode: str = "from_to_only", default_processing_time: float = 60.0):
        """시뮬레이션 모드 설정.

        - from_to_only: 기존 From-To 물류 중심 동작 유지
        - production_logistics: delivery 후 PROCESS_END 이벤트를 추가하고,
          rundown wait_start를 request 시점 기준으로 기록
        """
        self.simulation_mode = simulation_mode or "from_to_only"
        self.default_processing_time = max(0.0, float(default_processing_time))

    def set_idle_positioning_strategy(self, idle_positioning_strategy=None):
        """시뮬레이션 시작 시 UI에서 선택한 외부 Idle Positioning 전략을 주입한다."""
        self.idle_positioning_strategy = idle_positioning_strategy

    # ── Idle Repositioning ─────────────────────────────────
    def reposition_idle_ohts(self, current_time: float):
        """
        IDLE OHT를 재배치하는 함수이지만, 실제 의미는 "유휴 차량은 계속 움직인다"이다.
        즉 IDLE과 REPOSITIONING은 상태만 다를 뿐 동일한 유휴 이동 흐름으로 취급한다.

        [최적화] 한 번 호출에 처리할 OHT 수를 제한한다(최대 MAX_REPO_PER_CALL).
        라운드로빈 방식이므로 모든 IDLE OHT가 순차적으로 처리된다.
        대형 레이아웃에서 수십 대가 동시에 get_route를 호출하여 GUI가 멈추는 현상을 방지.
        """
        rm = self.vc.route_manager
        bridge = self.vc.bridge
        if rm is None or bridge is None:
            return

        MAX_REPO_PER_CALL = 10  # 한 번에 처리할 최대 IDLE OHT 수

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

        # 한 번 호출에 MAX_REPO_PER_CALL 개만 처리 (나머지는 다음 호출 때)
        for name in ordered[:MAX_REPO_PER_CALL]:
            oht = self.vc.oht_list.get(name)
            if oht is None:
                continue
            self._try_reposition(oht, current_time)

    # ── FromTo → LOT 이벤트 일괄 등록 ──────────────────────
    def init_lot_events(self, fromto_data: List[Tuple[str, str, float]],
                        sim_duration: float = 3600.0):
        """
        fromto_data: [(from_eq, to_eq, rate_per_hour), ...]
        시간 당 발생율(rate, 건/시간)을 기반으로 고정 간격(fixed interval: Δt = 3600 / rate 초)
        LOT 이벤트를 일괄 등록한다.
        """
        valid_records = [
            (fe, te, float(r)) for fe, te, r in fromto_data
            if fe in self.ds.eq_list and te in self.ds.eq_list and float(r) > 0
        ]

        if not valid_records:
            print("[EventHandler] 유효한 FromTo 레코드가 없습니다. "
                  "EQ 이름이 레이아웃과 일치하는지 확인하세요.")
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

        print(f"[EventHandler] LOT 이벤트 {len(events)}개 등록 완료 "
              f"(고정 간격 재생, 유효 레코드 {len(valid_records)}개, "
              f"스킵 {len(fromto_data) - len(valid_records)}개) "
              f"[시뮬레이션 시간: {sim_duration:.1f}초 = {sim_duration/3600:.2f}시간]")

    # ── 메인 이벤트 디스패처 ────────────────────────────────
    def process_event(self, event: Event):
        if event.event_type == "LOT":
            self._handle_lot(event)
        elif event.event_type == "TRANSFER_EXT":
            self._handle_transfer_ext(event)
        elif event.event_type == "PROCESS_END":
            self._handle_process_end(event)

    # ── LOT 이벤트 ─────────────────────────────────────────
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

        # IDLE OHT 배차
        oht = self.vc.assign_oht(from_eq.section_id)

        if oht is None:
            self.vc._log_dispatch_decision(current_time, from_eq.section_id, None, "RETRY_NO_OHT")
            # 5초 후 재시도
            retry = event.clone()
            retry.time_scheduled = current_time + 5.0
            self.ds.add_event(retry)
            return

        dispatch_reason = "DISPATCHED_IDLE" if oht.status == "IDLE" else f"DISPATCHED_{oht.status}"
        self.vc._log_dispatch_decision(current_time, from_eq.section_id, oht, dispatch_reason)

        # Lot 정보 생성 & EQ에 전달
        # 처리된 lot 수 + 현재 시각으로 고유 id 생성
        lot_serial = getattr(self.ds, '_lot_serial', 0) + 1
        self.ds._lot_serial = lot_serial
        lot_id = f"LOT_{lot_serial:05d}"
        lot_info = f"{lot_id}@{to_eq_name}@{current_time:.2f}"
        from_eq.message_receiver("LOT_CREATED", lot_info, self.vc)

        # 로거 기록
        get_logger().on_lot_created(lot_id, from_eq_name, to_eq_name, current_time)

        # 생산+물류 모드에서는 request 발생 시점을 설비 rundown 시작점으로 기록한다.
        # 기존 from-to only 모드의 측정 방식은 유지한다.
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

        # EQ에 OHT 배차 알림 (eq_status → OHT_COMING)
        from_eq.message_receiver("OHT_ASSIGNED", oht.name, self.vc)

        # 경로: OHT 현재 위치 → from_eq 섹션
        path = self.vc.get_route(oht.current_section_id, from_eq.section_id)
        oht.path_in_section_ids = path
        log = self.vc._route_log()
        if log is not None:
            log.log_vehicle_assign(
                current_time, oht.name, f"sec{oht.current_section_id}", f"sec{from_eq.section_id}", len(path), 0.0
            )

        self._schedule_next_transfer(oht, current_time)

    # ── TRANSFER_EXT 이벤트 ────────────────────────────────
    def _handle_transfer_ext(self, event: Event):
        oht = self.vc.oht_list.get(event.oht_id)
        if oht is None:
            return

        current_time = event.time_scheduled

        # ── 잔존 이벤트 차단 ──────────────────────────────────
        # REPOSITIONING → ASSIGNED 전환 시 큐에 남아있던 이전 경로의
        # TRANSFER_EXT가 뒤늦게 발동하면 OHT를 엉뚱한 섹션으로 이동시킴.
        # 이벤트 생성 시점의 to_node와 현재 path[0]가 다르면 무효 이벤트.
        if event.to_node:
            if oht.path_in_section_ids:
                if event.to_node != str(oht.path_in_section_ids[0]):
                    return  # 경로가 교체됨 → 잔존 이벤트 무시
            else:
                # path가 비었는데 to_node가 있음
                # REPOSITIONING 정상 완료 → _handle_arrival 처리
                # ASSIGNED/LOADED로 전환 후 잔존 → 무시
                if oht.status in ("ASSIGNED", "LOADED"):
                    return  # 잔존 이벤트 무시

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
                # 유휴 이동은 막힌 경로를 고집하지 말고 즉시 다른 1-hop 후보를 다시 본다.
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

            # 현재 섹션에서 제거
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

            # OHT 위치 갱신 + 거리 로깅
            get_logger().on_oht_moved(oht.name, next_section.length)
            old_sec_id = oht.current_section_id          # ✅ 이전 섹션 저장
            self.vc._log_vehicle_move_event(current_time, oht.name, old_sec_id, next_sec_id, True, "")
            oht.current_section_id = next_sec_id
            oht.current_buffer_index = 0
            oht.time_enter_current_section = current_time
            oht.path_in_section_ids.pop(0)

            if oht.status == "REPOSITIONING":
                self._remember_repo_section(oht.name, next_sec_id)

            # ✅ bridge penalty 갱신 (섹션 혼잡도 반영)
            if self.vc.bridge is not None:
                self.vc.bridge.on_oht_leave_section(old_sec_id)
                self.vc.bridge.on_oht_enter_section(next_sec_id)

            # VehicleTracker current_node 동기화 (bridge 기준 노드명)
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

        # 경로 소진 → 도착 처리
        # REPOSITIONING 중 경로가 비었는데 목적지 도착이 아닌 경우 IDLE로 안전 복귀
        if oht.status == "REPOSITIONING" and not oht.path_in_section_ids:
            pass  # _handle_arrival의 REPOSITIONING 케이스가 처리
        self._handle_arrival(oht, current_time)

    # ── 도착 처리 ──────────────────────────────────────────
    def _handle_arrival(self, oht: OHT, current_time: float):
        if oht.status == "ASSIGNED":
            # from_eq 도착 → Lot 픽업
            eq = self.ds.eq_list.get(oht.destination_eq)
            if eq and eq.port_buffer[0] is not None:
                oht.loaded_lot_info = eq.port_buffer[0]
                eq.message_receiver("LOT_TRANSPORTED", oht.name, self.vc)

            # 로거: 픽업 시각 기록
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

            # Production + Logistics 모드에서만 목적지 EQ의 생산 대기 상태를 표시한다.
            # From-To only / 물류 KPI 전용 모드에서는 processing 또는 rundown 개념을 생성하지 않고,
            # OHT가 lot을 가져다 주는 운반 이벤트만 수행한다.
            lot_id = oht.loaded_lot_info.split("@")[0] if oht.loaded_lot_info else ""
            if self.simulation_mode == "production_logistics":
                # request 시점에서 이미 LOT_INBOUND와 rundown wait가 열린다.
                # 여기서는 추가 상태 변경을 하지 않아 중복 rundown record를 방지한다.
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
            # to_eq 도착 → 배달 완료
            delivered_eq_name = self._extract_to_eq(oht.loaded_lot_info)
            delivered_eq = self.ds.eq_list.get(delivered_eq_name) if delivered_eq_name else None

            # 로거: 배달 완료 기록. 설비 가공 시작/종료는 Production + Logistics 모드에서만 기록한다.
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

            # 배달 완료 후에도 rail을 막지 않도록 즉시 유휴 이동 후보로 넣는다.
            self._oht_idle_since[oht.name] = current_time - self.REPOSITION_COOLDOWN
            self._try_reposition(oht, current_time)

        elif oht.status == "REPOSITIONING":
            # 재배치 목적지 도착 → IDLE 복귀
            old_status = oht.status
            oht.status = "IDLE"
            oht.path_in_section_ids = []
            get_logger().on_oht_status_change(oht.name, "IDLE", current_time)
            self.vc._log_vehicle_status_event(current_time, oht.name, old_status, "IDLE", oht.current_section_id, "reposition_arrived")

            # route_manager VehicleTracker 상태도 동기화
            rm = self.vc.route_manager
            if rm is not None:
                vehicle = rm.tracker.vehicles.get(oht.name)
                if vehicle:
                    vehicle.status = "IDLE"
                    vehicle.idle_since = current_time
                    vehicle.path = []
                    vehicle.path_index = 0
                    vehicle.destination_node = None

            # REPOSITIONING은 유휴 이동의 진행 상태일 뿐이므로 도착 즉시 다음 이동을 다시 시도한다.
            self._oht_idle_since[oht.name] = current_time - self.REPOSITION_COOLDOWN
            self._try_reposition(oht, current_time)

    def _handle_process_end(self, event: Event):
        """생산+물류 모드에서 설비 processing 종료를 기록한다.

        이 이벤트는 기존 From-To 운반 로직에 영향을 주지 않고, 생산 KPI 산출을
        위한 설비 상태와 processing duration만 추가 기록한다.
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
            # 이미 다른 inbound lot이 있으면 PROCESS_WAITING 상태 유지, 아니면 IDLE로 복귀
            if getattr(eq, "inbound_lot_count", 0) > 0:
                eq.eq_status = "PROCESS_WAITING"
            else:
                eq.eq_status = "IDLE"

    # ── Idle Repositioning 단일 OHT ──────────────────────
    def _sec_id_to_rm_node(self, sec_id: int, rm) -> Optional[str]:
        """섹션 ID → rm.network 노드명 변환 (N_ 접두사 처리 포함)"""
        node = self._sec_id_to_node(sec_id)
        if node is None:
            return None
        # N_ 접두사 제거
        rm_node = node.lstrip("N_") if node.startswith("N_") else node
        if rm_node in rm.network.nodes:
            return rm_node
        # 원본도 시도
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
        유휴 차량이 현재 섹션에서 즉시 한 칸이라도 이동할 수 있는 최선의 인접 섹션 선택.
        수정 포인트:
          - 바로 직전 섹션으로 되돌아가는 backtrack에 큰 페널티
          - 최근 방문 섹션 반복에도 페널티
          - next_sections를 우선 사용하고, 정말 막혔을 때만 prev_sections 고려
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

        # forward 가능한 후보가 있으면 prev는 보지 않음
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
        IDLE OHT의 재배치 목적지 노드 선택 — next_sections 기반 BFS.

        변경 배경:
          - 기존 `pathfinder.path_search` 기반은 종종 "경로 없음"을 반환해
            IDLE OHT가 레일 위에 멈춰 있는 현상 발생 (충돌/통행 차단 원인)
          - 시뮬레이터의 실제 연결 구조(next_sections)를 기준으로 BFS하면
            경로 발견율이 훨씬 높고, 실제 주행 가능 경로를 보장한다.

        전략:
          1) 현재 섹션에서 next_sections를 따라 BFS로 섹션 그래프 확장
          2) 방문하는 섹션들 중 혼잡도가 "현재 이하"인 섹션을 후보로 수집
          3) 혼잡도가 더 낮은 섹션이 있으면 그곳을 목적지로 선택
          4) 없으면 "현재와 같은 혼잡도"의 가장 가까운 섹션이라도 선택 (항상 움직임)
          5) 다른 REPOSITIONING OHT가 이미 가고 있는 섹션은 가중치 페널티
          6) 그래도 후보가 없으면 (=고립) 인접 next_section 중 아무거나
        """
        from collections import deque

        ds = self.vc.data_set
        cur_sec_id = oht.current_section_id
        cur_congestion = bridge.get_section_congestion(cur_sec_id)

        # 다른 REPOSITIONING OHT들의 목적지 섹션 (중복 집결 방지용 페널티)
        heading_sections = set()
        for other in self.vc.oht_list.values():
            if other.name == oht.name:
                continue
            if other.status == "REPOSITIONING" and other.path_in_section_ids:
                heading_sections.add(other.path_in_section_ids[-1])

        # BFS로 탐색 가능한 섹션 수집 (hop 거리 제한)
        # 너무 멀리 가면 오히려 혼잡 유발 → 보통 5~15hop이면 충분히 다른 영역 도달
        MAX_HOPS = 12
        visited = {cur_sec_id: 0}
        queue = deque([cur_sec_id])

        # (score, sec_id, hop) 튜플. score 낮을수록 좋음
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

                # 후보 평가: 혼잡도 낮을수록 + 가까울수록 좋음
                nxt_cong = bridge.get_section_congestion(nxt_id)
                # 점수: 혼잡도 * 10 + hop 거리 + heading 중복 페널티
                score = nxt_cong * 10.0 + hop + 1
                if nxt_id in heading_sections:
                    score += 5.0  # 다른 OHT가 이미 향하는 곳은 덜 매력적

                # entry node가 rm.network에 있어야 경로 변환 가능
                entry_node = bridge.section_entry_node.get(nxt_id)
                if entry_node and entry_node in rm.network.nodes:
                    candidates.append((score, nxt_id, hop + 1))

        if not candidates:
            # 완전 고립: next_sections 자체가 비어있거나 전부 탐색 실패
            # 현재 레일 구조가 막힌 것이므로 정말 움직일 수 없음
            return None

        # 점수 오름차순 정렬
        candidates.sort(key=lambda x: x[0])

        # 1순위: 현재보다 덜 혼잡한 섹션 중 최고점
        for score, sec_id, hop in candidates:
            cong = bridge.get_section_congestion(sec_id)
            if cong < cur_congestion:
                entry_node = bridge.section_entry_node.get(sec_id)
                if entry_node:
                    return entry_node

        # 2순위: 혼잡도가 현재와 같은 섹션 (같은 레벨이라도 움직여야 함)
        # — 요구사항: IDLE은 절대 멈춰 있으면 안 됨. 같은 혼잡도라도 이동.
        for score, sec_id, hop in candidates:
            cong = bridge.get_section_congestion(sec_id)
            if cong <= cur_congestion:
                entry_node = bridge.section_entry_node.get(sec_id)
                if entry_node:
                    return entry_node

        # 3순위: 그냥 가장 점수 낮은 섹션 (모두 현재보다 혼잡해도 일단 움직임)
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
        """Idle Positioning 전략 반환값을 기존 REPOSITIONING path로 변환한다."""
        if result is None:
            return None

        # dict 반환: path 또는 target section/node를 허용
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

        # int 반환: 목적 section id
        if isinstance(result, int):
            path = self._target_section_to_path(oht, result)
            return (path, result) if path else None

        # str 반환: target node 또는 'sec123'
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

        # list/tuple 반환: section path로 취급
        path = normalize_section_path(result, oht.current_section_id) if normalize_section_path else None
        path = self._valid_custom_idle_path(oht, path)
        if path:
            return path, path[-1]
        return None

    def _try_reposition(self, oht: OHT, current_time: float):
        """
        유휴 차량(IDLE)은 항상 이동하려고 시도한다.
        REPOSITIONING은 IDLE의 이동 중 상태일 뿐 별도 정책이 아니다.
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
                    print(f"[REPO] ⚠️  커스텀 Idle Positioning 반환값 무효 → 기본 재배치 fallback: {custom_result}")
            except Exception as e:
                print(f"[REPO] ⚠️  커스텀 Idle Positioning 오류 → 기본 재배치 fallback: {e}")

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

    # ── 유틸리티 ───────────────────────────────────────────
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
        """lot_info 'LOT_n@toEQ@time' → toEQ 추출"""
        if not lot_info:
            return ""
        parts = lot_info.split("@")
        return parts[1] if len(parts) >= 2 else ""
