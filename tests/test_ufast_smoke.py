from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


class CosimSmokeTests(unittest.TestCase):
    def test_requirements_is_utf8_text(self):
        path = os.path.join(ROOT, "requirements.txt")
        with open(path, "rb") as f:
            raw = f.read()

        self.assertNotEqual(raw[:2], b"\xff\xfe")
        self.assertNotIn(b"\x00", raw)
        text = raw.decode("utf-8-sig")
        self.assertIn("numpy", text)
        self.assertIn("scipy", text)

    def test_zero_day_ufast_and_comparison_output(self):
        from ufast.cosim.run import run_ufast

        dataset_dir = os.path.join(ROOT, "dataset", "HVLM")
        rail_file = os.path.join(ROOT, "dataset", "SMAT2022.rail")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            instance, amhs, traj_path = run_ufast(
                dataset_dir,
                rail_file,
                days=0,
                num_oht=5,
                dispatcher="fifo",
                seed=0,
                congestion_alpha=0.05,
                viz=False,
                amhs_strategy="nearest",
                congestion_model="off",
            )

        output = out.getvalue()
        self.assertFalse(traj_path)
        self.assertGreaterEqual(len(instance.machines), 1)
        self.assertEqual(amhs.total_jobs, 0)
        self.assertIn("LogiFabSim", output)
        self.assertIn("U-FAST", output)
        self.assertIn("Table 1", output)


class StrategyHelperTests(unittest.TestCase):
    def test_assignment_strategy_normalizes_oht_object(self):
        from ufast.common.strategy_loader import (
            invoke_assignment_strategy,
            normalize_oht_selection,
        )

        class OHT:
            def __init__(self, name):
                self.name = name

        class Strategy:
            def select(self, target_section_id, idle_ohts):
                return idle_ohts[0]

        oht = OHT("OHT_1")
        selected = invoke_assignment_strategy(
            Strategy(), 10, [oht], None, None, None)
        self.assertEqual(
            normalize_oht_selection(selected, {oht.name: oht}),
            "OHT_1",
        )

    def test_node_routing_strategy_normalizes_node_path(self):
        from ufast.common.strategy_loader import (
            invoke_node_routing_strategy,
            normalize_node_path,
        )

        class Network:
            nodes = {"A": object(), "B": object(), "C": object()}

        class RouteManager:
            network = Network()

        def route(from_node, to_node):
            return {"node_path": [from_node, "B", to_node]}

        rm = RouteManager()
        result = invoke_node_routing_strategy(route, "A", "C", rm, None)
        self.assertEqual(normalize_node_path(result, rm), ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
