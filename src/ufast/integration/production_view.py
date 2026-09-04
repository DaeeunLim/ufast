"""
ProductionDashboard
===================
Visualisation widget dedicated to production mode.

Unlike the AMHS, the production simulator is not a spatial model with OHTs moving
on rails; it is expressed through aggregate machine/lot metrics (throughput, WIP,
utilisation). The central screen is therefore laid out as a dashboard.

During timeline replay, set_snapshot() receives the Snapshot.metrics of that
moment and refreshes the metric text and simple bar gauges.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QFont
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QLabel,
    QGroupBox,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QScrollArea,
)

from ufast.integration.timeline import Snapshot


class _Gauge(QFrame):
    """Bar gauge for a 0..1 ratio."""

    def __init__(self, color="#32CD32", parent=None):
        super().__init__(parent)
        self.setMinimumHeight(22)
        self._ratio = 0.0
        self._color = QColor(color)
        self._caption = ""

    def set_value(self, ratio: float, caption: str = ""):
        self._ratio = max(0.0, min(1.0, ratio))
        self._caption = caption
        self.update()

    def paintEvent(self, _evt):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor("#888"), 1))
        p.setBrush(QBrush(QColor("#2a2a2a")))
        p.drawRoundedRect(rect, 4, 4)
        fill = rect.adjusted(0, 0, 0, 0)
        fill.setWidth(int(rect.width() * self._ratio))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._color))
        p.drawRoundedRect(fill, 4, 4)
        p.setPen(QPen(QColor("#fff")))
        f = QFont()
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._caption)
        p.end()


class ProductionDashboard(QWidget):
    """Dashboard showing production simulation results at a given moment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        root = QVBoxLayout(inner)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Production Simulation (PySCFabSim)")
        tf = QFont()
        tf.setPointSize(16)
        tf.setBold(True)
        title.setFont(tf)
        root.addWidget(title)

        # Current dataset display (always visible before/during/after a run)
        self.dataset_label = QLabel("Dataset: (not selected)")
        dsf = QFont()
        dsf.setPointSize(12)
        dsf.setBold(True)
        self.dataset_label.setFont(dsf)
        self.dataset_label.setStyleSheet("color:#7fd0ff;")
        root.addWidget(self.dataset_label)

        self.time_label = QLabel("Day 0.000")
        df = QFont()
        df.setPointSize(13)
        self.time_label.setFont(df)
        root.addWidget(self.time_label)

        # ── Metrics grid ──
        metrics_group = QGroupBox("Lot Metrics")
        grid = QGridLayout(metrics_group)
        self._metric_labels = {}
        rows = [
            ("Done Lots", "done_lots"),
            ("Active Lots (WIP)", "active_lots"),
            ("Dispatchable Lots", "dispatchable_lots"),
            ("Total Lots", "total_lots"),
            ("Throughput (Lots/day)", "throughput_per_day"),
            ("Run-down Time (h)", "downtime_hours"),
            ("Downtime (%)", "downtime_pct"),
        ]
        for i, (text, key) in enumerate(rows):
            lbl = QLabel(text)
            val = QLabel("-")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            vf = QFont()
            vf.setBold(True)
            val.setFont(vf)
            grid.addWidget(lbl, i, 0)
            grid.addWidget(val, i, 1)
            self._metric_labels[key] = val
        root.addWidget(metrics_group)

        # ── Gauges ──
        gauge_group = QGroupBox("Progress / Utilization")
        gl = QVBoxLayout(gauge_group)

        gl.addWidget(QLabel("Lot Completion Rate"))
        self.progress_gauge = _Gauge("#32CD32")
        gl.addWidget(self.progress_gauge)

        gl.addWidget(QLabel("Machine Utilization (fraction with waiting lots)"))
        self.util_gauge = _Gauge("#FFA500")
        gl.addWidget(self.util_gauge)

        root.addWidget(gauge_group)

        self.machine_label = QLabel("Machines: -")
        root.addWidget(self.machine_label)

        # ── Result summary tables (shown as soon as computation finishes) ──
        self.result_group = QGroupBox("Result Summary (shown on completion)")
        rg = QVBoxLayout(self.result_group)

        self.result_header = QLabel("No results yet. Run the simulation to display results.")
        self.result_header.setStyleSheet("color:#888;")
        rg.addWidget(self.result_header)

        # ── Additional production KPI summary (WIP / run-down time) ──
        kpi_grid = QGridLayout()
        self._result_kpi_labels = {}
        kpi_rows = [
            ("Avg WIP", "avg_wip"),
            ("Peak WIP", "peak_wip"),
            ("Total Run-down (h)", "total_down_h"),
            ("Avg Down/Machine (h)", "avg_down_h"),
            ("Downtime (%)", "down_pct"),
            ("Availability (%)", "avail_pct"),
        ]
        for i, (text, key) in enumerate(kpi_rows):
            lbl = QLabel(text)
            val = QLabel("-")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            vf = QFont()
            vf.setBold(True)
            val.setFont(vf)
            kpi_grid.addWidget(lbl, i // 2, (i % 2) * 2)
            kpi_grid.addWidget(val, i // 2, (i % 2) * 2 + 1)
            self._result_kpi_labels[key] = val
        rg.addLayout(kpi_grid)

        rg.addWidget(QLabel("Per-Lot-Type Metrics"))
        self.lot_table = QTableWidget(0, 5)
        self.lot_table.setHorizontalHeaderLabels(
            ["Lot Type", "Throughput", "Avg CT (days)", "On-time (%)", "Avg Tardiness (days)"]
        )
        self.lot_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.lot_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.lot_table.setMaximumHeight(220)
        rg.addWidget(self.lot_table)

        rg.addWidget(QLabel("Machine Family Utilization & Run-down Time"))
        self.machine_table = QTableWidget(0, 5)
        self.machine_table.setHorizontalHeaderLabels(
            ["Family", "Count", "Avg Util (%)", "Avg Down (h)", "Down (%)"]
        )
        self.machine_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.machine_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.machine_table.setMaximumHeight(180)
        rg.addWidget(self.machine_table)

        root.addWidget(self.result_group)

        self.hint_label = QLabel(
            "Results appear in the tables above as soon as the run finishes. "
            "Use the bottom progress bar to replay past states over time."
        )
        self.hint_label.setStyleSheet("color:#888;")
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)

    # ── Updates ─────────────────────────────────────────────
    def set_snapshot(self, snap: Optional[Snapshot]):
        if snap is None:
            return
        m = snap.metrics
        self.time_label.setText(f"Day {m.get('time_days', 0):.3f}")

        for key, lbl in self._metric_labels.items():
            if key == "downtime_hours":
                lbl.setText(f"{m.get('total_downtime_s', 0) / 3600:.1f}")
                continue
            if key == "downtime_pct":
                lbl.setText(f"{m.get('downtime_pct', 0):.2f}")
                continue
            v = m.get(key, "-")
            lbl.setText(str(v))

        total = max(1, m.get("total_lots", 1))
        done = m.get("done_lots", 0)
        self.progress_gauge.set_value(done / total, f"{done}/{total}")

        mt = max(1, m.get("machines_total", 1))
        busy = m.get("machines_busy", 0)
        self.util_gauge.set_value(busy / mt, f"{busy}/{mt} busy")
        self.machine_label.setText(
            f"Machines: total {m.get('machines_total', 0)} "
            f"/ busy {busy} / idle {m.get('machines_idle', 0)}"
        )

    def set_dataset_info(self, text: str):
        """Show the current dataset info at the top of the dashboard."""
        self.dataset_label.setText(text)

    def set_results(self, summary: dict):
        """Called as soon as computation finishes — fills the tables with the result summary."""
        self.result_header.setStyleSheet("color:#fff;")
        self.result_header.setText(
            f"Dataset {summary.get('dataset')} | {summary.get('days')} days | "
            f"Done lots {summary.get('total_done')} | "
            f"Compute time {summary.get('compute_duration', '-')}"
        )

        per_lot = summary.get("per_lot", [])
        self.lot_table.setRowCount(len(per_lot))
        for i, row in enumerate(per_lot):
            self.lot_table.setItem(i, 0, QTableWidgetItem(str(row["lot_type"])))
            self.lot_table.setItem(i, 1, QTableWidgetItem(str(row["throughput"])))
            self.lot_table.setItem(i, 2, QTableWidgetItem(f"{row['avg_cycle_time_days']:.2f}"))
            self.lot_table.setItem(i, 3, QTableWidgetItem(f"{row['on_time_pct']:.1f}"))
            self.lot_table.setItem(i, 4, QTableWidgetItem(f"{row['avg_tardiness_days']:.2f}"))

        machines = summary.get("machines", [])
        self.machine_table.setRowCount(len(machines))
        for i, row in enumerate(machines):
            self.machine_table.setItem(i, 0, QTableWidgetItem(str(row["family"])))
            self.machine_table.setItem(i, 1, QTableWidgetItem(str(row["count"])))
            self.machine_table.setItem(i, 2, QTableWidgetItem(f"{row['avg_util_pct']:.1f}"))
            self.machine_table.setItem(i, 3, QTableWidgetItem(f"{row.get('avg_downtime_s', 0) / 3600:.2f}"))
            self.machine_table.setItem(i, 4, QTableWidgetItem(f"{row.get('downtime_pct', 0):.2f}"))

        # ── Fill the WIP / run-down time summary ──
        wip = summary.get("wip", {}) or {}
        down = summary.get("downtime", {}) or {}
        kpi_vals = {
            "avg_wip": wip.get("avg_wip", "-"),
            "peak_wip": wip.get("peak_wip", "-"),
            "total_down_h": f"{down.get('total_down_s', 0) / 3600:.1f}",
            "avg_down_h": f"{down.get('avg_down_per_machine_s', 0) / 3600:.2f}",
            "down_pct": f"{down.get('down_pct', 0):.2f}",
            "avail_pct": f"{down.get('avail_pct', 0):.2f}",
        }
        for key, lbl in self._result_kpi_labels.items():
            lbl.setText(str(kpi_vals.get(key, "-")))

    def reset(self):
        self.time_label.setText("Day 0.000")
        for lbl in self._metric_labels.values():
            lbl.setText("-")
        self.progress_gauge.set_value(0.0, "")
        self.util_gauge.set_value(0.0, "")
        self.machine_label.setText("Machines: -")
        self.result_header.setStyleSheet("color:#888;")
        self.result_header.setText("No results yet. Run the simulation to display results.")
        for lbl in getattr(self, "_result_kpi_labels", {}).values():
            lbl.setText("-")
        self.lot_table.setRowCount(0)
        self.machine_table.setRowCount(0)
