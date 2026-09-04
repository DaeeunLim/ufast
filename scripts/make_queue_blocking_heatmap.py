#!/usr/bin/env python3
"""Per-section blocking heatmap of the queue congestion model.

Draws amhs.blocked_time_by_section from the result JSON on top of the rail
layout.  The style is identical to paper Figure (a) (make_fig_a_heatmap.py):
RdYlGn_r sequential colormap + PowerNorm, sections without blocking in light
grey.

Usage:
  python scripts/make_queue_blocking_heatmap.py <result.json> [rail_file] [out.png]
  (rail_file defaults to meta.rail_file, out defaults to <json_base>_blocking_heatmap.png)
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import PowerNorm
import numpy as np

from ufast.route import RouteManager, SectionNodeBridge


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    json_path = sys.argv[1]
    with open(json_path) as f:
        data = json.load(f)
    meta, amhs = data['meta'], data['amhs']
    rail_file = sys.argv[2] if len(sys.argv) > 2 else meta['rail_file']
    out_path = (sys.argv[3] if len(sys.argv) > 3
                else json_path.replace('.json', '_blocking_heatmap.png'))

    blocked_time = {int(k): v
                    for k, v in amhs.get('blocked_time_by_section', {}).items()}
    if not blocked_time:
        sys.exit("blocked_time_by_section is missing from the result "
                 "(check that the run used --congestion queue).")

    rm = RouteManager()
    rm.load_from_rail(rail_file)
    rm.initialize()
    bridge = SectionNodeBridge(rm)
    bridge.build_mapping()

    nodes = rm.network.nodes
    segments, values = [], []
    for sec_id, sec_nodes in bridge.section_to_nodes.items():
        pts = [(nodes[n].x, nodes[n].y) for n in sec_nodes if n in nodes]
        if len(pts) < 2:
            continue
        bt = blocked_time.get(sec_id, 0.0)
        for i in range(len(pts) - 1):
            segments.append([pts[i], pts[i + 1]])
            values.append(bt)

    values = np.array(values)
    fig, ax = plt.subplots(figsize=(7.0, 4.2), dpi=300)

    # Background: whole rail in light grey (same style as Figure (a))
    base = LineCollection(segments, colors='#d9d9d9', linewidths=0.8, zorder=1)
    ax.add_collection(base)

    # Sections with blocking: green (mild) -> yellow -> red (severe). Unblocked sections stay grey.
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
    cbar.set_label('Blocked waiting time per section [s]', fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, bbox_inches='tight')
    fig.savefig(out_path.replace('.png', '.pdf'), bbox_inches='tight')
    print(f"Saved: {out_path} (+.pdf) — {len(blocked_time)} blocked sections, "
          f"{amhs.get('blocked_events', 0):,} blocking events")


if __name__ == '__main__':
    main()
