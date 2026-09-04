#!/usr/bin/env python3
"""Per-section time-averaged occupancy heatmap of the queue congestion model
(queue-model version of paper Figure (a)).

Runs an instrumented 1-day HVLM co-simulation with --congestion queue,
collects the time-averaged occupancy ∫n(s,t)dt / T per section, and draws it
on top of the rail layout in the same style as Figure (a) (RdYlGn_r +
PowerNorm).  Vehicles stopped by blocking are also counted as occupancy, so
placing this next to the delay-model Figure (a) reveals how the two fidelity
levels represent congestion differently.

Usage:
  PYTHONPATH=src python scripts/make_queue_occupancy_heatmap.py [out.png]
  Optional environment variables: UFAST_HEATMAP_DAYS (default 1),
                UFAST_HEATMAP_OHT (default 100), UFAST_HEATMAP_CONGESTION (default queue)
"""
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'src'))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import PowerNorm
import numpy as np

from ufast.cosim.amhs import AMHSExecutor
from ufast.cosim.run import run_ufast


def main():
    out_path = (sys.argv[1] if len(sys.argv) > 1
                else os.path.join(_ROOT, 'results',
                                  'queue_occupancy_heatmap.png'))
    days = int(os.environ.get('UFAST_HEATMAP_DAYS', '1'))
    num_oht = int(os.environ.get('UFAST_HEATMAP_OHT', '100'))
    congestion = os.environ.get('UFAST_HEATMAP_CONGESTION', 'queue')

    # ── Instrumentation: wrap _add/_remove_inflight to collect the time integral ∫n dt ──
    occ_integral = defaultdict(float)   # sec -> ∫ n dt
    occ_last_t = defaultdict(float)     # sec -> time of last change
    _orig_add = AMHSExecutor._add_inflight
    _orig_remove = AMHSExecutor._remove_inflight

    def _accumulate(self, sec):
        t = self.last_event_time
        n = self.section_inflight.get(sec, 0)
        occ_integral[sec] += n * max(0.0, t - occ_last_t[sec])
        occ_last_t[sec] = t

    def _add(self, sec, *args, **kwargs):
        _accumulate(self, sec)
        return _orig_add(self, sec, *args, **kwargs)

    def _remove(self, sec, *args, **kwargs):
        _accumulate(self, sec)
        return _orig_remove(self, sec, *args, **kwargs)

    AMHSExecutor._add_inflight = _add
    AMHSExecutor._remove_inflight = _remove
    try:
        _instance, amhs, _ = run_ufast(
            os.path.join(_ROOT, 'dataset', 'HVLM'),
            os.path.join(_ROOT, 'dataset', 'SMAT2022.rail'),
            days=days, num_oht=num_oht, seed=0,
            congestion_model=congestion,
        )
    finally:
        AMHSExecutor._add_inflight = _orig_add
        AMHSExecutor._remove_inflight = _orig_remove

    T = max(amhs.last_event_time, 1.0)
    mean_occ = {sec: v / T for sec, v in occ_integral.items()}
    print(f"[queue_occ] sections with traffic: {len(mean_occ)}, "
          f"max mean occ: {max(mean_occ.values()):.3f}")

    nodes = amhs.rm.network.nodes
    segments, values = [], []
    for sec, names in amhs.bridge.section_to_nodes.items():
        pts = [(nodes[n].x, nodes[n].y) for n in names if n in nodes]
        if len(pts) < 2:
            continue
        for i in range(len(pts) - 1):
            segments.append([pts[i], pts[i + 1]])
            values.append(mean_occ.get(sec, 0.0))

    values = np.array(values)
    fig, ax = plt.subplots(figsize=(7.0, 4.2), dpi=300)
    base = LineCollection(segments, colors='#d9d9d9', linewidths=0.8, zorder=1)
    ax.add_collection(base)
    mask = values > 0
    hot = LineCollection(
        [s for s, m in zip(segments, mask) if m],
        array=values[mask], cmap='RdYlGn_r',
        norm=PowerNorm(gamma=0.45, vmin=0, vmax=values.max()),
        linewidths=2.0, zorder=2, capstyle='round')
    ax.add_collection(hot)
    ax.autoscale()
    ax.set_aspect('equal')
    ax.axis('off')
    cbar = fig.colorbar(hot, ax=ax, fraction=0.03, pad=0.01)
    cbar.set_label('Time-averaged OHTs per section', fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, bbox_inches='tight')
    fig.savefig(out_path.replace('.png', '.pdf'), bbox_inches='tight')
    print(f"[queue_occ] Saved: {out_path} (+.pdf)")


if __name__ == '__main__':
    main()
