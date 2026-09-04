"""
TimelineRecorder
================
두 시뮬레이터(물류/생산) 공통의 "비디오 녹화/재생" 엔진.

설계 개념
---------
* 시뮬레이션이 진행되는 동안 일정 *시뮬레이션 시간 간격*마다 Snapshot 을 기록한다.
  (실시간 간격이 아니라 sim-time 간격이므로 speed 와 무관하게 일정하다.)
* 사용자가 진행바(slider)를 드래그하면, 해당 sim-time 에 가장 가까운
  스냅샷을 찾아 화면을 그 시점 상태로 되돌린다 (= 과거 재생).
* 스냅샷은 "그 시점의 화면을 다시 그리는 데 필요한 최소 상태"만 담는다.
  - 물류: 각 OHT 의 (section_id, section 진입 시각, status) + EQ 상태 요약
  - 생산: 집계 지표(throughput, WIP, 가동률) + 머신 상태 요약

스냅샷이 sim-time 기준이므로 "1x = 실제 1초당 sim 1초" 가 보장되면
타임라인 위치와 실제 경과 시간이 정확히 대응된다.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Snapshot:
    """단일 시점의 화면 복원용 상태."""
    sim_time: float                      # 이 스냅샷이 나타내는 시뮬레이션 시각(초)
    mode: str                            # "logistics" | "production"
    # 물류용: {oht_name: {"section_id": int, "enter_time": float, "status": str}}
    ohts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # 물류용: {eq_name: {"status": str, "lot_count": int}}
    eqs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # 공통 지표 (생산/물류 모두): 화면 상단 통계 패널용
    metrics: Dict[str, Any] = field(default_factory=dict)


class TimelineRecorder:
    """
    스냅샷을 sim-time 오름차순으로 보관하고, 임의 시각에 대한
    최근접(<=) 스냅샷을 빠르게 조회한다.
    """

    def __init__(self, snapshot_interval: float = 1.0):
        # snapshot_interval: sim-time 기준 기록 간격(초). 작을수록 부드럽지만 메모리↑
        self.snapshot_interval = max(1e-6, float(snapshot_interval))
        self._snaps: List[Snapshot] = []
        self._times: List[float] = []          # bisect 용 sim_time 키 배열
        self._last_recorded_time: float = -1e18
        self.mode: str = "logistics"

    # ── 기록 ────────────────────────────────────────────────
    def reset(self, mode: str = "logistics", snapshot_interval: Optional[float] = None):
        self._snaps.clear()
        self._times.clear()
        self._last_recorded_time = -1e18
        self.mode = mode
        if snapshot_interval is not None:
            self.snapshot_interval = max(1e-6, float(snapshot_interval))

    def maybe_record(self, sim_time: float, builder) -> bool:
        """
        sim_time 이 직전 기록 + interval 을 넘었으면 builder()를 호출해
        스냅샷을 만들어 저장한다. builder 는 인자 없는 콜러블이며 Snapshot 을 반환.
        반환값: 실제로 기록했는지 여부.
        """
        if sim_time - self._last_recorded_time < self.snapshot_interval:
            return False
        snap = builder()
        if snap is None:
            return False
        self.record(snap)
        return True

    def record(self, snap: Snapshot):
        """이미 만들어진 스냅샷을 강제로 추가(시작/종료 시점 보장용)."""
        # sim_time 단조 증가 가정. 동일/역행 시각은 마지막 것으로 대체.
        if self._times and snap.sim_time <= self._times[-1] + 1e-9:
            self._snaps[-1] = snap
            self._times[-1] = snap.sim_time
        else:
            self._snaps.append(snap)
            self._times.append(snap.sim_time)
        self._last_recorded_time = snap.sim_time

    # ── 조회 ────────────────────────────────────────────────
    @property
    def duration(self) -> float:
        return self._times[-1] if self._times else 0.0

    @property
    def start_time(self) -> float:
        return self._times[0] if self._times else 0.0

    def __len__(self) -> int:
        return len(self._snaps)

    @property
    def snapshots(self) -> List[Snapshot]:
        """기록된 모든 스냅샷 리스트(시간순). 시계열 CSV/그래프 작성용."""
        return list(self._snaps)

    def at(self, sim_time: float) -> Optional[Snapshot]:
        """sim_time 이하의 가장 최근 스냅샷. 없으면 첫 스냅샷."""
        if not self._snaps:
            return None
        idx = bisect.bisect_right(self._times, sim_time) - 1
        if idx < 0:
            idx = 0
        return self._snaps[idx]

    def at_fraction(self, frac: float) -> Optional[Snapshot]:
        """0.0~1.0 비율 위치의 스냅샷."""
        if not self._snaps:
            return None
        frac = max(0.0, min(1.0, frac))
        t = self.start_time + frac * (self.duration - self.start_time)
        return self.at(t)
