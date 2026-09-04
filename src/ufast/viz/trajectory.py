"""
viz/trajectory.py — OHT 궤적 자료구조 + 위치 보간 + JSON 저장/로드.

ufast/amhs.py 가 매 trip 완료 시 dict 로 trip 데이터를 누적해두고,
시뮬레이션 종료 시 TrajectoryLog 로 묶어
results/<run_id>/*_trajectories.json 으로 저장.
viz/rerun_replay.py 가 그 파일을 읽어 시각 t 에서 OHT 위치를 보간한다.

보간 방식 (Phase 2 v1):
  - 경로 따라 누적 거리 비율로 선형 보간.
  - 운동학적 정확도(엣지별 시간) 대신 시각적 부드러움 우선.
  - leg 단위 분할: empty leg(OHT→픽업) → loaded leg(픽업→배달).
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# OHT 상태 라벨 (rerun 색상 키)
STATE_IDLE    = 'IDLE'
STATE_EMPTY   = 'EMPTY'    # OHT 가 픽업하러 가는 중 (빈 leg)
STATE_LOADED  = 'LOADED'   # OHT 가 lot 을 싣고 가는 중 (적재 leg)
STATE_AT_DEST = 'AT_DEST'  # 방금 배달 완료, 다음 작업 대기


@dataclass
class Trip:
    """OHT 한 회 이송 작업의 시각·경로 기록."""
    oht_id: str
    request_time: float            # 발주 시각
    assignment_time: float         # OHT 가 배정돼 출발한 시각
    delivery_time: float           # 적재물 배달 완료 시각
    empty_path: List[str]          # OHT 현재 노드 → 픽업 노드 (노드 이름 시퀀스)
    loaded_path: List[str]         # 픽업 노드 → 배달 노드
    empty_duration: float          # 빈 leg 소요 (혼잡 반영)
    loaded_duration: float         # 적재 leg 소요 (혼잡 반영)
    congestion: float              # 적용된 혼잡 계수

    @property
    def pickup_time(self) -> float:
        return self.assignment_time + self.empty_duration

    @property
    def total_duration(self) -> float:
        return self.delivery_time - self.assignment_time


@dataclass
class TrajectoryLog:
    """한 ufast 실행의 전체 궤적 로그."""
    trips: List[Trip] = field(default_factory=list)
    oht_initial_positions: Dict[str, str] = field(default_factory=dict)
    # production Machine 가동 기간 [{start, end, family}, ...] — 설비 활동 시각화용
    machine_activities: List[Dict[str, Any]] = field(default_factory=list)
    # family → 총 machine 수 — 부하율 (active/total) 기반 색상 gradient 에 사용
    family_sizes: Dict[str, int] = field(default_factory=dict)
    # F12 — KPI 시계열 snapshot tuple list:
    #   [(sim_time, busy_count, section_inflight_sum, pending_queue,
    #     delivered_count, max_section_inflight), ...]
    kpi_snapshots: List[List[Any]] = field(default_factory=list)
    # 시계열 부속 메타 — 정규화에 필요
    oht_total: int = 0

    def save(self, path: str) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({
                'trips': [asdict(t) for t in self.trips],
                'oht_initial_positions': self.oht_initial_positions,
                'machine_activities': self.machine_activities,
                'family_sizes': self.family_sizes,
                'kpi_snapshots': self.kpi_snapshots,
                'oht_total': self.oht_total,
            }, f, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> 'TrajectoryLog':
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        trips = [Trip(**t) for t in data.get('trips', [])]
        return cls(
            trips=trips,
            oht_initial_positions=data.get('oht_initial_positions', {}),
            machine_activities=data.get('machine_activities', []),
            family_sizes=data.get('family_sizes', {}),
            kpi_snapshots=data.get('kpi_snapshots', []),
            oht_total=data.get('oht_total', 0),
        )

    @classmethod
    def from_amhs_log(cls, trip_dicts: List[Dict[str, Any]],
                      initial_positions: Dict[str, str],
                      machine_activities: Optional[List[Dict[str, Any]]] = None,
                      family_sizes: Optional[Dict[str, int]] = None,
                      kpi_snapshots: Optional[List[Any]] = None,
                      oht_total: int = 0,
                      ) -> 'TrajectoryLog':
        """AMHS executor 가 누적한 raw dict 리스트 → TrajectoryLog."""
        trips = [Trip(**d) for d in trip_dicts]
        return cls(
            trips=trips,
            oht_initial_positions=initial_positions,
            machine_activities=list(machine_activities) if machine_activities else [],
            family_sizes=dict(family_sizes) if family_sizes else {},
            kpi_snapshots=[list(s) for s in (kpi_snapshots or [])],
            oht_total=oht_total,
        )


# ── 위치 보간 ───────────────────────────────────────────────

def _path_cumulative_distance(path: List[str],
                              nodes_xy: Dict[str, Tuple[float, float]]
                              ) -> Tuple[List[Tuple[float, float]], List[float]]:
    """경로 노드 좌표 + 누적 거리 배열."""
    pts = [nodes_xy[n] for n in path if n in nodes_xy]
    cum = [0.0]
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        cum.append(cum[-1] + (dx * dx + dy * dy) ** 0.5)
    return pts, cum


def _interpolate_along_path(path: List[str],
                            leg_elapsed: float,
                            leg_duration: float,
                            nodes_xy: Dict[str, Tuple[float, float]]
                            ) -> Tuple[float, float]:
    """leg_elapsed/leg_duration 비율로 경로 위 위치 선형 보간 (누적 거리 기준)."""
    pts, cum = _path_cumulative_distance(path, nodes_xy)
    if not pts:
        return (0.0, 0.0)
    if len(pts) == 1 or cum[-1] <= 0 or leg_duration <= 0:
        return pts[-1]

    frac = max(0.0, min(1.0, leg_elapsed / leg_duration))
    target = frac * cum[-1]

    # cum 에서 target 이 들어가는 구간 찾기
    for i in range(1, len(cum)):
        if cum[i] >= target:
            seg_len = cum[i] - cum[i - 1]
            seg_frac = (target - cum[i - 1]) / seg_len if seg_len > 0 else 0.0
            x = pts[i - 1][0] + seg_frac * (pts[i][0] - pts[i - 1][0])
            y = pts[i - 1][1] + seg_frac * (pts[i][1] - pts[i - 1][1])
            return (x, y)
    return pts[-1]


def interpolate_trip_position(trip: Trip, t: float,
                              nodes_xy: Dict[str, Tuple[float, float]]
                              ) -> Optional[Tuple[float, float, str]]:
    """
    Trip 의 시각 t 에서 OHT 위치와 상태.

    Returns:
        (x, y, state) — t < assignment 면 None.
        state ∈ {STATE_EMPTY, STATE_LOADED, STATE_AT_DEST}
    """
    if t < trip.assignment_time:
        return None
    if t >= trip.delivery_time:
        # 배달 완료 — 마지막 노드(=loaded_path 끝)에서 대기
        if trip.loaded_path:
            last = trip.loaded_path[-1]
            if last in nodes_xy:
                return (nodes_xy[last][0], nodes_xy[last][1], STATE_AT_DEST)
        return None

    elapsed = t - trip.assignment_time
    if elapsed < trip.empty_duration:
        # 빈 leg
        x, y = _interpolate_along_path(trip.empty_path, elapsed,
                                       trip.empty_duration, nodes_xy)
        return (x, y, STATE_EMPTY)
    else:
        # 적재 leg
        leg_elapsed = elapsed - trip.empty_duration
        x, y = _interpolate_along_path(trip.loaded_path, leg_elapsed,
                                       trip.loaded_duration, nodes_xy)
        return (x, y, STATE_LOADED)
