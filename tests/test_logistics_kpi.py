from __future__ import annotations

import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


from ufast.common.logger import SimulationLogger  # noqa: E402
from ufast.cosim.results import _percentile, _tail_stats  # noqa: E402


class DeliveryTailStatsTests(unittest.TestCase):
    def test_percentile_linear_interpolation(self):
        vals = [10.0, 20.0, 30.0, 40.0]
        self.assertEqual(_percentile(vals, 0), 10.0)
        self.assertEqual(_percentile(vals, 100), 40.0)
        self.assertEqual(_percentile(vals, 50), 25.0)
        self.assertAlmostEqual(_percentile(vals, 95), 38.5)

    def test_percentile_edge_cases(self):
        self.assertEqual(_percentile([], 95), 0.0)
        self.assertEqual(_percentile([7.0], 95), 7.0)

    def test_tail_stats_keys_and_values(self):
        stats = _tail_stats([float(i) for i in range(1, 101)], "delivery")
        self.assertEqual(set(stats), {"delivery_p50_s", "delivery_p95_s",
                                      "delivery_p99_s", "delivery_max_s"})
        self.assertAlmostEqual(stats["delivery_p50_s"], 50.5)
        self.assertAlmostEqual(stats["delivery_p95_s"], 95.05)
        self.assertEqual(stats["delivery_max_s"], 100.0)

    def test_tail_stats_input_order_independent(self):
        a = _tail_stats([3.0, 1.0, 2.0], "transport")
        b = _tail_stats([1.0, 2.0, 3.0], "transport")
        self.assertEqual(a, b)


class LogisticsKpiTests(unittest.TestCase):
    def test_oht_utilization_includes_empty_travel_and_final_interval(self):
        logger = SimulationLogger()
        logger.reset(num_oht=1, sim_duration=60.0)
        logger.on_oht_init("OHT_1", 0.0)
        logger.on_oht_status_change("OHT_1", "ASSIGNED", 10.0)
        logger.on_oht_status_change("OHT_1", "LOADED", 20.0)
        logger.on_oht_status_change("OHT_1", "IDLE", 40.0)

        logger.finalize_oht_times(60.0)
        logger.finalize_oht_times(60.0)

        record = logger.oht_records["OHT_1"]
        self.assertEqual(record.idle_time, 30.0)
        self.assertEqual(record.assigned_time, 10.0)
        self.assertEqual(record.loaded_time, 20.0)
        self.assertAlmostEqual(record.utilization, 20.0 / 60.0)


if __name__ == "__main__":
    unittest.main()
