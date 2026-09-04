"""
ProductionRunner
================
PySCFabSim 의 greedy 시뮬레이션 루프를 통합 GUI 안에서 구동하는 어댑터.

물류 시뮬레이터는 실시간 애니메이션(이벤트를 main_clock 으로 진행)이지만,
생산 시뮬레이터는 보통 수개월~수년 단위의 sim-time 을 빠르게 계산한다.
따라서 "실시간 1x" 개념을 그대로 적용할 수 없다.

통합 전략
---------
1. greedy 루프를 백그라운드 스레드에서 *최대한 빠르게* 끝까지 계산한다.
2. 계산 중 sim-time 간격마다 Snapshot 을 TimelineRecorder 에 기록한다.
3. 계산이 끝나면 GUI 는 기록된 타임라인을 진행바로 재생(스크럽)한다.
   - 이때 speed 슬라이더는 "재생 배속"으로 동작한다.

이 방식 덕분에 물류/생산 두 모드가 동일한 타임라인 재생 UI 를 공유한다.

PySCFabSim 의 입력은 *dataset 폴더*(예: datasets/HVLM) 이며, 이는
원본 시뮬레이터 설정 그대로 read_all() 로 로딩된다.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from ufast.integration.timeline import Snapshot, TimelineRecorder


# greedy 의사결정 함수들은 CLI co-sim 과 같은 production 엔진에서 재사용한다.
# (Phase 2: simulation/ 사본 의존 제거 — 엔진 단일화)
from ufast.production.greedy import (
    get_lots_to_dispatch_by_machine,
    get_lots_to_dispatch_by_lot,
)
from ufast.production.dispatching.dispatcher import dispatcher_map
from ufast.production.file_instance import FileInstance
from ufast.production.plugins.cost_plugin import CostPlugin
from ufast.production.randomizer import Randomizer
from ufast.production.read import read_all
from ufast.common.equipment_kpi import collect_equipment_kpis


def _get_lot_statistics(instance):
    """lot 단위 상세 통계 (일 단위).

    simulation/stats.py 의 get_lot_statistics 를 이식 — production/stats.py 에는
    없는 함수인데, 엔진 무변경 원칙에 따라 production/ 을 고치지 않고
    GUI 어댑터인 이 모듈에 둔다.
    """
    from collections import defaultdict
    lots = defaultdict(lambda: {
        'lot_type': '', 'CT': 0, 'on_time': 0, 'tardiness': 0,
        'waiting_time': 0, 'processing_time': 0, 'transport_time': 0,
        'waiting_time_batching': 0,
    })
    for lot in instance.done_lots:
        lot_id = f'{lot.name}_{lot.idx}'
        lots[lot_id]['lot_type'] = lot.name
        lots[lot_id]['CT'] = (lot.done_at - lot.release_at) / 3600 / 24
        lots[lot_id]['tardiness'] = max(0, lot.done_at - lot.deadline_at) / 3600 / 24
        lots[lot_id]['waiting_time'] = lot.waiting_time / 3600 / 24
        lots[lot_id]['waiting_time_batching'] = lot.waiting_time_batching / 3600 / 24
        lots[lot_id]['processing_time'] = lot.processing_time / 3600 / 24
        lots[lot_id]['transport_time'] = lot.transport_time / 3600 / 24
        lots[lot_id]['on_time'] = 1 if lot.done_at <= lot.deadline_at else 0
    return lots


class ProductionParams:
    """PySCFabSim run_greedy_core 가 기대하는 파라미터 객체.

    원본 main.py 의 simulation_parameters 클래스와 동일한 필드를 갖는다.
    """

    def __init__(
        self,
        dataset: str = "HVLM",
        days: int = 2,
        dispatcher: str = "fifo",
        congestion_factor: Optional[str] = None,
        max_transport_load: int = 300,
        max_congestion_factor: Optional[int] = None,
        alg: str = "l4m",
    ):
        self.dataset = dataset
        self.days = days
        self.dispatcher = dispatcher
        self.congestion_factor = congestion_factor
        self.max_transport_load = max_transport_load
        self.max_congestion_factor = max_congestion_factor
        self.alg = alg
        self.wandb = None
        self.chart = None


class ProductionRunner(QThread):
    """생산 시뮬레이션을 백그라운드에서 계산하며 타임라인을 채운다."""

    progress = pyqtSignal(float, int, int)   # (current_time_days, done_lots, active_lots)
    status_message = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)
    results_ready = pyqtSignal(dict)         # 계산 완료 즉시 결과 요약 전달

    def __init__(
        self,
        params: ProductionParams,
        recorder: TimelineRecorder,
        seed: int = 0,
        datasets_root: str = "datasets",
        snapshot_interval_days: float = 0.5,
    ):
        super().__init__()
        self.params = params
        self.recorder = recorder
        self.seed = seed
        self.datasets_root = datasets_root
        # 스냅샷 간격을 일(day) 단위로 받아 초로 환산
        self.snapshot_interval_sec = max(1.0, snapshot_interval_days * 86400.0)
        self._active = False
        self.lot_stats = None
        self.compute_duration = None

    def request_stop(self):
        self._active = False

    # ── 스냅샷 빌더 ──────────────────────────────────────────
    def _build_snapshot(self, instance) -> Snapshot:
        done = len(instance.done_lots)
        active = len(instance.active_lots)
        dispatchable = len(instance.dispatchable_lots)
        total = done + active + dispatchable

        # 머신 상태 요약: 가동/유휴 카운트
        busy = 0
        idle = 0
        for m in instance.machines:
            # waiting_lots 가 비어있고 setup 변화가 없으면 유휴로 간주(요약 지표)
            if getattr(m, "waiting_lots", None):
                busy += 1
            else:
                idle += 1

        cur_days = instance.current_time_days
        throughput_per_day = (done / cur_days) if cur_days > 0 else 0.0

        # ── WIP 시계열 샘플 누적 (결과 KPI 의 avg/peak WIP 계산용) ──
        if not hasattr(instance, "wip_samples"):
            instance.wip_samples = []
        instance.wip_samples.append((float(instance.current_time), active))

        # ── Run-down time (고장+PM) 순간 집계 ──
        total_down = 0.0
        for m in instance.machines:
            total_down += float(getattr(m, "bred_time", 0.0) or 0.0)
            total_down += float(getattr(m, "pmed_time", 0.0) or 0.0)
        sim_t = instance.current_time or 0.0
        mcount = len(instance.machines) or 1
        down_pct = round(100 * total_down / (sim_t * mcount), 2) if sim_t > 0 else 0.0

        return Snapshot(
            sim_time=float(instance.current_time),
            mode="production",
            metrics={
                "time_days": round(cur_days, 3),
                "done_lots": done,
                "active_lots": active,
                "dispatchable_lots": dispatchable,
                "total_lots": total,
                "machines_total": len(instance.machines),
                "machines_busy": busy,
                "machines_idle": idle,
                "throughput_per_day": round(throughput_per_day, 2),
                # 추가 KPI
                "wip": active,
                "total_downtime_s": round(total_down, 1),
                "downtime_pct": down_pct,
            },
        )

    # ── 스레드 진입점 ────────────────────────────────────────
    def run(self):
        self._active = True
        try:
            self._run_impl()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.failed.emit(str(e))
            return
        if self._active:
            self.finished_ok.emit()

    def _run_impl(self):
        p = self.params
        # dataset 가 절대경로(또는 기존 datasets_root 외부 경로)이면 그대로 사용,
        # 아니면 datasets_root 하위 폴더로 해석한다.
        if os.path.isabs(p.dataset) or os.path.isdir(p.dataset):
            dataset_path = p.dataset
        else:
            dataset_path = os.path.join(self.datasets_root, p.dataset)
        if not os.path.isdir(dataset_path):
            raise FileNotFoundError(
                f"Production dataset folder not found: {dataset_path}"
            )

        self.status_message.emit(f"Loading production dataset: {p.dataset}")
        files = read_all(dataset_path)

        run_to = 3600 * 24 * p.days
        Randomizer().random.seed(self.seed)
        l4m = (p.alg == "l4m")

        plugins = [CostPlugin()]
        # production.FileInstance 는 congestion factor 파라미터를 받지 않는다
        # (이송시간 팽창 모델은 simulation/ 사본 전용 기능이었음 —
        #  실제 혼잡 모델링은 CLI co-sim(ufast)이 담당)
        instance = FileInstance(files, run_to, l4m, plugins)
        dispatcher = dispatcher_map[p.dispatcher]

        self.recorder.reset(mode="production",
                            snapshot_interval=self.snapshot_interval_sec)
        # 시작 스냅샷
        self.recorder.record(self._build_snapshot(instance))

        start = datetime.now()
        last_emit_day = -1

        while self._active and not instance.done:
            done = instance.next_decision_point()
            if done or instance.current_time > run_to:
                break

            if l4m:
                machine, lots = get_lots_to_dispatch_by_machine(instance, dispatcher)
                if lots is None:
                    instance.usable_machines.remove(machine)
                else:
                    instance.dispatch(machine, lots)
            else:
                machine, lots = get_lots_to_dispatch_by_lot(
                    instance, instance.current_time, dispatcher
                )
                if lots is None:
                    instance.usable_lots.clear()
                    instance.lot_in_usable.clear()
                    instance.next_step()
                else:
                    instance.dispatch(machine, lots)

            # 스냅샷 (sim-time 간격 기준)
            self.recorder.maybe_record(
                instance.current_time,
                lambda inst=instance: self._build_snapshot(inst),
            )

            # 진행 상황 보고 (하루 단위)
            cur_day = int(instance.current_time_days)
            if cur_day != last_emit_day:
                last_emit_day = cur_day
                self.progress.emit(
                    instance.current_time_days,
                    len(instance.done_lots),
                    len(instance.active_lots),
                )
                self.status_message.emit(
                    f"Computing production simulation... Day {cur_day}/{p.days} "
                    f"| done lots {len(instance.done_lots)}"
                )

        if not self._active:
            self.status_message.emit("Production simulation stopped")
            return

        instance.finalize()
        # 종료 스냅샷
        self.recorder.record(self._build_snapshot(instance))

        self.compute_duration = datetime.now() - start
        self.lot_stats = _get_lot_statistics(instance)

        # 결과 요약(GUI 표시용) 계산 후 즉시 emit
        summary = self._build_result_summary(instance)
        summary["compute_duration"] = str(self.compute_duration)
        # 저장용: lot 단위 상세 통계 (lot_id -> dict)
        summary["lot_stats_detail"] = self.lot_stats
        # 실행 파라미터 기록 (재현용)
        summary["params"] = {
            "dataset": self.params.dataset,
            "days": self.params.days,
            "dispatcher": self.params.dispatcher,
            "algorithm": self.params.alg,
            "congestion_factor": self.params.congestion_factor,
            "seed": self.seed,
        }
        self.results_ready.emit(summary)

        self.status_message.emit(
            f"Production simulation complete — {p.days} days, "
            f"done lots {len(instance.done_lots)}, "
            f"compute time {self.compute_duration}"
        )

    # ── 결과 요약 (PyQt6 창에 바로 표시) ─────────────────────
    def _build_result_summary(self, instance) -> dict:
        """원본 PySCFabSim 통계와 동일한 핵심 지표를 lot-type 별로 집계."""
        import statistics
        from collections import defaultdict

        agg = defaultdict(lambda: {
            "ACT": [], "throughput": 0, "on_time": 0, "tardiness": 0.0,
        })
        for lot in instance.done_lots:
            a = agg[lot.name]
            a["ACT"].append((lot.done_at - lot.release_at) / 3600 / 24)  # days
            a["throughput"] += 1
            a["tardiness"] += max(0, lot.done_at - lot.deadline_at) / 3600 / 24
            if lot.done_at <= lot.deadline_at:
                a["on_time"] += 1

        per_lot = []
        for name in sorted(agg.keys()):
            a = agg[name]
            th = a["throughput"]
            per_lot.append({
                "lot_type": name,
                "throughput": th,
                "avg_cycle_time_days": round(statistics.mean(a["ACT"]), 2) if a["ACT"] else 0.0,
                "on_time_pct": round(a["on_time"] / th * 100, 1) if th else 0.0,
                "avg_tardiness_days": round(a["tardiness"] / th, 2) if th else 0.0,
            })

        # 머신 가동률 요약 (family 별 평균 util + 다운타임)
        util_by_family = defaultdict(list)
        down_by_family = defaultdict(list)
        sim_t = instance.current_time or 1
        for m in instance.machines:
            util_by_family[m.family].append(m.utilized_time / sim_t)
            down_s = (float(getattr(m, "bred_time", 0.0) or 0.0)
                      + float(getattr(m, "pmed_time", 0.0) or 0.0))
            down_by_family[m.family].append(down_s)
        machine_rows = []
        equipment = collect_equipment_kpis(instance)
        for fam in sorted(util_by_family.keys()):
            vals = util_by_family[fam]
            family_kpi = equipment["by_family"].get(str(fam), {})
            machine_rows.append({
                "family": str(fam),
                "count": len(vals),
                "avg_util_pct": round(statistics.mean(vals) * 100, 1) if vals else 0.0,
                "avg_starvation_s": family_kpi.get("avg_starvation_s", 0.0),
                "p95_starvation_s": family_kpi.get("p95_starvation_s", 0.0),
                "breakdown_count": family_kpi.get("breakdown_count", 0),
                "breakdown_time_s": family_kpi.get("breakdown_time_s", 0.0),
                "pm_count": family_kpi.get("pm_count", 0),
                "pm_time_s": family_kpi.get("pm_time_s", 0.0),
            })

        # ── WIP & Run-down time 요약 ──
        wip_samples = getattr(instance, "wip_samples", None) or []
        wips = [w for _, w in wip_samples]
        wip_summary = {
            "current_wip": len(instance.active_lots),
            "avg_wip": round(statistics.mean(wips), 1) if wips else len(instance.active_lots),
            "peak_wip": max(wips) if wips else len(instance.active_lots),
        }
        total_down_s = sum(
            float(getattr(m, "bred_time", 0.0) or 0.0)
            + float(getattr(m, "pmed_time", 0.0) or 0.0)
            for m in instance.machines)
        mcount = len(instance.machines) or 1
        downtime_summary = {
            "total_down_s": round(total_down_s, 1),
            "avg_down_per_machine_s": round(total_down_s / mcount, 1),
            "down_pct": round(100 * total_down_s / (sim_t * mcount), 2) if sim_t else 0.0,
            "avail_pct": round(100 * (1 - total_down_s / (sim_t * mcount)), 2) if sim_t else 100.0,
        }

        return {
            "dataset": self.params.dataset,
            "days": self.params.days,
            "total_done": len(instance.done_lots),
            "current_time_days": round(instance.current_time_days, 2),
            "per_lot": per_lot,
            "machines": machine_rows,
            "equipment": equipment,
        }
