from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


from ufast.cosim.results import auto_result_path, save_results  # noqa: E402


class ResultPathTests(unittest.TestCase):
    def test_auto_path_groups_artifacts_by_run_id(self):
        meta = {
            "mode": "production",
            "run_id": "2026-06-22_15-30-00_123456",
            "dataset_short": "HVLM",
            "days": 60,
            "num_oht": 100,
            "alpha": 0.05,
            "seed": 0,
            "dispatcher": "fifo",
            "amhs_strategy": "fifo",
            "congestion_model": "section_local",
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = auto_result_path(tmp, meta)
            expected_dir = os.path.join(tmp, meta["run_id"])

        self.assertEqual(os.path.dirname(path), expected_dir)
        self.assertTrue(path.endswith("HVLM_60d_100oht_a0.05_fifo_fifo_sl_s0.json"))

    def test_save_results_creates_run_directory(self):
        meta = {
            "mode": "production",
            "run_id": "2026-06-22_15-30-00_654321",
            "dataset_short": "HVLM",
            "days": 1,
            "num_oht": 5,
            "alpha": 0.05,
            "seed": 0,
        }
        result = {"meta": meta, "production": {}, "equipment": {}, "amhs": {}}

        with tempfile.TemporaryDirectory() as tmp:
            path = auto_result_path(tmp, meta)
            save_results(result, path)
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as stream:
                self.assertEqual(json.load(stream)["meta"]["run_id"], meta["run_id"])


if __name__ == "__main__":
    unittest.main()
