"""
viz/rerun_layout.py — Rerun 으로 SMAT2022 fab 정적 레이아웃 시각화.

단독 실행:
    python3 -m ufast.viz.rerun_layout
    python3 -m ufast.viz.rerun_layout --rail dataset/SMAT2022.rail
    python3 -m ufast.viz.rerun_layout --no-spawn --save layout.rrd

라이브러리 (Phase 2 replay 에서 재사용):
    from ufast.viz.rerun_layout import log_layout_entities, show_layout
    show_layout('dataset/SMAT2022.rail')           # init + 로그 + (옵션) viewer
    log_layout_entities('dataset/SMAT2022.rail')   # init 가정, 로그만
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


# ── 색상 / 크기 (좌표 mm, fab ~ 300m × 153m) ────────────────
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
            "rerun-sdk 가 설치돼 있지 않습니다.\n"
            "    pip3 install rerun-sdk"
        )


def load_layout_geometry(rail_file: str) -> Tuple[Dict[str, Tuple[float, float]],
                                                  list, list, list, list]:
    """
    .rail 파일에서 시각화에 필요한 기하 정보를 추출한다.

    Returns:
        nodes_xy        — {node_name: (x, y)}
        line_strips     — [[(x1,y1),(x2,y2)], ...] (직선 링크)
        curve_strips    — [[(x1,y1),(x2,y2)], ...] (곡선 링크)
        fam_positions   — [(x, y), ...]
        fam_labels      — [tool_group, ...]  (fam_positions 와 동일 순서)
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
    Layout entities 를 Rerun 에 로그한다. (rr.init 은 호출자가 이미 했다고 가정)

    Returns:
        nodes_xy 사전 — replay 단계에서 OHT 위치 보간에 재사용 가능.
    """
    _ensure_rerun()
    nodes_xy, line_strips, curve_strips, fam_positions, fam_labels = \
        load_layout_geometry(rail_file)

    # static=True: sim_time 타임라인 스크럽해도 항상 보이도록 함.
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

    print(f"[viz.rerun_layout] 레이아웃 로그 완료 — "
          f"노드 {len(nodes_xy)} / 링크 {len(line_strips) + len(curve_strips)} "
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
    """Rerun init + 레이아웃 로그 + (옵션) viewer / .rrd 저장."""
    _ensure_rerun()
    if save_rrd:
        rr.init(app_id, spawn=False)
    else:
        rr.init(app_id, spawn=spawn)
    log_layout_entities(rail_file)
    if save_rrd:
        rr.save(save_rrd)
        print(f"[viz.rerun_layout] .rrd 저장: {save_rrd}")


def _main():
    p = argparse.ArgumentParser(description="Rerun 으로 SMAT2022 fab 정적 레이아웃")
    p.add_argument('--rail',
                   default=os.path.join(DATASET_DIR, 'SMAT2022.rail'))
    p.add_argument('--no-spawn', action='store_true')
    p.add_argument('--save', metavar='PATH')
    a = p.parse_args()
    show_layout(a.rail, spawn=not a.no_spawn, save_rrd=a.save)


if __name__ == '__main__':
    _main()
