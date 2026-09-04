"""
ProductionRunner
================
Adapter that drives PySCFabSim's greedy simulation loop inside the integrated GUI.

The AMHS simulator is a real-time animation (events advanced by main_clock),
whereas the production simulator typically computes months to years of sim-time
quickly. The "real-time 1x" notion therefore cannot be applied directly.

Integration strategy
--------------------
1. Run the greedy loop to completion in a background thread *as fast as possible*.
2. While computing, record a Snapshot into the TimelineRecorder at each sim-time interval.
3. When the computation finishes, the GUI replays (scrubs) the recorded timeline
   with the progress bar.
   - The speed slider then acts as the "replay speed multiplier".

Thanks to this approach, the AMHS and production modes share the same timeline replay UI.

PySCFabSim's input is a *dataset folder* (e.g. datasets/HVLM), loaded with
read_all() exactly as in the original simulator configuration.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from ufast.integration.timeline import Snapshot, TimelineRecorder


# The greedy decision functions are reused from the same production engine as the CLI co-sim.
# (dependency on the simulation/ copy removed — single engine)
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
    """Detailed per-lot statistics (in days).

    Ported from get_lot_statistics in simulation/stats.py — the function does not
    exist in production/stats.py, and under the no-engine-change policy it lives in
    this GUI adapter module instead of modifying production/.
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
    """Parameter object expected by PySCFabSim's run_greedy_core.

    Has the same fields as the simulation_parameters class in the original main.py.
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
    """Computes the production simulation in the background while filling the timeline."""

    progress = pyqtSignal(float, int, int)   # (current_time_days, done_lots, active_lots)
    status_message = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)
    results_ready = pyqtSignal(dict)         # delivers the result summary as soon as computation finishes

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
        # Snapshot interval is given in days; convert to seconds
        self.snapshot_interval_sec = max(1.0, snapshot_interval_days * 86400.0)
        self._active = False
        self.lot_stats = None
        self.compute_duration = None

    def request_stop(self):
        self._active = False

    # ── Snapshot builder ─────────────────────────────────────
    def _build_snapshot(self, instance) -> Snapshot:
        done = len(instance.done_lots)
        active = len(instance.active_lots)
        dispatchable = len(instance.dispatchable_lots)
        total = done + active + dispatchable

        # Machine status summary: busy/idle counts
        busy = 0
        idle = 0
        for m in instance.machines:
            # Treated as idle if waiting_lots is empty and no setup change (summary metric)
            if getattr(m, "waiting_lots", None):
                busy += 1
            else:
                idle += 1

        cur_days = instance.current_time_days
        throughput_per_day = (done / cur_days) if cur_days > 0 else 0.0

        # ── Accumulate WIP time-series samples (for avg/peak WIP in the result KPIs) ──
        if not hasattr(instance, "wip_samples"):
            instance.wip_samples = []
        instance.wip_samples.append((float(instance.current_time), active))

        # ── Instantaneous run-down time (breakdown + PM) aggregate ──
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
                # Additional KPIs
                "wip": active,
                "total_downtime_s": round(total_down, 1),
                "downtime_pct": down_pct,
            },
        )

    # ── Thread entry point ───────────────────────────────────
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
        # If dataset is an absolute path (or an existing path outside datasets_root) use it
        # as is; otherwise resolve it as a subfolder of datasets_root.
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
        # production.FileInstance does not take a congestion factor parameter
        # (the transport-time inflation model was a feature only of the simulation/ copy —
        #  actual congestion modelling is handled by the CLI co-sim (ufast))
        instance = FileInstance(files, run_to, l4m, plugins)
        dispatcher = dispatcher_map[p.dispatcher]

        self.recorder.reset(mode="production",
                            snapshot_interval=self.snapshot_interval_sec)
        # Initial snapshot
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

            # Snapshot (at sim-time intervals)
            self.recorder.maybe_record(
                instance.current_time,
                lambda inst=instance: self._build_snapshot(inst),
            )

            # Progress report (once per day)
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
        # Final snapshot
        self.recorder.record(self._build_snapshot(instance))

        self.compute_duration = datetime.now() - start
        self.lot_stats = _get_lot_statistics(instance)

        # Compute the result summary (for GUI display) and emit immediately
        summary = self._build_result_summary(instance)
        summary["compute_duration"] = str(self.compute_duration)
        # For saving: detailed per-lot statistics (lot_id -> dict)
        summary["lot_stats_detail"] = self.lot_stats
        # Record run parameters (for reproducibility)
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

    # ── Result summary (shown directly in the PyQt6 window) ──
    def _build_result_summary(self, instance) -> dict:
        """Aggregate the same core metrics as the original PySCFabSim statistics per lot type."""
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

        # Machine utilisation summary (mean util + downtime per family)
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

        # ── WIP & run-down time summary ──
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
