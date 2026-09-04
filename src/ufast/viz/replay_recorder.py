"""
viz/replay_recorder.py — recorder for the GUI 'replay simulation' mode.

Unlike the real-time mode (the existing Qt animation), this runs the simulation
at full speed while recording aggregate metrics, then saves them to Parquet at
the end and replays them in the Rerun viewer.

Two-layer recording design:
  - Aggregate layer (whole run): per-section congestion (time-averaged number
    of occupying OHTs) and KPI time series. Size = sections x frames. The frame
    count is capped by frame_budget (default 10,000), so storage is bounded
    regardless of the run length.
  - Trajectory layer: OHT positions/states down-sampled at the frame step.
    Not being able to track an individual vehicle during fast replay is an
    accepted design limitation (per-vehicle verification is the job of the
    real-time mode).

on_step() is called from the SimulationThread (background), and finalize() is
called from the same thread at the end. No Qt objects are used at all.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from ufast.drawing.geometry import CQuadCurve

_OHT_SPEED_MM_S = 1000.0          # same interpolation speed as viewer.OHTItem
_CURVE_SAMPLES = 8                # number of segments for the curve polyline approximation

# OHT status colours (same semantics as the viewer legend)
_OHT_COLORS: Dict[str, List[int]] = {
    "IDLE":          [70, 110, 240, 220],
    "REPOSITIONING": [70, 110, 240, 220],
    "ASSIGNED":      [60, 200, 90, 255],
    "LOADED":        [230, 60, 50, 255],
}
_OHT_COLOR_MOVING = [255, 165, 0, 255]

_RAIL_COLOR_STATIC = [110, 110, 110, 160]

# Congestion palette — same RdYlGn family as the paper's fig_a (section heatmap):
# 0 = dark green (quiet) -> 0.5 = pale yellow -> 1 = red (congested)
_CONG_STOPS = [
    (0.0, (26, 152, 80)),     # green
    (0.5, (255, 255, 191)),   # pale yellow
    (1.0, (215, 48, 39)),     # red
]

# EQ status -> code -> colour (same colour scheme as viewer.update_animation)
_EQ_STATUS_CODES = {
    "IDLE": 0,
    "WAITING": 1,
    "PROCESS_WAITING": 2, "LOT_INBOUND": 2,
    "OHT_COMING": 3, "PROCESSING": 3,
}
_EQ_CODE_COLORS = {
    0: [160, 160, 160, 90],     # IDLE grey
    1: [255, 165, 0, 220],      # WAITING orange
    2: [135, 206, 250, 220],    # waiting for process, sky blue
    3: [50, 205, 50, 230],      # OHT approaching / processing, green
}
_EQ_HALF_SIZE_MM = 400.0


def _congestion_color(norm: float) -> List[int]:
    """Normalised congestion in 0..1 -> RdYlGn (green -> yellow -> red) gradient."""
    norm = max(0.0, min(norm, 1.0))
    for (t0, c0), (t1, c1) in zip(_CONG_STOPS, _CONG_STOPS[1:]):
        if norm <= t1:
            f = (norm - t0) / (t1 - t0) if t1 > t0 else 0.0
            return [int(a + (b - a) * f) for a, b in zip(c0, c1)] + [230]
    return list(_CONG_STOPS[-1][1]) + [230]


def _figure_polyline(fig) -> List[List[float]]:
    """Convert one section figure into a list of polyline points."""
    if isinstance(fig, CQuadCurve):
        pts = []
        for i in range(_CURVE_SAMPLES + 1):
            t = i / _CURVE_SAMPLES
            mt = 1.0 - t
            x = mt * mt * fig.start_x + 2 * mt * t * fig.ctrl_x + t * t * fig.end_x
            y = mt * mt * fig.start_y + 2 * mt * t * fig.ctrl_y + t * t * fig.end_y
            pts.append([x, y])
        return pts
    return [[fig.start_x, fig.start_y], [fig.end_x, fig.end_y]]


def _oht_xy(oht, clock: float, ds) -> Optional[List[float]]:
    """Same within-section position interpolation as viewer.OHTItem.update_position."""
    idx = ds.section_id_to_index.get(oht.current_section_id)
    if idx is None:
        return None
    section = ds.sections[idx]
    if not section.figures:
        return None
    fig = section.figures[0]
    elapsed = clock - oht.time_enter_current_section
    dist = elapsed * _OHT_SPEED_MM_S
    ratio = dist / section.length if section.length > 0 else 0.0
    ratio = max(0.0, min(1.0, ratio))
    if isinstance(fig, CQuadCurve):
        t = ratio
        mt = 1.0 - t
        return [
            mt * mt * fig.start_x + 2 * mt * t * fig.ctrl_x + t * t * fig.end_x,
            mt * mt * fig.start_y + 2 * mt * t * fig.ctrl_y + t * t * fig.end_y,
        ]
    return [
        fig.start_x + (fig.end_x - fig.start_x) * ratio,
        fig.start_y + (fig.end_y - fig.start_y) * ratio,
    ]


class ReplayRecorder:
    """Replay-mode recorder — on_step/finalize are called from the simulation thread."""

    def __init__(
        self,
        ds,
        duration: float,
        *,
        frame_budget: int = 10_000,
        out_base: str = os.path.join("logs", "replay"),
        spawn_viewer: bool = True,
        app_id: str = "UFAST_replay_gui",
    ):
        self.ds = ds
        self.duration = float(duration)
        self.frame_step = max(1.0, self.duration / frame_budget)
        self.sample_interval = min(1.0, self.frame_step)
        self.out_base = out_base
        self.spawn_viewer = spawn_viewer
        self.app_id = app_id
        self.out_dir: Optional[str] = None

        # The section list is fixed at start time (id order preserved)
        self._section_ids = [s.section_id for s in ds.sections]
        self._sec_index = {sid: i for i, sid in enumerate(self._section_ids)}

        # EQ list fixed (sorted by name) — status stored compactly as per-frame codes (bytearray)
        self._eq_names = sorted(ds.eq_list.keys())
        self._eq_centers = [
            [ds.eq_list[n].left, ds.eq_list[n].top] for n in self._eq_names
        ]
        self._eq_status_frames: List[bytearray] = []

        self._next_sample = 0.0
        self._next_frame = 0.0
        # Bucket accumulators: per-section occupancy sum / sample count
        self._occ_sum = [0.0] * len(self._section_ids)
        self._n_samples = 0

        # Frame storage (saved/logged in bulk in finalize)
        self.frame_times: List[float] = []
        self._congestion_rows: List[List[float]] = []   # frames x sections mean occupancy
        self._oht_names: List[str] = []
        self._oht_positions: List[List[List[float]]] = []  # frames × ohts × [x,y]
        self._oht_colors: List[List[List[int]]] = []
        self._kpi_rows: List[dict] = []

    @property
    def frame_count(self) -> int:
        return len(self.frame_times)

    # ── Simulation-thread hooks ──────────────────────────────
    def on_step(self, clock: float):
        if clock >= self._next_sample:
            self._sample_occupancy()
            self._next_sample += self.sample_interval
        if clock >= self._next_frame:
            self._flush_frame(clock)
            self._next_frame += self.frame_step

    def _sample_occupancy(self):
        for oht in self.ds.oht_list.values():
            i = self._sec_index.get(oht.current_section_id)
            if i is not None:
                self._occ_sum[i] += 1.0
        self._n_samples += 1

    def _flush_frame(self, clock: float):
        n = self._n_samples or 1
        self._congestion_rows.append([s / n for s in self._occ_sum])
        self._occ_sum = [0.0] * len(self._section_ids)
        self._n_samples = 0

        if not self._oht_names:
            self._oht_names = sorted(self.ds.oht_list.keys())
        positions, colors = [], []
        for name in self._oht_names:
            oht = self.ds.oht_list.get(name)
            xy = _oht_xy(oht, clock, self.ds) if oht is not None else None
            if xy is None:
                xy = [0.0, 0.0]
                colors.append([0, 0, 0, 0])       # unknown position -> transparent
            else:
                colors.append(_OHT_COLORS.get(oht.status, _OHT_COLOR_MOVING))
            positions.append(xy)
        self._oht_positions.append(positions)
        self._oht_colors.append(colors)

        eq_codes = bytearray(len(self._eq_names))
        for i, name in enumerate(self._eq_names):
            eq = self.ds.eq_list.get(name)
            status = getattr(eq, "eq_status", "IDLE") if eq is not None else "IDLE"
            eq_codes[i] = _EQ_STATUS_CODES.get(status, 0)
        self._eq_status_frames.append(eq_codes)

        self.frame_times.append(clock)
        self._kpi_rows.append(self._collect_kpis(clock))

    def _collect_kpis(self, clock: float) -> dict:
        ds = self.ds
        idle = repo = assigned = loaded = 0
        for o in ds.oht_list.values():
            s = o.status
            if s == "IDLE":
                idle += 1
            elif s == "REPOSITIONING":
                repo += 1
            elif s == "ASSIGNED":
                assigned += 1
            elif s == "LOADED":
                loaded += 1
        row = {
            "t": clock,
            "processed": ds.num_of_processed_lot,
            "total_lots": ds.lot_count,
            "oht_count": len(ds.oht_list),
            "idle": idle,
            "repositioning": repo,
            "assigned": assigned,
            "loaded": loaded,
            "throughput_lots_h": (ds.num_of_processed_lot / clock * 3600.0)
                                 if clock > 0 else 0.0,
        }
        try:
            from ufast.common.logger import get_logger
            k = get_logger().get_live_kpis()
            row.update({
                "avg_transport_time": k.get("avg_transport_time", 0.0),
                "avg_delivery_time":  k.get("avg_delivery_time", 0.0),
                "avg_call_wait":      k.get("avg_call_wait", 0.0),
                "wip":                k.get("wip", 0),
            })
        except Exception:
            pass
        return row

    # ── Finalisation: save Parquet + log to Rerun ─────────────
    def finalize(self) -> Optional[str]:
        if not self.frame_times:
            return None
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.out_dir = os.path.join(self.out_base, ts)
        os.makedirs(self.out_dir, exist_ok=True)

        self._save_parquet()
        try:
            self._save_geometry()
        except Exception as e:  # noqa: BLE001
            print(f"[replay_recorder] ⚠️ Failed to save geometry: {e}")
        try:
            self._log_rerun()
        except Exception as e:  # noqa: BLE001 — a viewer failure must not block saving results
            print(f"[replay_recorder] ⚠️ Rerun logging failed: {e}")
        try:
            # Post-run static report (KPI time-series charts + congestion heatmap + HTML)
            from ufast.viz.report import generate_report
            generate_report(self.out_dir)
        except Exception as e:  # noqa: BLE001
            print(f"[replay_recorder] ⚠️ Report generation failed: {e}")
        return self.out_dir

    def _save_parquet(self):
        import pandas as pd
        kpi = pd.DataFrame(self._kpi_rows)
        kpi_path = os.path.join(self.out_dir, "kpi_timeseries.parquet")
        kpi.to_parquet(kpi_path, index=False)

        cong = pd.DataFrame(
            self._congestion_rows,
            columns=[str(s) for s in self._section_ids],
            dtype="float32",
        )
        cong.insert(0, "t", self.frame_times)
        cong_path = os.path.join(self.out_dir, "section_congestion.parquet")
        cong.to_parquet(cong_path, index=False)
        print(f"[replay_recorder] Parquet saved: {kpi_path}, {cong_path} "
              f"({self.frame_count} frames × {len(self._section_ids)} sections)")

    def _section_strips(self):
        """Section figures -> list of polylines + owning section index per strip."""
        strips: List[List[List[float]]] = []
        strip_section: List[int] = []
        for si, section in enumerate(self.ds.sections):
            for fig in section.figures:
                strips.append(_figure_polyline(fig))
                strip_section.append(si)
        return strips, strip_section

    def _save_geometry(self):
        """Save the section geometry — used by the report (viz.report) to draw
        congestion directly on the rails."""
        import json
        strips, strip_section = self._section_strips()
        geo: Dict[str, list] = {}
        for strip, si in zip(strips, strip_section):
            geo.setdefault(str(self._section_ids[si]), []).append(strip)
        path = os.path.join(self.out_dir, "section_geometry.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(geo, f)
        print(f"[replay_recorder] Geometry saved: {path}")

    def _log_rerun(self):
        import rerun as rr

        rr.init(self.app_id, spawn=self.spawn_viewer)
        try:
            rr.disable_timeline("log_time")
        except Exception:
            pass

        # ── Static rails ──
        strips, strip_section = self._section_strips()
        rr.log("layout/rails",
               rr.LineStrips2D(strips, colors=_RAIL_COLOR_STATIC, radii=60.0),
               static=True)

        # ── Static EQ markers (with name labels) — status colour updated per frame ──
        can_partial_boxes = hasattr(rr.Boxes2D, "from_fields")
        if self._eq_names:
            rr.log("layout/eqs",
                   rr.Boxes2D(centers=self._eq_centers,
                              half_sizes=[[_EQ_HALF_SIZE_MM, _EQ_HALF_SIZE_MM]]
                                         * len(self._eq_names),
                              colors=_EQ_CODE_COLORS[0],
                              labels=self._eq_names,
                              show_labels=False),
                   static=True)

        # Congestion normalisation reference: 95th percentile of positive occupancy over all frames
        flat = [v for row in self._congestion_rows for v in row if v > 0]
        if flat:
            flat.sort()
            p95 = flat[int(len(flat) * 0.95) - 1] if len(flat) > 1 else flat[0]
            p95 = p95 or 1.0
        else:
            p95 = 1.0

        can_partial = hasattr(rr.LineStrips2D, "from_fields")
        for f, t in enumerate(self.frame_times):
            rr.set_time("sim_time", duration=t)

            row = self._congestion_rows[f]
            colors = [_congestion_color(row[si] / p95) for si in strip_section]
            if can_partial:
                rr.log("layout/congestion", rr.LineStrips2D.from_fields(colors=colors))
            else:
                rr.log("layout/congestion",
                       rr.LineStrips2D(strips, colors=colors, radii=90.0))

            rr.log("ohts", rr.Points2D(self._oht_positions[f],
                                       colors=self._oht_colors[f],
                                       radii=600.0))

            if self._eq_names:
                eq_colors = [_EQ_CODE_COLORS[c] for c in self._eq_status_frames[f]]
                if can_partial_boxes:
                    rr.log("layout/eqs", rr.Boxes2D.from_fields(colors=eq_colors))
                else:
                    rr.log("layout/eqs",
                           rr.Boxes2D(centers=self._eq_centers,
                                      half_sizes=[[_EQ_HALF_SIZE_MM, _EQ_HALF_SIZE_MM]]
                                                 * len(self._eq_names),
                                      colors=eq_colors,
                                      labels=self._eq_names,
                                      show_labels=False))

            k = self._kpi_rows[f]
            rr.log("kpi/throughput_lots_h", rr.Scalars(k["throughput_lots_h"]))
            rr.log("kpi/loaded", rr.Scalars(float(k["loaded"])))
            rr.log("kpi/idle", rr.Scalars(float(k["idle"])))
            if "wip" in k:
                rr.log("kpi/wip", rr.Scalars(float(k["wip"])))
            if "avg_transport_time" in k:
                rr.log("kpi/avg_transport_time", rr.Scalars(k["avg_transport_time"]))

        # Partial updates are invisible if the first frame carries no congestion geometry,
        # so in partial mode lay the geometry down once as a static entity.
        if can_partial:
            rr.set_time("sim_time", duration=self.frame_times[0])
            rr.log("layout/congestion",
                   rr.LineStrips2D(strips,
                                   colors=[_congestion_color(0.0)] * len(strips),
                                   radii=90.0),
                   static=True)

        rrd_path = os.path.join(self.out_dir, "replay.rrd")
        try:
            rr.save(rrd_path)
            print(f"[replay_recorder] .rrd saved: {rrd_path}")
        except Exception as e:  # noqa: BLE001
            print(f"[replay_recorder] ⚠️ Failed to save .rrd (viewer is unaffected): {e}")
        print(f"[replay_recorder] Rerun logging complete — {self.frame_count} frames")
