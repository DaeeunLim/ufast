"""
ufast/heap_instance.py — UFastInstance 없이 AMHSExecutor 단독 구동용 shim.

Production ufast 에서는 UFastInstance 가 이벤트 큐를 소유하고 add_event() 로
이벤트를 등록한다. Fromto-only 모드(생산 레이어 없음)에서는 그 역할을
HeapInstance 가 대신한다.
"""
from __future__ import annotations
import heapq
from typing import List, Tuple, Any


class HeapInstance:
    """AMHSExecutor 가 add_event() / done 을 호출할 수 있는 경량 shim."""

    def __init__(self):
        self._events: List[Tuple[float, int, Any]] = []  # (timestamp, seq, event)
        self.current_time: float = 0.0
        self._seq: int = 0  # 동점 타임스탬프 타이-브레이킹용 시퀀스
        self.processed: int = 0  # pop_until 이 처리한 이벤트 수

    # ── UFastInstance 와의 호환 인터페이스 ──────────────────
    def add_event(self, event) -> None:
        heapq.heappush(self._events, (event.timestamp, self._seq, event))
        self._seq += 1

    @property
    def done(self) -> bool:
        return not self._events

    # ── 헤드리스 / 실시간 드라이버가 사용하는 헬퍼 ──────────
    def pop_until(self, target: float) -> None:
        """target 시각까지의 이벤트를 모두 처리한다."""
        while self._events and self._events[0][0] <= target:
            t, _seq, evt = heapq.heappop(self._events)
            self.current_time = t
            self.processed += 1
            evt.handle(self)

    def pending_count(self) -> int:
        """아직 처리되지 않은 이벤트 수."""
        return len(self._events)

    def next_event_time(self) -> float:
        """다음 이벤트 시각 (없으면 inf)."""
        return self._events[0][0] if self._events else float('inf')
