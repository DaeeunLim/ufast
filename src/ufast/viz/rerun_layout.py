"""
viz/rerun_layout.py — static SMAT2022 fab layout visualisation with Rerun.

Standalone:
    python3 -m ufast.viz.rerun_layout
    python3 -m ufast.viz.rerun_layout --rail dataset/SMAT2022.rail
    python3 -m ufast.viz.rerun_layout --no-spawn --save layout.rrd

As a library (reused by the replay):
    from ufast.viz.rerun_layout import log_layout_entities, show_layout
    show_layout('dataset/SMAT2022.rail')           # init + log + (optional) viewer
    log_layout_entities('dataset/SMAT2022.rail')   # assumes init; log only
"""
from __future__ import annotations
import argparse
import os
import sys
from typing import Dict, Optional, Tuple

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

try:
    import rerun as rr
except ImportError:
    rr = None

from ufast.paths import DATASET_DIR
from ufast.route import RouteManager


# ── Colours / sizes (coordinates in mm, fab ~ 300 m x 153 m) ──
_NODE_COLOR        = [128, 128, 128, 200]
_FAMILY_COLOR      = [255, 200,  50, 255]
_LINK_LINE_COLOR   = [ 80, 140, 220, 200]
_LINK_CURVE_COLOR  = [220,  90,  90, 220]

_NODE_RADIUS_MM    = 300.0
_LINK_THICKNESS_MM = 120.0
_FAMILY_RADIUS_MM  = 1200.0


def _ensure_rerun():
    if rr is None:
        raise RuntimeError(
            "rerun-sdk is not installed.\n"
            "    pip3 install rerun-sdk"
        )


def load_layout_geometry(rail_file: str) -> Tuple[Dict[str, Tuple[float, float]],
                                                  list, list, list, list]:
    """
    Extract the geometry needed for visualisation from a .rail file.

    Returns:
        nodes_xy        — {node_name: (x, y)}
        line_strips     — [[(x1,y1),(x2,y2)], ...] (straight links)
        curve_strips    — [[(x1,y1),(x2,y2)], ...] (curved links)
        fam_positions   — [(x, y), ...]
        fam_labels      — [tool_group, ...]  (same order as fam_positions)
    """
    rm = RouteManager()
    rm.load_from_rail(rail_file)
    net = rm.network
    nodes_xy = {nm: (n.x, n.y) for nm, n in net.nodes.items()}

    line_strips, curve_strips = [], []
    for link in net.links:
        a = nodes_xy.get(link.from_node)
        b = nodes_xy.get(link.to_node)
        if not a or not b:
            continue
        seg = [a, b]
        if link.link_type == 'CURVE':
            curve_strips.append(seg)
        else:
            line_strips.append(seg)

    fam_positions, fam_labels = [], []
    for fam, node_name in sorted(net.eq_to_node.items()):
        p = nodes_xy.get(node_name)
        if p is None:
            continue
        fam_positions.append(p)
        fam_labels.append(fam)

    return nodes_xy, line_strips, curve_strips, fam_positions, fam_labels


def log_layout_entities(rail_file: str) -> Dict[str, Tuple[float, float]]:
    """
    Log the layout entities to Rerun. (Assumes the caller has already called rr.init.)

    Returns:
        nodes_xy dict — reusable for OHT position interpolation in the replay stage.
    """
    _ensure_rerun()
    nodes_xy, line_strips, curve_strips, fam_positions, fam_labels = \
        load_layout_geometry(rail_file)

    # static=True: keep visible while scrubbing the sim_time timeline.
    if line_strips:
        rr.log("layout/links/line",
               rr.LineStrips2D(line_strips, colors=_LINK_LINE_COLOR,
                               radii=_LINK_THICKNESS_MM / 2),
               static=True)
    if curve_strips:
        rr.log("layout/links/curve",
               rr.LineStrips2D(curve_strips, colors=_LINK_CURVE_COLOR,
                               radii=_LINK_THICKNESS_MM / 2),
               static=True)
    rr.log("layout/nodes",
           rr.Points2D(list(nodes_xy.values()),
                       colors=_NODE_COLOR, radii=_NODE_RADIUS_MM),
           static=True)
    rr.log("layout/family",
           rr.Points2D(fam_positions, colors=_FAMILY_COLOR,
                       radii=_FAMILY_RADIUS_MM, labels=fam_labels),
           static=True)

    print(f"[viz.rerun_layout] Layout logged — "
          f"nodes {len(nodes_xy)} / links {len(line_strips) + len(curve_strips)} "
          f"(LINE {len(line_strips)} / CURVE {len(curve_strips)}) / "
          f"family {len(fam_positions)}")
    return nodes_xy


def show_layout(
    rail_file: str,
    *,
    app_id: str = "UFAST_layout",
    spawn: bool = True,
    save_rrd: Optional[str] = None,
) -> None:
    """Rerun init + layout log + (optional) viewer / .rrd save."""
    _ensure_rerun()
    if save_rrd:
        rr.init(app_id, spawn=False)
    else:
        rr.init(app_id, spawn=spawn)
    log_layout_entities(rail_file)
    if save_rrd:
        rr.save(save_rrd)
        print(f"[viz.rerun_layout] .rrd saved: {save_rrd}")


def _main():
    p = argparse.ArgumentParser(description="Static SMAT2022 fab layout in Rerun")
    p.add_argument('--rail',
                   default=os.path.join(DATASET_DIR, 'SMAT2022.rail'))
    p.add_argument('--no-spawn', action='store_true')
    p.add_argument('--save', metavar='PATH')
    a = p.parse_args()
    show_layout(a.rail, spawn=not a.no_spawn, save_rrd=a.save)


if __name__ == '__main__':
    _main()
