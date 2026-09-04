import os
import sys

# Support running the file directly (python src/ufast/main_ui.py) — add src to sys.path
_SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
import heapq
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QTimer, QThread, pyqtSignal

from ufast.paths import DATASET_DIR
from ufast.core.data_set import SimulatorDataSet, Event
from ufast.control.controllers import VehicleController, EventHandler
from ufast.layout.rail_manager import RailManager
from ufast.gui.viewer import SimulationViewer
from ufast.common.dxf_parser import DXFParser
from ufast.common.rail_io import save_rail_file, load_rail_file
from ufast.common.fromto_parser import load_fromto
from ufast.common.logger import get_logger
from ufast.common.strategy_loader import StrategyLoadError, describe_strategy, load_strategy

# Integrated: timeline + production runner
from ufast.integration.timeline import TimelineRecorder, Snapshot
try:
    from ufast.integration.production_runner import ProductionRunner, ProductionParams
    _PROD_RUNNER_AVAILABLE = True
except Exception as _pe:  # noqa: BLE001
    _PROD_RUNNER_AVAILABLE = False
    print(f"[main_ui] ⚠️ ProductionRunner import failed: {_pe}")

try:
    from ufast.route import RouteManager, SectionNodeBridge, Dispatcher, NearestIdleStrategy
    from ufast.route.logistics_logger import get_logistics_logger
    _ROUTE_PKG_AVAILABLE = True
except ImportError:
    _ROUTE_PKG_AVAILABLE = False
    get_logistics_logger = None
    print("[main_ui] ⚠️  route package import failed → falling back to legacy Dijkstra only")


# ──────────────────────────────────────────────────────────────
#  Simulation worker thread
#  Runs separately from the main (GUI) thread so that heavy computations such as
#  Dijkstra do not block screen rendering.
# ──────────────────────────────────────────────────────────────
class SimulationThread(QThread):
    """
    Dedicated thread for the simulation loop.

    - Never calls Qt GUI objects directly.
    - When a GUI update is needed, emits a signal and delegates to the main thread.

    ── Speed control (fixed) ──────────────────────────────────
    Previous bug: a fixed time_step (0.05s) was added every step at an 8ms interval,
    so even at 1x the sim-time advanced about 6.25 times faster than real time.

    Fix: advance the sim-clock by "real elapsed time since the previous step ×
    sim_speed".
        Δsim = (now - prev_real) × sim_speed
    Therefore
        sim_speed = 1.0  →  sim-time runs 1:1 with real time (real-time)
        sim_speed = 20.0 →  20 s of sim-time per real second (exactly 20x)
    Even if a frame takes longer (compute delay), Δreal grows accordingly and the ratio holds.
    """

    # Signals for events to be handled on the main thread
    status_message = pyqtSignal(str)   # status-bar text
    sim_finished   = pyqtSignal()      # simulation complete (triggers log saving)

    STEP_INTERVAL = 0.008   # target render step interval (s). Balances smoothness vs. CPU usage

    def __init__(self, app: "SimulationApp"):
        super().__init__()
        self._app = app
        self._active = False
        self._prev_real = None     # wall-clock time of the previous step

    # ── External control ──────────────────────────────────────
    def request_stop(self):
        """Safely stop the loop from the main thread."""
        self._active = False

    # ── Thread entry point ────────────────────────────────────
    def run(self):
        import time
        self._active = True
        self._prev_real = time.perf_counter()
        self._last_status_wall = 0.0
        finished = False

        while self._active:
            t0 = time.perf_counter()
            try:
                finished = self._step(t0)
            except Exception:
                import traceback
                traceback.print_exc()
                self._active = False
                break

            if finished:
                self._active = False
                break

            # Sleep until the target step interval → avoids hogging the CPU.
            # In replay mode, run at full speed without sleeping.
            if self._app.replay_recorder is None:
                elapsed = time.perf_counter() - t0
                rest = self.STEP_INTERVAL - elapsed
                if rest > 0.0001:
                    time.sleep(rest)

        # Replay mode: finalize the recording at completion/stop (Parquet + Rerun viewer).
        # It does not touch GUI objects, so it is safe to run on the worker thread.
        rec = self._app.replay_recorder
        if rec is not None and rec.frame_count > 0:
            self.status_message.emit("Writing replay outputs (Parquet + Rerun viewer)...")
            try:
                rec.finalize()
            except Exception:
                import traceback
                traceback.print_exc()

        if finished:
            self.sim_finished.emit()

    # ── Single simulation step (no GUI object access) ─────────
    def _step(self, now: float) -> bool:
        """
        Advance the simulation by one step.
        now: wall-clock time of this step (perf_counter).
        Returns True → simulation termination condition met.
        """
        app = self._app
        ds  = app.ds

        replay = app.replay_recorder is not None

        # Advance the sim-clock by real elapsed time × speed factor (real-time basis).
        real_dt = now - self._prev_real
        self._prev_real = now
        # Guard against abnormally large jumps (e.g. debugger pauses): clamp to 0.25s
        if real_dt > 0.25:
            real_dt = 0.25
        prev_clock = ds.main_clock
        if replay:
            # Replay mode: fixed-step full-speed progression, independent of the wall-clock.
            # (Large speed-based jumps change how often periodic logic such as idle
            #  repositioning runs and thus alter results, so the step size is fixed for determinism.)
            ds.main_clock += 0.1
        else:
            ds.main_clock += real_dt * app.sim_speed

        if app.route_manager:
            app.route_manager.sim_time = ds.main_clock

        # ── Termination check ─────────────────────────────────
        if ds.main_clock >= app.sim_duration:
            ds.main_clock = app.sim_duration
            processed = ds.num_of_processed_lot
            total     = ds.lot_count
            self.status_message.emit(
                f"Simulation complete — {app.sim_duration:.0f}s | "
                f"Lots: {processed}/{total} | saving logs..."
            )
            return True

        # ── Event processing ──────────────────────────────────
        while ds.event_queue:
            evt = ds.event_queue[0]
            if evt.time_scheduled <= ds.main_clock:
                heapq.heappop(ds.event_queue)
                if app.event_handler:
                    app.event_handler.process_event(evt)
            else:
                break

        # ── IDLE OHT repositioning ────────────────────────────
        if (
            app.event_handler is not None
            and app.route_manager is not None
            and app.bridge is not None
            and ds.main_clock - app._last_idle_repo_scan >= 0.5
        ):
            app.event_handler.reposition_idle_ohts(ds.main_clock)
            app._last_idle_repo_scan = ds.main_clock

        # ── Deadlock check (every 30s) ────────────────────────
        if app.route_manager and ds.main_clock - app._last_deadlock_check >= 30.0:
            app._last_deadlock_check = ds.main_clock
            deadlocks = app.route_manager.detect_deadlock()
            if deadlocks:
                print(f"[DEADLOCK] ⚠️  t={ds.main_clock:.1f}s deadlock detected: {deadlocks}")

        # ── Status message / console log ──────────────────────
        processed  = ds.num_of_processed_lot
        total      = ds.lot_count
        oht_count  = len(ds.oht_list)

        if replay:
            # In replay mode sim-time advances very quickly, so throttle by wall-clock
            need_status = (now - self._last_status_wall) >= 0.2
            log_bucket  = 3600
        else:
            need_status = ds.main_clock - app._last_status_update >= 0.2
            log_bucket  = 10
        prev_10     = int(prev_clock / log_bucket)
        curr_10     = int(ds.main_clock / log_bucket)
        need_log    = (curr_10 != prev_10) and (oht_count > 0)

        if need_status or need_log:
            # dict iteration is safe thanks to the GIL (oht_list does not change during the simulation)
            idle_cnt     = sum(1 for o in ds.oht_list.values() if o.status == "IDLE")
            assigned_cnt = sum(1 for o in ds.oht_list.values() if o.status == "ASSIGNED")
            loaded_cnt   = sum(1 for o in ds.oht_list.values() if o.status == "LOADED")
            repo_cnt     = sum(1 for o in ds.oht_list.values() if o.status == "REPOSITIONING")

            if need_status:
                self.status_message.emit(
                    f"Time: {ds.main_clock:.1f}s / {app.sim_duration:.0f}s | "
                    f"Lots: {processed}/{total} | "
                    f"IDLE:{idle_cnt} ASGN:{assigned_cnt} "
                    f"LOAD:{loaded_cnt} REPO:{repo_cnt}"
                )
                app._last_status_update = ds.main_clock
                self._last_status_wall = now

            if need_log:
                print(
                    f"[OHT t={ds.main_clock:.0f}s] "
                    f"IDLE={idle_cnt} ASSIGNED={assigned_cnt} "
                    f"LOADED={loaded_cnt} REPO={repo_cnt} "
                    f"/ total={oht_count} | Lots {processed}/{total}"
                )

        # ── Recording: ReplayRecorder in replay mode, timeline snapshots in real-time mode ──
        if replay:
            app.replay_recorder.on_step(ds.main_clock)
        elif app.timeline_recorder is not None:
            app.timeline_recorder.maybe_record(
                ds.main_clock,
                lambda: app._build_logistics_snapshot(),
            )

        return False


# ──────────────────────────────────────────────────────────────
class StrategyConfigDialog(QWidget):
    """Dialog for choosing external strategy files (enable + path) before the simulation starts."""

    _LABELS = {
        "routing":          "Routing strategy",
        "assignment":       "Assignment strategy",
        "idle_positioning": "Idle positioning strategy",
        "routing_cost":     "Routing cost function",
    }

    def __init__(self, parent=None, initial_paths=None, initial_enabled=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Strategy Settings")
        self._checks = {}
        self._edits  = {}
        initial_paths   = initial_paths   or {}
        initial_enabled = initial_enabled or {}

        layout = QVBoxLayout(self)
        form   = QFormLayout()

        for kind, label in self._LABELS.items():
            check  = QCheckBox("Enable")
            edit   = QLineEdit(initial_paths.get(kind, ""))
            edit.setPlaceholderText("Select .py / .pkl / .pickle file")
            browse = QPushButton("Browse")

            enabled = bool(initial_enabled.get(kind, False))
            check.setChecked(enabled)
            edit.setEnabled(enabled)
            browse.setEnabled(enabled)

            def on_toggle(state, e=edit, b=browse):
                active = bool(state)
                e.setEnabled(active)
                b.setEnabled(active)

            def on_browse(_=False, e=edit, title=label):
                filename, _ = QFileDialog.getOpenFileName(
                    self,
                    f"Select {title} file",
                    e.text() or "",
                    "Strategy Files (*.py *.pkl *.pickle);;"
                    "Python Files (*.py);;"
                    "Pickle Files (*.pkl *.pickle);;"
                    "All Files (*)",
                )
                if filename:
                    e.setText(filename)

            check.toggled.connect(on_toggle)
            browse.clicked.connect(on_browse)

            row = QHBoxLayout()
            row.addWidget(check)
            row.addWidget(edit, 1)
            row.addWidget(browse)

            form.addRow(label, row)
            self._checks[kind] = check
            self._edits[kind]  = edit

        layout.addLayout(form)

    def values(self):
        result = {}
        for kind in self._LABELS:
            checked = self._checks[kind].isChecked()
            path    = self._edits[kind].text().strip()
            result[kind] = path if checked else ""
        return result


# ──────────────────────────────────────────────────────────────
class SimulationApp:
    def __init__(self):
        self.app = QApplication(sys.argv)

        # 1. Initialize the dataset and managers
        self.ds           = SimulatorDataSet.get_instance()
        self.rail_manager = RailManager()

        self.vehicle_controller: VehicleController | None = None
        self.event_handler:      EventHandler      | None = None
        self.fromto_data = []
        self.fromto_filename = None      # Integrated: file name of the currently loaded From-To chart
        self.route_manager: RouteManager      | None = None
        self.bridge:        SectionNodeBridge | None = None
        self.custom_strategy_paths = {
            "routing": "", "assignment": "",
            "idle_positioning": "", "routing_cost": "",
        }
        self.kpi_save_mode          = "both"
        self.simulator_mode         = "production_logistics"
        self.default_processing_time = 60.0

        # 2. Initialize the viewer
        self.viewer = SimulationViewer()
        self.viewer.simulation_start_signal.connect(self.start_simulation)
        self.viewer.simulation_stop_signal.connect(self.stop_simulation)
        self.viewer.layout_file_loaded_signal.connect(self.load_layout_file)
        self.viewer.rail_file_loaded_signal.connect(self.load_rail_file)
        self.viewer.rail_file_save_signal.connect(self.save_rail_file)
        self.viewer.fromto_file_loaded_signal.connect(self.load_fromto_file)
        self.viewer.clear_signal.connect(self.clear_all)
        self.viewer.speed_changed_signal.connect(self.on_speed_changed)
        self.viewer.save_log_signal.connect(lambda: self._save_logs())
        self.viewer.layer_panel.layer_changed.connect(self.on_layer_changed)
        self.viewer.layer_panel.convert_to_rail.connect(self.on_convert_to_rail)
        # Integrated: mode switching / timeline playback signals
        self.viewer.mode_changed_signal.connect(self.on_mode_changed)
        self.viewer.timeline_scrubbed_signal.connect(self.on_timeline_scrubbed)
        self.viewer.playback_toggled_signal.connect(self.on_playback_toggled)
        self.viewer.go_live_signal.connect(self.on_go_live)
        # Panel-resident Run Options
        self.viewer.configure_strategies_signal.connect(self.open_strategy_dialog)
        self.viewer.production_add_dataset_signal.connect(self.on_production_add_dataset)
        self.viewer.draw_layout()
        self.viewer.show()

        # 3. Simulation settings
        self.sim_speed    = 1.0
        self.sim_duration = 3600.0
        self.max_sim_duration = 31_536_000.0

        # Simulation thread (None = stopped)
        self.sim_thread: SimulationThread | None = None

        # Timestamps for tracking periodic tasks
        self._last_idle_repo_scan  = -9999.0
        self._last_deadlock_check  = -9999.0
        self._last_status_update   = -9999.0

        # ── Integrated: mode / timeline / production runner ──────
        self.app_mode = "logistics"             # "logistics" | "production"
        # Replay-mode recorder — None means real-time (live) mode
        self.replay_recorder = None
        # Logistics: snapshots every 1 s of sim-time / production: the runner sets its own interval
        self.timeline_recorder = TimelineRecorder(snapshot_interval=1.0)
        self.production_runner: "ProductionRunner | None" = None
        self.production_params = ProductionParams() if _PROD_RUNNER_AVAILABLE else None

        # Playback state
        self._playback_active = False
        self._playback_clock = 0.0              # virtual sim-time being played back
        self._reviewing = False                 # whether the user is browsing the past (review)
        self._playback_timer = QTimer()
        self._playback_timer.timeout.connect(self._on_playback_tick)
        self._playback_timer.setInterval(33)    # ~30fps
        self._playback_last_real = None

        # Timer that keeps the timeline progress bar following the LIVE simulation
        self._live_follow_timer = QTimer()
        self._live_follow_timer.timeout.connect(self._on_live_follow_tick)
        self._live_follow_timer.setInterval(100)  # refresh the progress bar every 0.1s

        # Integrated: initialize the run-info text at the right of the status bar (default logistics mode)
        self.viewer.set_run_info("From-To: (not loaded)")

    # ── Simulation loop (called from the thread) ──────────────
    def _is_sim_running(self) -> bool:
        return self.sim_thread is not None and self.sim_thread.isRunning()

    # ── Integrated: logistics snapshot builder (called from SimulationThread) ─
    def _build_logistics_snapshot(self) -> "Snapshot":
        ds = self.ds
        ohts = {}
        for name, oht in ds.oht_list.items():
            ohts[name] = {
                "section_id": oht.current_section_id,
                "enter_time": oht.time_enter_current_section,
                "status": oht.status,
            }
        eqs = {}
        for eq_name, eq in ds.eq_list.items():
            eqs[eq_name] = {
                "status": getattr(eq, "eq_status", "IDLE"),
                "lot_count": getattr(eq, "waiting_lot_count", 0),
            }
        idle_cnt = sum(1 for o in ds.oht_list.values() if o.status == "IDLE")
        assigned = sum(1 for o in ds.oht_list.values() if o.status == "ASSIGNED")
        loaded   = sum(1 for o in ds.oht_list.values() if o.status == "LOADED")
        repo     = sum(1 for o in ds.oht_list.values() if o.status == "REPOSITIONING")
        return Snapshot(
            sim_time=float(ds.main_clock),
            mode="logistics",
            ohts=ohts,
            eqs=eqs,
            metrics={
                "processed": ds.num_of_processed_lot,
                "total": ds.lot_count,
                "oht_count": len(ds.oht_list),
                "idle": idle_cnt, "assigned": assigned,
                "loaded": loaded, "repositioning": repo,
            },
        )

    # ── Integrated: mode switching ────────────────────────────
    def on_mode_changed(self, mode: str):
        # Stop if running
        self.stop_simulation()
        self._stop_playback()
        self.app_mode = mode
        self.viewer.set_mode(mode)
        if mode == "production":
            self.viewer.statusBar().showMessage(
                "Production mode: Run ▶ to compute with a PySCFabSim dataset."
            )
            # Status-bar right side: show the last selected dataset (if any)
            self._update_production_run_info()
        else:
            self.viewer.statusBar().showMessage(
                "Logistics mode: load DXF/Rail + FromTo, then run."
            )
            # Status-bar right side: show the currently loaded From-To chart
            if self.fromto_filename:
                self.viewer.set_run_info(
                    f"From-To: {self.fromto_filename} ({len(self.fromto_data)} records)"
                )
            else:
                self.viewer.set_run_info("From-To: (not loaded)")

    def _update_production_run_info(self):
        """Show the current production dataset info on the status bar (right) and dashboard."""
        import os
        if self.production_params and getattr(self.production_params, "dataset", None):
            ds = self.production_params.dataset
            name = os.path.basename(ds.rstrip("/\\")) if (os.path.isabs(ds) or os.path.sep in ds) else ds
            short = (
                f"Dataset: {name} | {self.production_params.days} days | "
                f"{self.production_params.dispatcher}/{self.production_params.alg}"
            )
            self.viewer.set_run_info(short)
            if self.viewer.production_dashboard:
                self.viewer.production_dashboard.set_dataset_info(
                    f"Dataset: {name}  ·  {self.production_params.days} days  ·  "
                    f"dispatcher={self.production_params.dispatcher}  ·  "
                    f"algorithm={self.production_params.alg}"
                    + (f"  ·  source={ds}" if name != ds else "")
                )
        else:
            self.viewer.set_run_info("Dataset: (not selected)")
            if self.viewer.production_dashboard:
                self.viewer.production_dashboard.set_dataset_info("Dataset: (not selected)")

    # ── Integrated: playback / past-browsing (review) control ──
    #
    # Two situations are distinguished.
    #  (1) Simulation running in real time (LIVE): the simulation thread keeps running.
    #      Grabbing the progress bar enters 'review' mode, which only displays the snapshot at
    #      that time; the simulation itself does not stop. '● Go LIVE' returns to the present.
    #  (2) Simulation stopped/finished: the progress bar and ▶Play replay the recording like a video.
    #
    def _sim_running(self) -> bool:
        thread_live = self.sim_thread is not None and self.sim_thread.isRunning()
        prod_live = self.production_runner is not None and self.production_runner.isRunning()
        return thread_live or prod_live

    def on_go_live(self):
        """End past browsing and return to real-time tracking."""
        self._reviewing = False
        self.viewer.set_reviewing(False)
        self.viewer.exit_playback_mode()   # resume update_animation
        if self.app_mode == "production":
            self.viewer.statusBar().showMessage("Returned to LIVE computation tracking")
        else:
            self.viewer.statusBar().showMessage("Returned to LIVE simulation")

    def on_playback_toggled(self, playing: bool):
        # ▶Play (past replay) is not used while the simulation is running in real time.
        if self._sim_running():
            self.viewer.play_button.setChecked(False)
            return
        if playing:
            self._start_playback()
        else:
            self._stop_playback()

    def _start_playback(self):
        import time
        if len(self.timeline_recorder) == 0:
            self.viewer.statusBar().showMessage("No recording to play. Run the simulation first.")
            self.viewer.play_button.setChecked(False)
            return
        if self._playback_clock >= self.timeline_recorder.duration:
            self._playback_clock = self.timeline_recorder.start_time
        self._playback_active = True
        self._playback_last_real = time.perf_counter()
        self._playback_timer.start()

    def _stop_playback(self):
        self._playback_active = False
        self._playback_timer.stop()
        if hasattr(self.viewer, "play_button") and self.viewer.play_button.isChecked():
            self.viewer.play_button.blockSignals(True)
            self.viewer.play_button.setChecked(False)
            self.viewer.play_button.setText("▶ Play")
            self.viewer.play_button.blockSignals(False)

    def _on_playback_tick(self):
        import time
        rec = self.timeline_recorder
        if len(rec) == 0:
            self._stop_playback()
            return
        now = time.perf_counter()
        dt = now - (self._playback_last_real or now)
        self._playback_last_real = now
        self._playback_clock += dt * self.sim_speed
        if self._playback_clock >= rec.duration:
            self._playback_clock = rec.duration
            self._render_snapshot_at(self._playback_clock)
            self._stop_playback()
            return
        self._render_snapshot_at(self._playback_clock)

    def on_timeline_scrubbed(self, frac: float):
        """Progress-bar drag → restore the view at the corresponding fraction of the timeline."""
        rec = self.timeline_recorder
        if len(rec) == 0:
            return
        t = rec.start_time + frac * (rec.duration - rec.start_time)
        self._playback_clock = t

        if self._sim_running():
            # Running in real time: leave the simulation alone and only 'review' the past.
            self._reviewing = True
            self.viewer.set_reviewing(True)
            self._render_snapshot_at(t)
            self.viewer.statusBar().showMessage(
                "Reviewing the past — the simulation keeps running. Press '● Go LIVE' to return to the present."
            )
        else:
            # Stopped/finished: ordinary playback scrubbing
            self._stop_playback()
            self._render_snapshot_at(t)

    def _render_snapshot_at(self, sim_time: float):
        """Render the snapshot at the given sim-time."""
        rec = self.timeline_recorder
        snap = rec.at(sim_time)
        if snap is None:
            return
        dur_span = (rec.duration - rec.start_time) or 1.0
        frac = (sim_time - rec.start_time) / dur_span
        self.viewer.set_timeline_position(
            frac, sim_time, label_days=(snap.mode == "production")
        )
        if snap.mode == "production":
            if self.viewer.production_dashboard is not None:
                self.viewer.production_dashboard.set_snapshot(snap)
        else:
            self.viewer.render_logistics_snapshot(snap)

    def _on_live_follow_tick(self):
        """While running live and not reviewing, align the progress bar with the current time."""
        if self._reviewing or not self._sim_running():
            return
        rec = self.timeline_recorder
        if len(rec) == 0:
            return
        if self.app_mode == "production":
            # Production: the computation finishes quickly, so the bar points at the end of the recording so far.
            now_t = rec.duration
            label_days = True
        else:
            now_t = self.ds.main_clock
            label_days = False
        span = (rec.duration - rec.start_time) or 1.0
        # Logistics shows the progress bar relative to the full sim_duration
        if self.app_mode != "production":
            total = self.sim_duration or rec.duration or 1.0
            frac = now_t / total
        else:
            frac = (now_t - rec.start_time) / span
        self.viewer.set_timeline_position(frac, now_t, label_days=label_days)

    # ── Integrated: run the production simulation ─────────────
    def start_production_simulation(self):
        if not _PROD_RUNNER_AVAILABLE:
            self.viewer.statusBar().showMessage("Production runner unavailable (import failed).")
            return
        if self.production_runner and self.production_runner.isRunning():
            return

        # Read directly from the panel-resident Run Options (no dialog at Run time)
        opts = self.viewer.get_production_options()
        if opts is None:
            QMessageBox.warning(
                self.viewer, "No Dataset",
                "No dataset available.\n"
                "Place a dataset under datasets/ or use "
                "'Add dataset folder…' to pick an external folder.",
            )
            return
        params = ProductionParams(
            dataset    = opts["dataset"],
            days       = opts["days"],
            dispatcher = opts["dispatcher"],
            alg        = opts["alg"],
        )
        params.fast_mode = opts["fast_mode"]
        self.production_params = params
        self._stop_playback()
        self._playback_clock = 0.0
        # Show the selected dataset at the right of the status bar
        self._update_production_run_info()

        # Results-first (fast) mode: use a very large snapshot interval so only start/end are recorded.
        # → The loop overhead disappears, computation runs at native PySCFabSim speed, results show immediately.
        fast = bool(getattr(params, "fast_mode", True))
        snap_days = float(params.days) * 10.0 if fast else 0.5

        self.production_runner = ProductionRunner(
            params, self.timeline_recorder, seed=opts["seed"],
            datasets_root=DATASET_DIR,
            snapshot_interval_days=snap_days,
        )
        self.production_runner.status_message.connect(self.viewer.statusBar().showMessage)
        self.production_runner.progress.connect(self._on_production_progress)
        self.production_runner.finished_ok.connect(self._on_production_finished)
        self.production_runner.failed.connect(self._on_production_failed)
        self.production_runner.results_ready.connect(self._on_production_results)
        if self.viewer.production_dashboard:
            self.viewer.production_dashboard.reset()
        self.production_runner.start()
        # Integrated: production computation also runs in the background → shown as LIVE
        self._reviewing = False
        self.viewer.set_reviewing(False)
        self.viewer.set_live_running(True)
        self._live_follow_timer.start()
        self.viewer.set_timeline_enabled(True)
        mode_msg = "results-only mode" if fast else "playback-recording mode"
        self.viewer.statusBar().showMessage(f"Starting production simulation... ({mode_msg})")

    def _on_production_progress(self, days, done, active):
        # Push the latest snapshot to the dashboard only when not reviewing the past
        if self._reviewing:
            return
        if self.viewer.production_dashboard:
            snap = self.timeline_recorder.at(self.timeline_recorder.duration)
            if snap:
                self.viewer.production_dashboard.set_snapshot(snap)

    def _on_production_results(self, summary: dict):
        """Show the result summary in the dashboard tables right after computation, and save it to files."""
        if self.viewer.production_dashboard:
            self.viewer.production_dashboard.set_results(summary)
        saved_dir = None
        try:
            saved_dir = self._save_production_results(summary)
        except Exception as e:  # noqa: BLE001
            print(f"[main_ui] ⚠️ Failed to save production results: {e}")
        tail = f" | saved to {saved_dir}" if saved_dir else ""
        self.viewer.statusBar().showMessage(
            f"Results displayed — done lots {summary.get('total_done')} "
            f"(compute time {summary.get('compute_duration', '-')}){tail}"
        )

    def _save_production_results(self, summary: dict) -> str:
        """Save production results under logs/production/<timestamp>/.

        Files written:
          - summary.json          : overall summary (including parameters)
          - lot_summary.csv       : aggregates per lot type
          - machine_summary.csv   : machine family utilization
          - lot_detail.csv        : per-lot detailed statistics
        Returns: path of the output folder
        """
        import os, csv, json
        run_dir = self._make_run_dir("production")

        # 1) summary.json (lot_stats_detail goes to a separate CSV and is excluded from the JSON)
        summary_for_json = {k: v for k, v in summary.items() if k != "lot_stats_detail"}
        with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary_for_json, f, indent=2, ensure_ascii=False)

        # 2) lot_summary.csv
        per_lot = summary.get("per_lot", [])
        with open(os.path.join(run_dir, "lot_summary.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["lot_type", "throughput", "avg_cycle_time_days",
                        "on_time_pct", "avg_tardiness_days"])
            for r in per_lot:
                w.writerow([r["lot_type"], r["throughput"], r["avg_cycle_time_days"],
                            r["on_time_pct"], r["avg_tardiness_days"]])

        # 3) machine_summary.csv (utilization + Run-down time)
        machines = summary.get("machines", [])
        with open(os.path.join(run_dir, "machine_summary.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "family", "count", "avg_util_pct",
                "avg_starvation_s", "p95_starvation_s",
                "breakdown_count", "breakdown_time_s", "pm_count", "pm_time_s",
            ])
            for r in machines:
                w.writerow([
                    r["family"], r["count"], r["avg_util_pct"],
                    r.get("avg_starvation_s", 0), r.get("p95_starvation_s", 0),
                    r.get("breakdown_count", 0), r.get("breakdown_time_s", 0),
                    r.get("pm_count", 0), r.get("pm_time_s", 0),
                ])

        # 4) lot_detail.csv (per lot_id)
        detail = summary.get("lot_stats_detail", {}) or {}
        with open(os.path.join(run_dir, "lot_detail.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["lot_id", "lot_type", "cycle_time_days", "on_time",
                        "tardiness_days", "waiting_time_days",
                        "processing_time_days", "transport_time_days",
                        "waiting_time_batching_days"])
            for lot_id, d in detail.items():
                w.writerow([
                    lot_id, d.get("lot_type", ""), round(d.get("CT", 0), 4),
                    d.get("on_time", 0), round(d.get("tardiness", 0), 4),
                    round(d.get("waiting_time", 0), 4),
                    round(d.get("processing_time", 0), 4),
                    round(d.get("transport_time", 0), 4),
                    round(d.get("waiting_time_batching", 0), 4),
                ])

        # 5) equipment_kpi.csv
        equipment = summary.get("equipment", {}) or {}
        with open(os.path.join(run_dir, "equipment_kpi.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["metric", "value", "unit"])
            for metric, unit in (
                ("starvation_count", "events"),
                ("total_starvation_s", "s"),
                ("avg_starvation_s", "s"),
                ("p95_starvation_s", "s"),
                ("max_starvation_s", "s"),
                ("censored_starvation_count", "events"),
                ("censored_starvation_s", "s"),
                ("breakdown_count", "events"),
                ("breakdown_time_s", "s"),
                ("pm_count", "events"),
                ("pm_time_s", "s"),
                ("total_downtime_s", "s"),
            ):
                w.writerow([metric, equipment.get(metric, 0), unit])

        print(f"[main_ui] Production results saved to: {run_dir}")
        return run_dir

    def _extract_production_timeseries(self) -> list:
        """Extract time-series metrics from the production snapshots of the timeline recorder."""
        rec = getattr(self, "timeline_recorder", None)
        if rec is None:
            return []
        out = []
        for snap in rec.snapshots:
            m = snap.metrics or {}
            if m.get("done_lots") is None and m.get("wip") is None:
                continue
            out.append({
                "time_days": m.get("time_days", round(snap.sim_time / 86400, 4)),
                "wip": m.get("wip", m.get("active_lots", 0)),
                "done_lots": m.get("done_lots", 0),
                "throughput_per_day": m.get("throughput_per_day", 0),
                "total_downtime_s": m.get("total_downtime_s", 0),
                "downtime_pct": m.get("downtime_pct", 0),
            })
        return out

    def _save_production_graphs(self, run_dir: str, summary: dict, series: list):
        """Save production results as PNG graphs (matplotlib, no GUI backend)."""
        import os
        import matplotlib
        matplotlib.use("Agg")  # avoid conflicts with the GUI thread
        import matplotlib.pyplot as plt

        # (a) WIP / Throughput / Downtime time series
        if series:
            t = [r["time_days"] for r in series]
            wip = [r["wip"] for r in series]
            done = [r["done_lots"] for r in series]
            dpct = [r["downtime_pct"] for r in series]

            fig, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
            axes[0].plot(t, wip, color="#2a7de1")
            axes[0].set_ylabel("WIP (lots)")
            axes[0].set_title("WIP over time")
            axes[0].grid(True, alpha=0.3)

            axes[1].plot(t, done, color="#27ae60")
            axes[1].set_ylabel("Done lots")
            axes[1].set_title("Cumulative completed lots")
            axes[1].grid(True, alpha=0.3)

            axes[2].plot(t, dpct, color="#e67e22")
            axes[2].set_ylabel("Downtime (%)")
            axes[2].set_xlabel("Time (days)")
            axes[2].set_title("Machine downtime ratio over time")
            axes[2].grid(True, alpha=0.3)

            fig.tight_layout()
            fig.savefig(os.path.join(run_dir, "timeseries.png"), dpi=120)
            plt.close(fig)

        # (b) Utilization & downtime per machine family (bars)
        machines = summary.get("machines", []) or []
        if machines:
            fams = [str(r["family"]) for r in machines]
            util = [r.get("avg_util_pct", 0) for r in machines]
            dpct = [r.get("downtime_pct", 0) for r in machines]
            x = range(len(fams))
            fig, ax = plt.subplots(figsize=(max(8, len(fams) * 0.6), 5))
            width = 0.4
            ax.bar([i - width / 2 for i in x], util, width, label="Util (%)", color="#2a7de1")
            ax.bar([i + width / 2 for i in x], dpct, width, label="Downtime (%)", color="#e67e22")
            ax.set_xticks(list(x))
            ax.set_xticklabels(fams, rotation=45, ha="right")
            ax.set_ylabel("%")
            ax.set_title("Machine family utilization & downtime")
            ax.legend()
            ax.grid(True, axis="y", alpha=0.3)
            fig.tight_layout()
            fig.savefig(os.path.join(run_dir, "machine_util.png"), dpi=120)
            plt.close(fig)

        # (c) Throughput & on-time delivery rate per lot type
        per_lot = summary.get("per_lot", []) or []
        if per_lot:
            names = [str(r["lot_type"]) for r in per_lot]
            th = [r.get("throughput", 0) for r in per_lot]
            ot = [r.get("on_time_pct", 0) for r in per_lot]
            x = range(len(names))
            fig, ax1 = plt.subplots(figsize=(max(8, len(names) * 0.6), 5))
            ax1.bar(list(x), th, color="#27ae60", alpha=0.8)
            ax1.set_ylabel("Throughput (lots)", color="#27ae60")
            ax1.set_xticks(list(x))
            ax1.set_xticklabels(names, rotation=45, ha="right")
            ax2 = ax1.twinx()
            ax2.plot(list(x), ot, color="#c0392b", marker="o")
            ax2.set_ylabel("On-time (%)", color="#c0392b")
            ax2.set_ylim(0, 105)
            ax1.set_title("Throughput & on-time rate by lot type")
            fig.tight_layout()
            fig.savefig(os.path.join(run_dir, "lot_throughput.png"), dpi=120)
            plt.close(fig)

        print(f"[main_ui] Production graphs saved to: {run_dir}")

    def _on_production_finished(self):
        rec = self.timeline_recorder
        self.viewer.set_timeline_bounds(rec.start_time, rec.duration)
        # Computation finished → leave LIVE, switch to playback mode
        self._live_follow_timer.stop()
        self.viewer.set_live_running(False)
        self._reviewing = False
        self.viewer.set_reviewing(False)
        self._playback_clock = rec.duration
        self._render_snapshot_at(rec.duration)

        fast = bool(getattr(self.production_params, "fast_mode", True))
        if fast:
            # Results-first mode: no playback recording, so disable the progress bar/Play
            self.viewer.set_timeline_enabled(False)
            self.viewer.statusBar().showMessage(
                "Production simulation complete — see the result tables. "
                "(To enable past replay, uncheck 'Results only, fast' and rerun.)"
            )
        else:
            self.viewer.set_timeline_enabled(True)
            self.viewer.statusBar().showMessage(
                "Production simulation complete — use ▶Play or the progress bar to replay."
            )

    def _on_production_failed(self, msg: str):
        QMessageBox.warning(self.viewer, "Production Simulation Failed", msg)

    @staticmethod
    def _validate_production_dataset(path: str):
        """Validate a PySCFabSim dataset folder.

        Returns: (ok: bool, message: str)
        The required .txt files and at least one route_*.txt must be present.
        """
        import os
        required = [
            "tool.txt.1l", "fromto.txt", "order.txt", "WIP.txt", "part.txt",
            "setup.txt", "setupgrp.txt", "downcal.txt", "pmcal.txt", "attach.txt",
        ]
        if not os.path.isdir(path):
            return False, f"Not a folder: {path}"
        existing = set(os.listdir(path))
        missing = [f for f in required if f not in existing]
        has_route = any(f.startswith("route_") and f.endswith(".txt") for f in existing)
        if not has_route:
            missing.append("route_*.txt (at least one)")
        if missing:
            return False, "Missing required files:\n  - " + "\n  - ".join(missing)
        return True, "Valid dataset"

    def on_production_add_dataset(self):
        """Panel's 'Add dataset folder…' — pick an external dataset folder and validate it."""
        folder = QFileDialog.getExistingDirectory(
            self.viewer,
            "Select production dataset folder (must contain tool.txt.1l, fromto.txt, ...)",
            "",
        )
        if not folder:
            return
        ok, msg = self._validate_production_dataset(folder)
        if not ok:
            QMessageBox.warning(
                self.viewer, "Invalid Dataset",
                f"The selected folder is not a valid PySCFabSim dataset.\n\n"
                f"{folder}\n\n{msg}",
            )
            return
        self.viewer.add_production_dataset(folder)

    def _on_sim_finished(self):
        """Slot called on the main thread when the simulation finishes."""
        if self.replay_recorder is not None:
            # Replay mode finished: the thread has already finalized (saved files + opened the Rerun viewer)
            out_dir = self.replay_recorder.out_dir
            self.replay_recorder = None
            self._live_follow_timer.stop()
            self.viewer.set_live_running(False)
            self.viewer.exit_playback_mode()
            self._reviewing = False
            self.viewer.set_reviewing(False)
            self._save_logs()
            self.viewer.statusBar().showMessage(
                f"Replay simulation complete — Rerun viewer opened | "
                f"outputs: {out_dir or '-'}"
            )
            return
        self._save_logs()
        # Integrated: leave LIVE → switch to playback (past-browsing) mode
        self._live_follow_timer.stop()
        self.viewer.set_live_running(False)
        self._reviewing = False
        self.viewer.set_reviewing(False)
        rec = self.timeline_recorder
        self.viewer.set_timeline_bounds(rec.start_time, rec.duration)
        processed = self.ds.num_of_processed_lot
        total     = self.ds.lot_count
        self.viewer.statusBar().showMessage(
            f"Simulation complete — {self.sim_duration:.0f}s | "
            f"Lots: {processed}/{total} | replay past via ▶Play or the progress bar"
        )

    # ── Simulation control ────────────────────────────────────
    def start_simulation(self):
        # Integrated: in production mode, branch to the production runner
        if self.app_mode == "production":
            self.start_production_simulation()
            return

        if self._is_sim_running():
            return

        if not self.ds.sections:
            self.viewer.statusBar().showMessage(
                "No sections. Load a layout first."
            )
            return

        if not self.fromto_data:
            self.viewer.statusBar().showMessage("Please load a FromTo file first.")
            return

        # Read directly from the panel-resident Run Options (no dialog at Run time)
        settings = self.viewer.get_logistics_options()
        num_oht = settings["num_oht"]
        self.sim_duration            = float(settings["duration"])
        self.kpi_save_mode           = settings["kpi_save_mode"]
        self.simulator_mode          = settings["simulator_mode"]
        self.default_processing_time = float(settings["default_processing_time"])
        custom_strategies = self._load_selected_strategies(self.custom_strategy_paths)
        if custom_strategies is None:
            return

        # Run mode: live (real-time animation) / replay (full-speed recording → Rerun playback)
        self.replay_recorder = None
        if settings.get("run_mode") == "replay":
            from ufast.viz.replay_recorder import ReplayRecorder
            self.replay_recorder = ReplayRecorder(self.ds, self.sim_duration)

        # ── Initialization ────────────────────────────────────
        self.ds.main_clock             = 0.0
        self.ds.event_queue.clear()
        self.ds.oht_list.clear()
        self.ds.num_of_processed_lot   = 0
        self.ds.lot_count              = 0
        self._last_idle_repo_scan      = -9999.0
        self._last_deadlock_check      = -9999.0
        self._last_status_update       = -9999.0

        # Integrated: reset the timeline recording (logistics uses a 1 sim-sec interval)
        self._stop_playback()
        self.viewer.exit_playback_mode()
        self._playback_clock = 0.0
        self.timeline_recorder.reset(mode="logistics", snapshot_interval=1.0)
        self.viewer.set_timeline_bounds(0.0, self.sim_duration)

        for sec in self.ds.sections:
            for buf in sec.oht_buffers:
                buf.buffer = [None] * buf.capacity

        # ── Controllers ───────────────────────────────────────
        self.vehicle_controller = VehicleController(
            num_oht, self.route_manager, self.bridge
        )
        self.vehicle_controller.init()
        self.vehicle_controller.set_custom_strategies(
            routing_strategy    = custom_strategies.get("routing"),
            assignment_strategy = custom_strategies.get("assignment"),
        )

        if self.route_manager is not None and custom_strategies.get("routing_cost") is not None:
            setter = getattr(self.route_manager, "set_custom_cost_function", None)
            if callable(setter):
                setter(custom_strategies.get("routing_cost"))
                print(f"[main_ui] ✅ Custom routing cost function applied: "
                      f"{describe_strategy(custom_strategies.get('routing_cost'))}")
            else:
                print("[main_ui] ⚠️ The current RouteManager does not support injecting a routing cost function.")

        if self.route_manager and self.bridge:
            self.vehicle_controller.dispatcher = Dispatcher(
                self.route_manager,
                self.bridge,
                strategy=NearestIdleStrategy(),
            )
            print("[main_ui] ✅ Dispatcher initialized (NearestIdleStrategy)")

        self.event_handler = EventHandler(self.vehicle_controller)
        self.event_handler.set_idle_positioning_strategy(custom_strategies.get("idle_positioning"))
        self.event_handler.simulator_mode          = self.simulator_mode
        self.event_handler.default_processing_time = self.default_processing_time
        self.event_handler.init_lot_events(self.fromto_data, self.sim_duration)

        # ── Loggers ───────────────────────────────────────────
        logger = get_logger()
        logger.reset(num_oht, self.sim_duration)
        for oht_id in self.ds.oht_list:
            logger.on_oht_init(oht_id, 0.0)

        if _ROUTE_PKG_AVAILABLE and get_logistics_logger:
            logi_log = get_logistics_logger()
            logi_log.reset()
            print("[main_ui] ✅ Logistics logger (LogisticsLogger) initialized")

        if self.event_handler:
            for oht in self.vehicle_controller.oht_list.values():
                if oht.status == "IDLE":
                    self.event_handler._oht_idle_since[oht.name] = 0.0
            self.event_handler.reposition_idle_ohts(0.0)
            print("[main_ui] ✅ Immediate repositioning of initially IDLE OHTs started")

        # ── Start the thread ──────────────────────────────────
        self.sim_thread = SimulationThread(self)
        self.sim_thread.status_message.connect(
            self.viewer.statusBar().showMessage
        )
        self.sim_thread.sim_finished.connect(self._on_sim_finished)
        self.sim_thread.start()

        # Integrated: show LIVE status + start auto-following of the progress bar
        self._reviewing = False
        self.viewer.set_reviewing(False)
        self.viewer.set_live_running(True)
        self.viewer.exit_playback_mode()
        self.viewer.set_timeline_enabled(True)
        self._live_follow_timer.start()

        if self.replay_recorder is not None:
            # Replay mode: the live animation, timeline dock, and results panel are not used.
            # (Results are provided through the Rerun viewer and Parquet files)
            self.viewer._playback_mode = True
            self.viewer.set_timeline_enabled(False)
            self._live_follow_timer.stop()
            self.viewer.results_dock.hide()
            self.viewer.statusBar().showMessage(
                f"Replay simulation started: {num_oht} OHTs, "
                f"{self.ds.lot_count} lots — running at full speed, "
                f"frame step {self.replay_recorder.frame_step:.1f}s"
            )
        else:
            # Real-time (Live) mode: show the statistics/legend results panel
            self.viewer.results_dock.show()
            self.viewer.statusBar().showMessage(
                f"Simulation started: {num_oht} OHTs, {self.ds.lot_count} lots"
            )

    def stop_simulation(self):
        if self.sim_thread and self.sim_thread.isRunning():
            self.sim_thread.request_stop()
            # Replay mode needs extra time to finalize (save files) when stopped
            self.sim_thread.wait(15000 if self.replay_recorder is not None else 3000)
        if self.replay_recorder is not None:
            self.replay_recorder = None
            self.viewer.exit_playback_mode()
        # Integrated: also stop the production runner safely
        if self.production_runner and self.production_runner.isRunning():
            self.production_runner.request_stop()
            self.production_runner.wait(5000)
        # Integrated: clean up LIVE/playback state
        self._live_follow_timer.stop()
        self.viewer.set_live_running(False)
        self._reviewing = False
        self.viewer.set_reviewing(False)
        self._stop_playback()
        # After stopping, the recording (if any) can still be replayed via the progress bar
        rec = self.timeline_recorder
        if len(rec) > 0:
            self.viewer.set_timeline_bounds(rec.start_time, rec.duration)
        self.viewer.statusBar().showMessage("Simulation stopped")

    def clear_all(self):
        self.stop_simulation()
        self.ds.clear()
        self.fromto_data = []
        self.vehicle_controller = None
        self.event_handler      = None
        if self.bridge:
            self.bridge.reset_penalties()
        # Integrated: reset timeline / playback state
        self._stop_playback()
        self.viewer.exit_playback_mode()
        self._playback_clock = 0.0
        self.timeline_recorder.reset(mode=self.app_mode, snapshot_interval=1.0)
        self.viewer.set_timeline_bounds(0.0, 0.0)
        self.viewer.set_timeline_position(0.0, 0.0)
        self.viewer.reset_input_file_labels()
        self.viewer.results_dock.hide()
        if self.viewer.production_dashboard:
            self.viewer.production_dashboard.reset()

    # ── Custom strategy settings dialog (panel's Custom strategies… button) ──
    def open_strategy_dialog(self):
        dialog = QDialog(self.viewer)
        dialog.setWindowTitle("Custom Strategy Settings")
        layout = QVBoxLayout(dialog)

        strategy_widget = StrategyConfigDialog(
            dialog,
            initial_paths   = self.custom_strategy_paths,
            initial_enabled = {k: bool(v) for k, v in self.custom_strategy_paths.items()},
        )
        layout.addWidget(strategy_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        strategy_paths = strategy_widget.values()
        for kind, path in strategy_paths.items():
            if path and not path.lower().endswith((".py", ".pkl", ".pickle")):
                QMessageBox.warning(
                    self.viewer, "Strategy File Error",
                    f"{StrategyConfigDialog._LABELS[kind]} only accepts "
                    f".py, .pkl, or .pickle files.",
                )
                return

        self.custom_strategy_paths = strategy_paths
        self.viewer.set_strategy_summary(
            sum(1 for v in strategy_paths.values() if v)
        )

    def _load_selected_strategies(self, strategy_paths):
        loaded = {
            "routing": None, "assignment": None,
            "idle_positioning": None, "routing_cost": None,
        }
        for kind, path in strategy_paths.items():
            if not path:
                continue
            try:
                loaded[kind] = load_strategy(path, kind)
                print(f"[main_ui] ✅ Custom {kind} strategy loaded: "
                      f"{describe_strategy(loaded[kind])} ({path})")
            except StrategyLoadError as e:
                QMessageBox.warning(self.viewer, "Strategy Load Failed", str(e))
                return None
            except Exception as e:
                QMessageBox.warning(self.viewer, "Strategy Load Failed", f"{path}\n\n{e}")
                return None
        return loaded

    def _make_run_dir(self, mode: str, base: str = "logs") -> str:
        """Create a timestamped folder for saving results.

        Structure: logs/<mode>/<YYYY-MM-DD_HH-MM-SS>/
        - mode: "logistics" or "production"
        Returns: absolute path of the created folder
        """
        import os
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        run_dir = os.path.join(base, mode, ts)
        os.makedirs(run_dir, exist_ok=True)
        return run_dir

    def _save_logs(self, log_dir: str = "logs"):
        import os
        try:
            # Accumulate under logs/logistics/<timestamp>/
            output_dir = self._make_run_dir("logistics", base=log_dir)

            logger      = get_logger()
            saved_files = logger.save(
                output_dir,
                kpi_mode       = self.kpi_save_mode,
                simulator_mode = self.simulator_mode,
            )

            if _ROUTE_PKG_AVAILABLE and get_logistics_logger \
                    and self.kpi_save_mode in ("logistics", "both"):
                logi_log    = get_logistics_logger()
                logi_saved  = logi_log.save(output_dir)
                saved_files.extend(logi_saved)
                stats = logi_log.get_stats()
                print(
                    f"[main_ui] Logistics logs saved: "
                    f"pathfind {stats['pathfind_count']}, "
                    f"dispatch {stats['dispatch_count']}, "
                    f"vehicle events {stats['vehicle_event_count']}"
                )
                for sf in logi_saved:
                    print(f"[main_ui]   saved: {sf}")
                if stats["dispatch_count"] == 0:
                    print("[main_ui] ⚠️  route_dispatch log has 0 rows.")

            try:
                from ufast.verification.verification import run_verification
                verification_saved = run_verification(
                    output_dir   = output_dir,
                    data_set     = self.ds,
                    logger       = logger,
                    route_manager = self.route_manager,
                )
                saved_files.extend(verification_saved)
            except Exception as ve:
                print(f"[Verification] ⚠️ Failed to save verification report: {ve}")

            msg = f"Results saved to:\n{output_dir}\n\n" + "\n".join(saved_files)
            QMessageBox.information(self.viewer, "Results Saved", msg)
        except Exception as e:
            QMessageBox.warning(self.viewer, "Save Failed", str(e))

    # ── File loading ──────────────────────────────────────────
    def _apply_layers(self, layers, skip_build=False):
        self.ds.layers = layers
        self.viewer.layer_panel.set_layers(layers)
        if not skip_build:
            self.rail_manager.build_layout(layers)
        self.viewer.draw_layout()

    def load_layout_file(self, filename):
        import os
        parser = DXFParser()
        layers = parser.parse(filename)
        self._apply_layers(layers)
        # The layer panel is needed for DXF → rail conversion, so show it automatically only here
        self.viewer.layer_panel.show()
        self.viewer.set_layout_file_label(os.path.basename(filename))

    def load_rail_file(self, filename):
        import os
        layers = load_rail_file(filename)
        self._apply_layers(layers, skip_build=True)
        self.viewer.set_layout_file_label(os.path.basename(filename))

        if _ROUTE_PKG_AVAILABLE:
            try:
                self.route_manager = RouteManager()
                self.route_manager.load_from_rail(filename)
                self.route_manager.initialize()

                self.bridge = SectionNodeBridge(self.route_manager, self.ds)
                self.bridge.build_mapping()

                node_count = len(self.route_manager.network.nodes)
                sec_count  = len(self.route_manager.network.sections)
                print(f"[main_ui] ✅ RouteManager initialized: {node_count} nodes, {sec_count} sections")
                print(f"[main_ui] ✅ Bridge initialized: {len(self.bridge.section_to_nodes)} sections mapped")
            except Exception as e:
                print(f"[main_ui] ⚠️  RouteManager initialization failed: {e} → using legacy Dijkstra")
                self.route_manager = None
                self.bridge        = None
        else:
            print("[main_ui] route package not available → using legacy Dijkstra")

    def save_rail_file(self, filename):
        if self.ds.layers:
            save_rail_file(filename, self.ds.layers)

    def load_fromto_file(self, filename):
        import os
        self.fromto_data = load_fromto(filename)
        count = len(self.fromto_data)
        self.fromto_filename = os.path.basename(filename)
        self.viewer.set_fromto_file_label(self.fromto_filename, count)
        self.viewer.statusBar().showMessage(f"FromTo loaded: {count} records")
        # In logistics mode, keep the current From-To chart shown on the status bar
        if self.app_mode == "logistics":
            self.viewer.set_run_info(
                f"From-To: {self.fromto_filename} ({count} records)"
            )

    # ── Layers / speed ────────────────────────────────────────
    def on_speed_changed(self, speed: float):
        self.sim_speed = speed

    def on_layer_changed(self):
        self.rail_manager.build_layout(self.ds.layers)
        self.viewer.draw_layout()

    def on_convert_to_rail(self):
        if not self.ds.layers:
            return
        has_rail = any(l.is_rail_layer for l in self.ds.layers)
        if not has_rail:
            return
        self.viewer.save_rail_dialog()

    def run(self):
        sys.exit(self.app.exec())


if __name__ == "__main__":
    sim_app = SimulationApp()
    sim_app.run()
