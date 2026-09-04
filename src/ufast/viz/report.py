"""
viz/report.py — post-run report generation for replay runs.

Reads the Parquet files (KPI time series + section congestion) in
logs/replay/<timestamp>/ and bundles static charts (PNG) into a single
report.html. Unlike the live view (Rerun), the goal is static output for
paper figures and sharing.

Generated files (inside the replay folder):
  - kpi_timeseries.png          4-panel KPI time series
  - congestion_heatmap.png      section x time congestion heatmap (time-series heatmap)
  - congestion_total.png        fab-wide congestion (occupancy sum) over time
  - report.html                 single report combining the charts above + summary table

Usage:
  python3 viz/report.py                     # latest folder under logs/replay, automatically
  python3 viz/report.py logs/replay/<ts>    # explicit folder
(ReplayRecorder.finalize() calls this automatically when a replay ends)
"""
from __future__ import annotations

import glob
import os
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)


def _time_axis(t_seconds):
    """Pick (converted value array, axis label) appropriate for the duration."""
    span = float(t_seconds.iloc[-1]) if len(t_seconds) else 0.0
    if span >= 2 * 86400:
        return t_seconds / 86400.0, "Time (days)"
    if span >= 2 * 3600:
        return t_seconds / 3600.0, "Time (h)"
    if span >= 600:
        return t_seconds / 60.0, "Time (min)"
    return t_seconds, "Time (s)"


def fig_kpi_timeseries(kpi, out_png: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x, xlabel = _time_axis(kpi["t"])

    def draw_throughput(ax):
        ax.plot(x, kpi["processed"], color="#27ae60", label="Completed lots")
        ax.set_ylabel("Completed lots")
        ax.grid(True, alpha=0.3)
        ax2 = ax.twinx()
        ax2.plot(x, kpi["throughput_lots_h"], color="#2a7de1", alpha=0.7)
        ax2.set_ylabel("Throughput (lots/h)", color="#2a7de1")
        ax.set_title("Throughput")

    def draw_fleet(ax):
        idle = kpi["idle"] + kpi.get("repositioning", 0)
        ax.stackplot(
            x, idle, kpi["assigned"], kpi["loaded"],
            labels=["IDLE+REPO", "ASSIGNED", "LOADED"],
            colors=["#4a6fe3", "#3cb96e", "#e2504a"], alpha=0.85,
        )
        ax.set_ylabel("OHT count")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_title("OHT fleet status")

    def draw_transport(ax):
        for col, label, color in (
            ("avg_transport_time", "Transport time (TT)", "#2a7de1"),
            ("avg_delivery_time", "Delivery time (DT)", "#e67e22"),
            ("avg_call_wait", "Call wait", "#8e44ad"),
        ):
            if col in kpi.columns:
                ax.plot(x, kpi[col], color=color, label=label)
        ax.set_ylabel("Seconds (cumulative avg)")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_title("Transport KPIs")

    def draw_wip(ax):
        ax.plot(x, kpi["wip"], color="#c0392b")
        ax.set_ylabel("WIP (lots)")
        ax.grid(True, alpha=0.3)
        ax.set_title("WIP (undelivered transport jobs)")

    # Draw only panels that have data (no empty axes when a column is missing)
    panels = [draw_throughput, draw_fleet]
    if any(c in kpi.columns for c in
           ("avg_transport_time", "avg_delivery_time", "avg_call_wait")):
        panels.append(draw_transport)
    if "wip" in kpi.columns:
        panels.append(draw_wip)

    fig, axes = plt.subplots(len(panels), 1,
                             figsize=(10, 3 * len(panels)), sharex=True)
    if len(panels) == 1:
        axes = [axes]
    for draw, ax in zip(panels, axes):
        draw(ax)
    axes[-1].set_xlabel(xlabel)

    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)


def fig_congestion_map(cong, geometry: dict, out_png: str):
    """Draw time-averaged congestion directly on the rail layout (paper fig_a style).

    geometry: {section_id(str): [strip, ...]}, strip = [[x, y], ...]
    Colour: RdYlGn_r (green = quiet -> red = congested), p95-normalised.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.collections import LineCollection

    means = cong.drop(columns=["t"]).mean(axis=0)   # section_id(str) → avg occ
    pos = means[means > 0]
    vmax = float(np.percentile(pos, 95)) if len(pos) else 1.0
    vmax = vmax or 1.0

    segments, values = [], []
    for sid, strips in geometry.items():
        v = float(means.get(sid, 0.0))
        for strip in strips:
            segments.append(strip)
            values.append(v)

    fig, ax = plt.subplots(figsize=(13, 7))
    lc = LineCollection(segments, cmap="RdYlGn_r", linewidths=2.2,
                        capstyle="round")
    lc.set_array(np.array(values))
    lc.set_clim(0.0, vmax)
    ax.add_collection(lc)
    ax.autoscale()
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Time-averaged section occupancy")
    fig.colorbar(lc, ax=ax, label="Time-averaged OHTs per section",
                 fraction=0.03, pad=0.02)
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)


def fig_congestion_heatmap(cong, out_png: str, top_n: int = 60):
    """Section x time congestion (mean occupancy) heatmap — the top_n sections by mean occupancy."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    t = cong["t"]
    x, xlabel = _time_axis(t)
    data = cong.drop(columns=["t"])
    means = data.mean(axis=0)
    top = means.sort_values(ascending=False).head(top_n)
    sel = data[top.index]                       # frames × top_n
    # Per-frame occupancy (0/1 flicker) looks like stippling, so smooth it with a
    # rolling mean along the time axis — window ~0.5% of the total (minimum 1)
    win = max(1, len(sel) // 200)
    if win > 1:
        sel = sel.rolling(window=win, min_periods=1).mean()
    mat = sel.to_numpy(dtype=float).T           # top_n × frames

    fig, ax = plt.subplots(figsize=(11, max(4.5, 0.12 * len(top))))
    im = ax.imshow(
        mat, aspect="auto", origin="upper", cmap="inferno",
        extent=[float(x.iloc[0]), float(x.iloc[-1]), len(top), 0],
        interpolation="nearest",
    )
    step = max(1, len(top) // 30)
    ax.set_yticks(np.arange(len(top))[::step] + 0.5)
    ax.set_yticklabels([f"sec{s}" for s in top.index[::step]], fontsize=7)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(f"Section (top {len(top)} by mean occupancy)")
    ax.set_title("Section occupancy over time (avg OHTs in section)")
    fig.colorbar(im, ax=ax, label="Avg occupancy (OHTs)")
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)


def fig_congestion_total(cong, out_png: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = cong["t"]
    x, xlabel = _time_axis(t)
    total = cong.drop(columns=["t"]).sum(axis=1)
    fig, ax = plt.subplots(figsize=(10, 3.2))
    ax.plot(x, total, color="#e67e22")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Total occupancy (OHTs on rail)")
    ax.set_title("Fab-wide congestion over time")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)


def _summary_rows(kpi, cong):
    def last(col):
        return kpi[col].iloc[-1] if col in kpi.columns and len(kpi) else None

    dur = kpi["t"].iloc[-1] if len(kpi) else 0.0
    data = cong.drop(columns=["t"])
    busiest = data.mean(axis=0).sort_values(ascending=False)
    rows = [
        ("Simulated time", f"{dur:,.0f} s ({dur / 3600:.2f} h)"),
        ("Frames recorded", f"{len(kpi):,}"),
        ("Sections tracked", f"{data.shape[1]:,}"),
        ("Completed lots (final)", f"{last('processed'):.0f}"),
        ("Throughput (final)", f"{last('throughput_lots_h'):.1f} lots/h"),
    ]
    for col, label, unit in (
        ("avg_transport_time", "Avg transport time (final)", "s"),
        ("avg_delivery_time", "Avg delivery time (final)", "s"),
        ("avg_call_wait", "Avg call wait (final)", "s"),
        ("wip", "WIP (final)", "lots"),
    ):
        v = last(col)
        if v is not None:
            rows.append((label, f"{v:.1f} {unit}"))
    rows.append(("Busiest section", f"sec{busiest.index[0]} "
                                    f"(avg {busiest.iloc[0]:.2f} OHTs)"))
    return rows


def build_html(out_dir: str, rows, images) -> str:
    items = "\n".join(
        f"<tr><td>{k}</td><td style='text-align:right'><b>{v}</b></td></tr>"
        for k, v in rows
    )
    imgs = "\n".join(
        f"<h2>{title}</h2>\n<img src='{os.path.basename(p)}' "
        f"style='max-width:100%;border:1px solid #ddd'/>"
        for title, p in images
    )
    run_name = os.path.basename(os.path.normpath(out_dir))
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>U-FAST replay report — {run_name}</title>
<style>
 body {{ font-family: -apple-system, sans-serif; max-width: 1000px;
         margin: 2rem auto; padding: 0 1rem; color: #222; }}
 table {{ border-collapse: collapse; min-width: 420px; }}
 td {{ border-bottom: 1px solid #eee; padding: 6px 14px 6px 0; }}
 h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin-top: 2rem; }}
 .meta {{ color: #888; font-size: 13px; }}
</style></head><body>
<h1>U-FAST replay report</h1>
<p class="meta">Run: {run_name} &nbsp;|&nbsp; Source: kpi_timeseries.parquet,
section_congestion.parquet</p>
<h2>Summary</h2>
<table>{items}</table>
{imgs}
</body></html>
"""
    path = os.path.join(out_dir, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def generate_report(replay_dir: str) -> str:
    """Read the Parquet files in a replay folder and generate PNG charts + report.html."""
    import pandas as pd

    kpi = pd.read_parquet(os.path.join(replay_dir, "kpi_timeseries.parquet"))
    cong = pd.read_parquet(os.path.join(replay_dir, "section_congestion.parquet"))

    images = []
    p = os.path.join(replay_dir, "kpi_timeseries.png")
    fig_kpi_timeseries(kpi, p)
    images.append(("KPI time series", p))

    # Spatial heatmap on the rails (when geometry is available) — otherwise a section x time matrix
    geo_path = os.path.join(replay_dir, "section_geometry.json")
    if os.path.isfile(geo_path):
        import json
        with open(geo_path, encoding="utf-8") as f:
            geometry = json.load(f)
        p = os.path.join(replay_dir, "congestion_map.png")
        fig_congestion_map(cong, geometry, p)
        images.append(("Time-averaged congestion on rail layout", p))
    else:
        p = os.path.join(replay_dir, "congestion_heatmap.png")
        fig_congestion_heatmap(cong, p)
        images.append(("Section congestion heatmap", p))

    p = os.path.join(replay_dir, "congestion_total.png")
    fig_congestion_total(cong, p)
    images.append(("Fab-wide congestion", p))

    html = build_html(replay_dir, _summary_rows(kpi, cong), images)
    print(f"[viz.report] Report generated: {html}")
    return html


def main():
    if len(sys.argv) > 1:
        replay_dir = sys.argv[1]
    else:
        dirs = sorted(glob.glob(os.path.join("logs", "replay", "*")))
        dirs = [d for d in dirs if os.path.isdir(d)]
        if not dirs:
            print("No results found in logs/replay/. Run in Replay mode first.")
            sys.exit(1)
        replay_dir = dirs[-1]
        print(f"(auto-selected latest replay: {replay_dir})")
    generate_report(replay_dir)


if __name__ == "__main__":
    main()
