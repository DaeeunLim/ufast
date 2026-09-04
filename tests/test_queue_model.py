"""Unit tests for the 'queue' congestion model (capacity blocking).

Verifies only the core logic of the AMHSExecutor queue model without the full
stack (RouteManager + rail loading) — FIFO wake-up of waiters, wait-for cycle
detection, forced entry.
"""
import sys
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ufast.cosim.amhs import AMHSExecutor  # noqa: E402


def _bare_executor(**attrs):
    """A minimal-state AMHSExecutor created without going through __init__."""
    ex = object.__new__(AMHSExecutor)
    ex.congestion_model = 'queue'
    ex.section_inflight = {}
    ex.section_capacity = {}
    ex._waiting = defaultdict(deque)
    ex._blocked_since = {}
    ex._section_vehicles = defaultdict(set)
    ex._deadlock_check_pending = False
    ex.total_blocked_events = 0
    ex.total_blocked_time = 0.0
    ex.deadlock_forced = 0
    ex.blocked_by_section = {}
    ex.blocked_time_by_section = {}
    ex.measurement_start_time = 0.0
    ex.instance = None
    for k, v in attrs.items():
        setattr(ex, k, v)
    return ex


class _FakeOHT:
    def __init__(self, name):
        self.name = name


class TestDeadlockDetection:
    def test_two_cycle_detected_earliest_waiter_is_victim(self):
        # A waits for sec2 (occupied by B), B waits for sec1 (occupied by A)
        ex = _bare_executor(
            _blocked_since={'A': (2, 10.0), 'B': (1, 5.0)},
            _section_vehicles=defaultdict(set, {1: {'A'}, 2: {'B'}}),
        )
        assert ex._find_deadlock_victim() == 'B'  # the one that has waited longer

    def test_mover_in_target_means_no_deadlock(self):
        # A moving (non-blocked) vehicle M in sec2 means it can resolve naturally — not a deadlock
        ex = _bare_executor(
            _blocked_since={'A': (2, 10.0), 'B': (1, 5.0)},
            _section_vehicles=defaultdict(set, {1: {'A'}, 2: {'B', 'M'}}),
        )
        assert ex._find_deadlock_victim() is None

    def test_three_cycle_detected(self):
        ex = _bare_executor(
            _blocked_since={'A': (2, 3.0), 'B': (3, 2.0), 'C': (1, 1.0)},
            _section_vehicles=defaultdict(
                set, {1: {'A'}, 2: {'B'}, 3: {'C'}}),
        )
        assert ex._find_deadlock_victim() == 'C'

    def test_chain_without_cycle_is_not_deadlock(self):
        # A → B (B waits for a mover, no cycle) — no cycle
        ex = _bare_executor(
            _blocked_since={'A': (2, 10.0), 'B': (3, 5.0)},
            _section_vehicles=defaultdict(
                set, {2: {'B'}, 3: {'M'}}),
        )
        assert ex._find_deadlock_victim() is None


class TestWakeWaiters:
    def test_fifo_order_and_capacity_respected(self):
        ex = _bare_executor(
            section_capacity={7: 2},
            section_inflight={7: 0},
        )
        admitted = []

        def fake_enter(instance, oht, job, next_idx, t, force=False):
            admitted.append(oht.name)
            ex.section_inflight[7] = ex.section_inflight.get(7, 0) + 1

        ex.on_section_enter = fake_enter
        for name in ('A', 'B', 'C'):
            ex._waiting[7].append((_FakeOHT(name), object(), 1))
            ex._blocked_since[name] = (7, 0.0)

        ex._wake_waiters(7, now=100.0)

        assert admitted == ['A', 'B']          # FIFO, only up to capacity 2
        assert [w[0].name for w in ex._waiting[7]] == ['C']
        assert 'C' in ex._blocked_since
        assert ex.total_blocked_time == 200.0  # A and B each waited 100s


class TestForceAdmit:
    def test_force_admit_bypasses_capacity(self):
        ex = _bare_executor(
            section_capacity={7: 1},
            section_inflight={7: 1},   # full
        )
        calls = []

        def fake_enter(instance, oht, job, next_idx, t, force=False):
            calls.append((oht.name, force))

        ex.on_section_enter = fake_enter
        oht = _FakeOHT('A')
        ex._waiting[7].append((oht, object(), 1))
        ex._blocked_since['A'] = (7, 0.0)

        ex._force_admit('A', t=50.0)

        assert calls == [('A', True)]
        assert not ex._waiting[7]
        assert 'A' not in ex._blocked_since
        assert ex.deadlock_forced == 1
        assert ex.total_blocked_time == 50.0


class TestCongestionMultiplier:
    def test_queue_mode_has_no_delay_multiplier(self):
        ex = _bare_executor(section_inflight={5: 9})
        ex.congestion_alpha = 0.05
        assert ex._congestion_multiplier(5) == 1.0
