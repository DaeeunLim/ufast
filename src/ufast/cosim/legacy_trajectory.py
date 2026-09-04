"""
ufast/legacy_trajectory.py — Fromto-driven 시뮬레이션 trajectory 기록기.

legacy controllers.py 의 OHT 상태 전환(IDLE→ASSIGNED→LOADED→IDLE)을 관찰해
production 모드와 동일한 TrajectoryLog (Trip 시퀀스) 로 변환한다.
rerun_replay 가 두 모드를 동일하게 재생할 수 있게 된다.

설계:
  - 이벤트 루프 사이에 recorder.observe(t) 를 호출 — 각 OHT 의 상태 전환을 감지.
  - 상태 머신:
      IDLE → ASSIGNED  : trip 시작 (assignment_time, empty_path 초기 노드)
      section 변경      : 진행 중인 leg 의 path 에 노드 append
      ASSIGNED → LOADED : pickup (pickup_time = empty_duration 종료)
      LOADED → IDLE/REPO: 배달 완료 → Trip 확정 후 trips 에 추가
  - 노드 위치 = bridge.section_exit_node[current_section_id] (없으면 entry_node).
  - REPOSITIONING 트립은 무시 (lot 운반 아님).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional


class LegacyTrajectoryRecorder:
    """controllers.py VehicleController 의 OHT 상태를 polling 으로 관찰."""

    def __init__(self, vehicle_controller, bridge):
        self.vc = vehicle_controller
        self.bridge = bridge

        # 이전 관측 상태 (oht_id → (status, current_section_id, destination_eq, loaded_lot_info))
        self._prev: Dict[str, tuple] = {}
        # 진행 중인 trip (oht_id → 부분 dict)
        self._active_trip: Dict[str, Dict[str, Any]] = {}
        # 완료 trip
        self.trips: List[Dict[str, Any]] = []
        # OHT 초기 위치 (oht_id → node)
        self.initial_positions: Dict[str, str] = {}

    # ── 헬퍼 ────────────────────────────────────────────
    def _oht_node(self, oht) -> Optional[str]:
        """OHT 의 representative 노드 — section exit_node (fallback: entry_node)."""
        sec_id = oht.current_section_id
        if sec_id is None or sec_id < 0:
            return None
        n = self.bridge.section_exit_node.get(sec_id)
        if not n:
            n = self.bridge.section_entry_node.get(sec_id)
        return n

    def _key(self, oht) -> tuple:
        return (oht.status, oht.current_section_id,
                oht.destination_eq, oht.loaded_lot_info)

    # ── 초기 스냅샷 ─────────────────────────────────────
    def snapshot_initial(self):
        """vc.init() 직후 호출 — OHT 초기 노드 위치 + 상태 기록."""
        for name, oht in self.vc.oht_list.items():
            node = self._oht_node(oht)
            self.initial_positions[name] = node or ""
            self._prev[name] = self._key(oht)

    # ── 매 이벤트 후 관찰 ───────────────────────────────
    def observe(self, t: float):
        """이벤트 처리 후 OHT 상태 전환을 감지해 trip 데이터를 갱신."""
        for name, oht in self.vc.oht_list.items():
            prev = self._prev.get(name)
            curr = self._key(oht)
            if prev == curr:
                continue

            prev_status, prev_sec, _, _ = prev or (None, None, None, None)
            curr_status, curr_sec, _, _ = curr

            # ── 1) (IDLE / REPOSITIONING) → ASSIGNED : trip 시작 ──
            # REPOSITIONING→ASSIGNED 는 assign_oht 의 interrupt_reposition 경로.
            if prev_status in ("IDLE", "REPOSITIONING") and curr_status == "ASSIGNED":
                start_node = self._oht_node(oht)
                self._active_trip[name] = {
                    'oht_id': name,
                    'request_time': t,
                    'assignment_time': t,
                    'empty_path': [start_node] if start_node else [],
                    'loaded_path': [],
                    'pickup_time': None,
                }

            # ── 2) ASSIGNED → LOADED : pickup 완료 ──
            elif prev_status == "ASSIGNED" and curr_status == "LOADED":
                trip = self._active_trip.get(name)
                if trip is not None:
                    trip['pickup_time'] = t
                    trip['empty_duration'] = t - trip['assignment_time']
                    pickup_node = self._oht_node(oht)
                    if pickup_node:
                        # empty_path 마지막에 pickup 지점 보장
                        if not trip['empty_path'] or trip['empty_path'][-1] != pickup_node:
                            trip['empty_path'].append(pickup_node)
                        trip['loaded_path'] = [pickup_node]

            # ── 3) LOADED → IDLE/REPOSITIONING : 배달 완료 ──
            elif prev_status == "LOADED" and curr_status in ("IDLE", "REPOSITIONING"):
                trip = self._active_trip.pop(name, None)
                if trip and trip.get('pickup_time') is not None:
                    delivery_node = self._oht_node(oht)
                    if delivery_node and (not trip['loaded_path']
                                          or trip['loaded_path'][-1] != delivery_node):
                        trip['loaded_path'].append(delivery_node)
                    trip['delivery_time'] = t
                    trip['loaded_duration'] = t - trip['pickup_time']
                    trip['congestion'] = 1.0  # legacy 는 별도 congestion 측정 없음
                    # Trip dataclass 와 동일한 필드만 보관
                    self.trips.append({
                        'oht_id': trip['oht_id'],
                        'request_time': trip['request_time'],
                        'assignment_time': trip['assignment_time'],
                        'delivery_time': trip['delivery_time'],
                        'empty_path': trip['empty_path'],
                        'loaded_path': trip['loaded_path'],
                        'empty_duration': trip['empty_duration'],
                        'loaded_duration': trip['loaded_duration'],
                        'congestion': 1.0,
                    })

            # ── 4) section 변경 (진행 중 leg 에 노드 append) ──
            elif prev_sec != curr_sec and curr_sec is not None and curr_sec >= 0:
                trip = self._active_trip.get(name)
                if trip is not None:
                    node = self._oht_node(oht)
                    if node:
                        if curr_status == "ASSIGNED":
                            if not trip['empty_path'] or trip['empty_path'][-1] != node:
                                trip['empty_path'].append(node)
                        elif curr_status == "LOADED":
                            if not trip['loaded_path'] or trip['loaded_path'][-1] != node:
                                trip['loaded_path'].append(node)

            # ── 5) ASSIGNED → IDLE (pickup 실패) — 진행 trip 폐기 ──
            elif prev_status == "ASSIGNED" and curr_status == "IDLE":
                self._active_trip.pop(name, None)

            self._prev[name] = curr

    # ── TrajectoryLog 빌드 ──────────────────────────────
    def build_log_data(self) -> Dict[str, Any]:
        """저장용 dict (TrajectoryLog.save 와 동일 키 구조)."""
        return {
            'trips': self.trips,
            'oht_initial_positions': self.initial_positions,
            # fromto 모드는 production 머신 활동 없음 — 빈 리스트/딕셔너리
            'machine_activities': [],
            'family_sizes': {},
        }
