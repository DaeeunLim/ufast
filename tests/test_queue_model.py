"""'queue' 혼잡 모델 (capacity blocking) 단위 테스트.

전체 스택(RouteManager + rail 로드) 없이 AMHSExecutor 의 queue 모델
핵심 로직만 검증한다 — 대기열 FIFO wake, wait-for 사이클 탐지, 강제 진입.
"""
import sys
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ufast.cosim.amhs import AMHSExecutor  # noqa: E402


def _bare_executor(**attrs):
    """__init__ 을 거치지 않은 최소 상태의 AMHSExecutor."""
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
        # A 는 sec2 대기 (sec2 는 B 가 점유), B 는 sec1 대기 (sec1 은 A 가 점유)
        ex = _bare_executor(
            _blocked_since={'A': (2, 10.0), 'B': (1, 5.0)},
            _section_vehicles=defaultdict(set, {1: {'A'}, 2: {'B'}}),
        )
        assert ex._find_deadlock_victim() == 'B'  # 더 오래 기다린 쪽

    def test_mover_in_target_means_no_deadlock(self):
        # sec2 에 이동 중(비차단) 차량 M 이 있으면 자연 해소 가능 — 데드락 아님
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
        # A → B (B 는 사이클 없이 mover 를 기다림) — 사이클 없음
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

        assert admitted == ['A', 'B']          # FIFO, 용량 2 까지만
        assert [w[0].name for w in ex._waiting[7]] == ['C']
        assert 'C' in ex._blocked_since
        assert ex.total_blocked_time == 200.0  # A, B 각각 100s 대기


class TestForceAdmit:
    def test_force_admit_bypasses_capacity(self):
        ex = _bare_executor(
            section_capacity={7: 1},
            section_inflight={7: 1},   # 가득 참
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
