from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


from ufast.common.equipment_kpi import (  # noqa: E402
    close_starvation,
    collect_equipment_kpis,
    open_starvation,
    record_equipment_downtime,
)


class EquipmentKpiTests(unittest.TestCase):
    def setUp(self):
        self.machine = SimpleNamespace(
            family="ETCH",
            bred_time=0.0,
            pmed_time=0.0,
            breakdown_intervals=[],
            pm_intervals=[],
            starvation_intervals=[],
            starvation_open_since=None,
        )
        self.instance = SimpleNamespace(
            current_time=0.0,
            measurement_start_time=100.0,
            machines=[self.machine],
        )

    def test_starvation_is_clipped_to_measurement_window(self):
        open_starvation(self.machine, 90.0)
        close_starvation(self.machine, 130.0)
        self.instance.current_time = 200.0

        result = collect_equipment_kpis(self.instance)

        self.assertEqual(result["starvation_count"], 1)
        self.assertEqual(result["total_starvation_s"], 30.0)
        self.assertEqual(result["avg_starvation_s"], 30.0)

    def test_starvation_transport_attribution_uses_binding_lot_transit_window(self):
        # Starvation [120, 160); the lot that closed it was ordered for transport at 130 -> delivered at 160.
        # The 30s overlap with the transit window is transport-blocked, the remaining 10s is upstream.
        lot = SimpleNamespace(
            free_since=160.0, last_transit_start=130.0, last_transit_end=160.0)
        open_starvation(self.machine, 120.0)
        close_starvation(self.machine, 160.0, [lot])
        self.instance.current_time = 200.0

        result = collect_equipment_kpis(self.instance)

        self.assertEqual(result["total_starvation_s"], 40.0)
        self.assertEqual(result["transport_blocked_starvation_s"], 30.0)
        self.assertEqual(result["upstream_starvation_s"], 10.0)
        self.assertEqual(result["transport_blocked_starvation_pct"], 75.0)
        self.assertEqual(result["transport_blocked_starvation_count"], 1)

    def test_starvation_transport_attribution_picks_last_ready_lot_in_batch(self):
        # For batch dispatch the last-ready lot determines the time — only that lot's
        # transit window (150~160) is attributed; the earlier lot (delivered at 125) is ignored.
        early = SimpleNamespace(
            free_since=125.0, last_transit_start=110.0, last_transit_end=125.0)
        late = SimpleNamespace(
            free_since=160.0, last_transit_start=150.0, last_transit_end=160.0)
        open_starvation(self.machine, 120.0)
        close_starvation(self.machine, 160.0, [early, late])
        self.instance.current_time = 200.0

        result = collect_equipment_kpis(self.instance)

        self.assertEqual(result["transport_blocked_starvation_s"], 10.0)

    def test_starvation_without_transit_window_counts_as_upstream(self):
        # Lot without transport (no window set) — all upstream; the backward-compatible 2-tuple behaves the same.
        lot = SimpleNamespace(
            free_since=160.0, last_transit_start=None, last_transit_end=None)
        open_starvation(self.machine, 120.0)
        close_starvation(self.machine, 160.0, [lot])
        self.machine.starvation_intervals.append((160.0, 170.0))  # legacy 2-tuple
        self.instance.current_time = 200.0

        result = collect_equipment_kpis(self.instance)

        self.assertEqual(result["starvation_count"], 2)
        self.assertEqual(result["transport_blocked_starvation_s"], 0.0)
        self.assertEqual(result["upstream_starvation_s"], 50.0)

    def test_utilization_time_budget_decomposition(self):
        # 200s window: busy 50 + setup 10 + starvation 40 (transport 30) + other idle 100.
        self.instance.measurement_start_time = 0.0
        self.machine.utilized_time = 50.0
        self.machine.setuped_time = 10.0
        lot = SimpleNamespace(
            free_since=160.0, last_transit_start=130.0, last_transit_end=160.0)
        open_starvation(self.machine, 120.0)
        close_starvation(self.machine, 160.0, [lot])
        self.instance.current_time = 200.0

        result = collect_equipment_kpis(self.instance)

        self.assertEqual(result["busy_time_s"], 50.0)
        self.assertEqual(result["setup_time_s"], 10.0)
        self.assertEqual(result["utilization_pct"], 25.0)
        self.assertEqual(result["idle_other_s"], 100.0)

    def test_utilization_budget_is_none_with_warmup_window(self):
        # Legacy run without interval records + measurement_start > 0 -> not computed.
        self.machine.utilized_time = 50.0
        self.instance.current_time = 200.0

        result = collect_equipment_kpis(self.instance)

        self.assertIsNone(result["utilization_pct"])
        self.assertIsNone(result["idle_other_s"])

    def test_utilization_budget_from_intervals_with_warmup_window(self):
        # With busy/setup intervals, clipping is computed even in the warm-up measurement window (100~200).
        # busy [80,140) -> 40 inside the window, setup [140,150) -> 10, remaining idle 50.
        self.machine.busy_intervals = [(80.0, 140.0)]
        self.machine.setup_intervals = [(140.0, 150.0)]
        self.machine.utilized_time = 999.0  # the scalar must be ignored
        self.instance.current_time = 200.0

        result = collect_equipment_kpis(self.instance)

        self.assertEqual(result["busy_time_s"], 40.0)
        self.assertEqual(result["setup_time_s"], 10.0)
        self.assertEqual(result["utilization_pct"], 40.0)
        self.assertEqual(result["idle_other_s"], 50.0)

    def test_downtime_counts_and_uses_union_for_total(self):
        self.instance.current_time = 110.0
        record_equipment_downtime(self.instance, self.machine, "breakdown", 30.0)
        self.instance.current_time = 120.0
        record_equipment_downtime(self.instance, self.machine, "pm", 30.0)
        self.instance.current_time = 200.0

        result = collect_equipment_kpis(self.instance)

        self.assertEqual(result["breakdown_count"], 1)
        self.assertEqual(result["breakdown_time_s"], 30.0)
        self.assertEqual(result["pm_count"], 1)
        self.assertEqual(result["pm_time_s"], 30.0)
        self.assertEqual(result["total_downtime_s"], 40.0)

    def test_open_starvation_is_reported_as_censored(self):
        open_starvation(self.machine, 150.0)
        self.instance.current_time = 200.0

        result = collect_equipment_kpis(self.instance)

        self.assertEqual(result["starvation_count"], 0)
        self.assertEqual(result["censored_starvation_count"], 1)
        self.assertEqual(result["censored_starvation_s"], 50.0)

    def test_measurement_end_is_capped_at_configured_run_length(self):
        self.instance.run_to = 180.0
        open_starvation(self.machine, 150.0)
        self.instance.current_time = 220.0

        result = collect_equipment_kpis(self.instance)

        self.assertEqual(result["measurement_end_s"], 180.0)
        self.assertEqual(result["censored_starvation_s"], 30.0)


if __name__ == "__main__":
    unittest.main()
