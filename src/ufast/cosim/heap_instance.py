"""
ufast/heap_instance.py — shim for driving AMHSExecutor standalone, without UFastInstance.

In production-mode ufast, UFastInstance owns the event queue and events are
registered through add_event(). In fromto-only mode (no production layer),
HeapInstance takes over that role.
"""
from __future__ import annotations
import heapq
from typing import List, Tuple, Any


class HeapInstance:
    """Lightweight shim exposing add_event() / done for AMHSExecutor."""

    def __init__(self):
        self._events: List[Tuple[float, int, Any]] = []  # (timestamp, seq, event)
        self.current_time: float = 0.0
        self._seq: int = 0  # sequence number for tie-breaking equal timestamps
        self.processed: int = 0  # number of events processed by pop_until

    # ── Interface compatible with UFastInstance ──────────────
    def add_event(self, event) -> None:
        heapq.heappush(self._events, (event.timestamp, self._seq, event))
        self._seq += 1

    @property
    def done(self) -> bool:
        return not self._events

    # ── Helpers used by the headless / real-time drivers ─────
    def pop_until(self, target: float) -> None:
        """Process every event scheduled at or before target."""
        while self._events and self._events[0][0] <= target:
            t, _seq, evt = heapq.heappop(self._events)
            self.current_time = t
            self.processed += 1
            evt.handle(self)

    def pending_count(self) -> int:
        """Number of events not yet processed."""
        return len(self._events)

    def next_event_time(self) -> float:
        """Timestamp of the next event (inf if none)."""
        return self._events[0][0] if self._events else float('inf')
