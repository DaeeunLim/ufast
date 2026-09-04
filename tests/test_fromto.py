"""ufast.common.fromto_parser 및 fixed-interval 이벤트 생성 단위 테스트."""
import heapq
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ufast.common.fromto_parser import (
    load_fromto,
    group_fromto_rates,
    generate_fixed_interval_events,
)
from ufast.core.data_set import SimulatorDataSet, Event
from ufast.control.controllers import EventHandler, VehicleController


class TestFromtoParser(unittest.TestCase):
    def test_load_fromto(self):
        content = (
            "EQ_A\tEQ_B\t2.0\n"
            "EQ_B\tEQ_C\t0.5\n"
            "\n"
            "INVALID_LINE\n"
            "EQ_C\tEQ_D\tnot_a_float\n"
            "EQ_A\tEQ_B\t3.0\n"
        )
        with tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8') as f:
            f.write(content)
            tmp_path = f.name

        try:
            records = load_fromto(tmp_path)
            self.assertEqual(len(records), 3)
            self.assertEqual(records[0], ('EQ_A', 'EQ_B', 2.0))
            self.assertEqual(records[1], ('EQ_B', 'EQ_C', 0.5))
            self.assertEqual(records[2], ('EQ_A', 'EQ_B', 3.0))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_group_fromto_rates(self):
        records = [
            ('EQ_A', 'EQ_B', 1.0),
            ('EQ_A', 'EQ_B', 2.0),
            ('EQ_C', 'EQ_D', 0.5),
        ]
        grouped = group_fromto_rates(records)
        self.assertEqual(grouped[('EQ_A', 'EQ_B')], [1.0, 2.0])
        self.assertEqual(grouped[('EQ_C', 'EQ_D')], [0.5])

    def test_generate_fixed_interval_events(self):
        # Rate = 2.0 req/hr -> dt = 3600 / 2.0 = 1800s
        # offset_ratio = 0.5 -> first event at 900s, second at 2700s
        records = [('EQ_A', 'EQ_B', 2.0)]
        events = generate_fixed_interval_events(records, sim_duration=3600.0, offset_ratio=0.5)
        self.assertEqual(len(events), 2)
        self.assertAlmostEqual(events[0][0], 900.0)
        self.assertAlmostEqual(events[1][0], 2700.0)
        self.assertEqual(events[0][1:], ('EQ_A', 'EQ_B'))
        self.assertEqual(events[1][1:], ('EQ_A', 'EQ_B'))

    def test_generate_fixed_interval_events_multi_hour(self):
        # Hour 0: rate = 1.0 req/hr (dt = 3600s, first at 1800s)
        # Hour 1: rate = 2.0 req/hr (dt = 1800s, next at 1800 + 3600 = 5400s)
        records = [
            ('EQ_A', 'EQ_B', 1.0),
            ('EQ_A', 'EQ_B', 2.0),
        ]
        events = generate_fixed_interval_events(records, sim_duration=7200.0, offset_ratio=0.5)
        # Event 1 at 1800s (hour 0)
        # Event 2 at 1800 + 3600 = 5400s (hour 1)
        self.assertEqual(len(events), 2)
        self.assertAlmostEqual(events[0][0], 1800.0)
        self.assertAlmostEqual(events[1][0], 5400.0)

    def test_event_handler_init_lot_events(self):
        ds = SimulatorDataSet.get_instance()
        ds.clear()
        ds.eq_list = {'EQ_A': None, 'EQ_B': None}

        eh = EventHandler(None)
        eh.ds = ds

        # Rate = 1.0 req/hr for 1 hour -> exactly 1 event at 1800.0s
        records = [('EQ_A', 'EQ_B', 1.0)]
        eh.init_lot_events(records, sim_duration=3600.0)

        self.assertEqual(ds.lot_count, 1)
        self.assertEqual(len(ds.event_queue), 1)
        evt = heapq.heappop(ds.event_queue)
        self.assertEqual(evt.event_type, "LOT")
        self.assertAlmostEqual(evt.time_scheduled, 1800.0)
        self.assertEqual(evt.from_node, "EQ_A")
        self.assertEqual(evt.to_node, "EQ_B")


if __name__ == '__main__':
    unittest.main()


class TestFromtoBlockingEngine(unittest.TestCase):
    """ufast-fromto 가 co-simulation 과 같은 AMHSExecutor(blocking) 로 도는지 스모크."""

    def test_run_fromto_smoke(self):
        import contextlib, io, json, os, tempfile
        from ufast.cosim import results as R
        from ufast.cosim.run_fromto import run_fromto
        root = Path(__file__).resolve().parent.parent
        rail = str(root / 'dataset' / 'case1.rail')
        fromto = str(root / 'dataset' / 'case1_Fromto.dat')
        with tempfile.TemporaryDirectory() as d:
            old = R.DEFAULT_RESULTS_DIR
            R.DEFAULT_RESULTS_DIR = d
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    out = run_fromto(rail, fromto, num_oht=10,
                                     sim_duration_s=3600.0, seed=0,
                                     verbose=False)
            finally:
                R.DEFAULT_RESULTS_DIR = old
            self.assertGreater(out['jobs_requested'], 1000)
            self.assertGreater(out['jobs_completed'], 0)
            res = json.load(open(out['result_path']))
        self.assertEqual(res['meta']['mode'], 'fromto')
        self.assertEqual(res['meta']['engine'], 'amhs')
        self.assertEqual(res['meta']['congestion_model'], 'queue')
        self.assertEqual(res['meta']['vehicle']['footprint_mm'], 909.0)
        self.assertIn('blocked_events', res['amhs'])
        self.assertIn('deadlock_forced', res['amhs'])
        self.assertEqual(res['transport']['lots_completed'], out['jobs_completed'])


class TestCustomStrategyFlags(unittest.TestCase):
    """--custom-routing-cost 배선과 strategies/ 폴더 이름 폴백."""

    def test_routing_cost_plugin_and_strategies_lookup(self):
        import contextlib, io, json, os, shutil, tempfile
        from ufast.cosim import results as R
        from ufast.cosim.run_fromto import run_fromto
        from ufast.paths import REPO_ROOT
        root = Path(__file__).resolve().parent.parent
        strat_dir = os.path.join(REPO_ROOT, 'strategies')
        os.makedirs(strat_dir, exist_ok=True)
        tmp_name = '_test_cost_plugin.py'
        shutil.copy(root / 'examples' / 'custom_routing_cost_example.py',
                    os.path.join(strat_dir, tmp_name))
        try:
            with tempfile.TemporaryDirectory() as d:
                old = R.DEFAULT_RESULTS_DIR; R.DEFAULT_RESULTS_DIR = d
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        out = run_fromto(str(root / 'dataset' / 'case1.rail'),
                                         str(root / 'dataset' / 'case1_Fromto.dat'),
                                         num_oht=5, sim_duration_s=1800.0,
                                         custom_routing_cost_path=tmp_name,   # bare name → strategies/
                                         custom_assignment_path=str(root / 'examples' / 'custom_assignment_example.py'),
                                         verbose=False)
                finally:
                    R.DEFAULT_RESULTS_DIR = old
                meta = json.load(open(out['result_path']))['meta']
        finally:
            os.remove(os.path.join(strat_dir, tmp_name))
        self.assertEqual(meta['custom_strategies'],
                         {'assignment': 'custom_assignment_example.py',
                          'routing_cost': tmp_name})
        self.assertGreater(out['jobs_completed'], 0)
