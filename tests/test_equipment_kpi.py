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
        # 굶주림 [120, 160), 닫아준 lot 은 130 에 이송 발주 → 160 에 배달.
        # 이송 창과의 겹침 30s 가 transport-blocked, 나머지 10s 는 upstream.
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
        # 배치 dispatch 는 가장 늦게 준비된 lot 이 시점을 결정 — 그 lot 의
        # 이송 창(150~160)만 귀속되고, 먼저 온 lot(125 배달)은 무시된다.
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
        # 이송이 없었던 lot(창 미설정) — 전부 upstream, 하위호환(2-tuple)도 동일.
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
        # 창 200s: busy 50 + setup 10 + starvation 40 (transport 30) + 기타 idle 100.
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
        # 구간 기록이 없는 레거시 실행 + measurement_start > 0 → 미계산.
        self.machine.utilized_time = 50.0
        self.instance.current_time = 200.0

        result = collect_equipment_kpis(self.instance)

        self.assertIsNone(result["utilization_pct"])
        self.assertIsNone(result["idle_other_s"])

    def test_utilization_budget_from_intervals_with_warmup_window(self):
        # busy/setup 구간이 있으면 warm-up 측정창(100~200)에서도 clipping 계산.
        # busy [80,140) → 창 내 40, setup [140,150) → 10, 나머지 idle 50.
        self.machine.busy_intervals = [(80.0, 140.0)]
        self.machine.setup_intervals = [(140.0, 150.0)]
        self.machine.utilized_time = 999.0  # 스칼라는 무시되어야 함
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
