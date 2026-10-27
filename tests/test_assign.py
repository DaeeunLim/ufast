"""Regression tests for AMHSExecutor._assign — joining the empty and loaded legs.

Section-level routes run between section endpoints, so the empty leg often ends in a
section next to the one the loaded leg starts in. That case used to fall through to
the "both legs empty" branch and the job was delivered in 0 s without touching the rail.
"""
import contextlib
import io
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ufast.cosim.amhs import AMHSExecutor  # noqa: E402
from ufast.cosim.heap_instance import HeapInstance  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class _ChainBridge:
    """A straight rail N0→…→N7, two nodes per section (10, 20, 30, 40).

    Section routes are canned, endpoint to endpoint like SectionNodeBridge:
    the empty leg to N5 stops at the exit of section 20, and the loaded leg
    from N5 starts at the entry of section 30.
    """

    def __init__(self):
        self.section_to_nodes = {10: ['N0', 'N1'], 20: ['N2', 'N3'],
                                 30: ['N4', 'N5'], 40: ['N6', 'N7']}
        self.node_to_section = {n: s for s, ns in self.section_to_nodes.items()
                                for n in ns}
        self.routes = {(10, 30): ['N0', 'N1', 'N2', 'N3'],
                       (30, 40): ['N4', 'N5', 'N6', 'N7']}

    def get_section_for_node(self, node):
        return self.node_to_section.get(node)

    def estimate_section_route_cost(self, from_sec, to_sec, context=''):
        return [], 0.0, list(self.routes[(from_sec, to_sec)])

    def on_oht_enter_section(self, sec, update_penalty=False):
        pass

    def on_oht_leave_section(self, sec, update_penalty=False):
        pass


def _chain_executor(congestion_model):
    # every link is 5 m at 5 m/s, and the stub kinematics charge dist/speed → 1 s per link
    links = [SimpleNamespace(from_node=f'N{i}', to_node=f'N{i + 1}',
                             distance=5000.0, link_type='LINE')
             for i in range(7)]
    rm = SimpleNamespace(network=SimpleNamespace(links=links))
    kin = SimpleNamespace(path_time=lambda edges: sum(d / v for d, v in edges))
    ex = AMHSExecutor(rm, kin, num_oht=1, oht_start_nodes=['N0'],
                      bridge=_ChainBridge(), dispatch_strategy='fifo',
                      congestion_model=congestion_model)
    ex.instance = HeapInstance()
    return ex


def test_empty_leg_ending_in_neighbour_section_travels_both_legs():
    for model in ('queue', 'off'):
        ex = _chain_executor(model)
        delivered = []
        ex.request_transport(object(), 'N5', 'N7', 0.0,
                             lambda t, dur: delivered.append((t, dur)))
        job = ex.instance._events[0][2].job

        assert job.section_path == [10, 20, 30, 40]
        assert job.pickup_idx == 1
        assert (job.empty_t, job.loaded_t, job.free_flow) == (3.0, 3.0, 6.0)

        ex.instance.pop_until(100.0)
        assert delivered == [(6.0, 6.0)]
        assert job.dur >= job.free_flow
        assert (job.empty_actual, job.loaded_actual) == (3.0, 3.0)
        assert ex.total_transitions == 3
        assert all(c == 0 for c in ex.section_inflight.values())
        assert ex.idle and ex.idle[0].status == 'IDLE'


def test_case1_no_instant_delivery_between_different_nodes():
    from ufast.cosim import results as R
    from ufast.cosim.run_fromto import run_fromto

    jobs = []
    orig = AMHSExecutor.on_arrive

    def spy(self, instance, oht, job, t):
        orig(self, instance, oht, job, t)
        jobs.append(job)

    AMHSExecutor.on_arrive = spy
    old = R.DEFAULT_RESULTS_DIR
    try:
        with tempfile.TemporaryDirectory() as d:
            R.DEFAULT_RESULTS_DIR = d
            with contextlib.redirect_stdout(io.StringIO()):
                run_fromto(str(ROOT / 'dataset' / 'case1.rail'),
                           str(ROOT / 'dataset' / 'case1_Fromto.dat'),
                           num_oht=10, sim_duration_s=1800.0, seed=0,
                           verbose=False)
    finally:
        AMHSExecutor.on_arrive = orig
        R.DEFAULT_RESULTS_DIR = old

    moved = [j for j in jobs if j.from_node != j.to_node]
    assert len(moved) > 100
    assert [j for j in moved if j.dur <= 0.0 or j.free_flow <= 0.0] == []
    assert all(j.dur >= j.free_flow - 1e-9 for j in moved)
