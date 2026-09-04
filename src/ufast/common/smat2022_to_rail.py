"""
smat2022_to_rail.py — SMAT2022 CSV → U-FAST .rail converter (legacy-compatible).

Converts SMAT2022 layout data into the .rail format read by U-FAST.
Outputs a common format readable by both the legacy common/rail_io.py
and the new route/rail_parser.py.

Input (SMAT2022/):
  Adress.csv     rail node coordinates
  Rail.csv       rail links (directed FROM→TO)
  Equipment.csv  equipment → tool group, coordinates (comma decimal separator)

Output:
  *.rail                  RAILDATA / SCALE / NODE / LINK / RAILLIST /
                          EQTONODEMAP / TEXT
  *_family_node_map.csv   tool group → representative node mapping report

Design notes:
  - Node names are remapped to **integer IDs (1..N)**.
    Required because legacy rail_io.py parses NODE ids with int().
    route.rail_parser also accepts string node names, so integer strings are compatible.
  - LINK / EQTONODEMAP / RAILLIST all use integer IDs.
  - RAILLIST includes (geometry + length) — used by legacy code to create Section objects.
  - TEXT holds EQ marker coordinates — for legacy visualization.
  - The `int(sec_name)` requirement of bridge.build_mapping is met automatically since sec_id is 1..M.
  - EQTONODEMAP uses granularity (i): one representative node per tool group.
    Rule (a): the rail node closest to the centroid of the equipment in that tool group.
  - Port.csv is not used because EQP_NAME is mostly empty (17760 of 22120 blank).
  - Equipment.csv and Adress.csv coordinates were confirmed to share the same coordinate system.
"""
from __future__ import annotations
import csv
import math
import os
from typing import Dict, List, Optional, Tuple


def _num(s: Optional[str]) -> Optional[float]:
    """float conversion that handles comma decimal separators (2090,688)."""
    if s is None or s == '':
        return None
    return float(s.replace(',', '.'))


def _read_tsv(path: str) -> List[dict]:
    """Read a tab-separated CSV that may include a BOM."""
    with open(path, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f, delimiter='\t'))


def convert(smat_dir: str, out_rail: str, out_map: Optional[str] = None) -> dict:
    """
    Convert a SMAT2022 directory into a .rail file (legacy-compatible).

    Returns:
        statistics dict (nodes, links, tool_groups, ...)
    """
    nodes = _read_tsv(os.path.join(smat_dir, 'Adress.csv'))
    rails = _read_tsv(os.path.join(smat_dir, 'Rail.csv'))
    equips = _read_tsv(os.path.join(smat_dir, 'Equipment.csv'))

    # ── Nodes: name → integer ID (1..N) + coordinates ──
    node_id_by_name: Dict[str, int] = {}
    node_xy: Dict[str, Tuple[float, float]] = {}
    node_rows: List[Tuple[str, int, float, float]] = []  # (name, id, x, y)
    for i, r in enumerate(nodes, start=1):
        nm = r['NAME']
        x, y = _num(r['POSITION_X']), _num(r['POSITION_Y'])
        node_id_by_name[nm] = i
        node_xy[nm] = (x, y)
        node_rows.append((nm, i, x, y))

    # ── Equipment coordinates per tool group ──
    group_eq: Dict[str, List[Tuple[float, float]]] = {}
    for r in equips:
        g = r['TOOL_GROUP']
        x, y = _num(r['POSITION_X']), _num(r['POSITION_Y'])
        if x is None or y is None:
            continue
        group_eq.setdefault(g, []).append((x, y))

    # ── Representative node per tool group (rule a) ──
    name_xy_list = list(node_xy.items())

    def nearest_node(cx: float, cy: float) -> Tuple[str, float]:
        best, bd = None, float('inf')
        for nm, (nx, ny) in name_xy_list:
            d = (nx - cx) ** 2 + (ny - cy) ** 2
            if d < bd:
                bd, best = d, nm
        return best, math.sqrt(bd)

    group_node: Dict[str, str] = {}     # family → representative node name (original)
    map_report: List[dict] = []
    for g in sorted(group_eq):
        pts = group_eq[g]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        rn, dist = nearest_node(cx, cy)
        group_node[g] = rn
        rx, ry = node_xy[rn]
        map_report.append(dict(
            tool_group=g, n_eq=len(pts),
            centroid_x=round(cx, 2), centroid_y=round(cy, 2),
            rep_node_name=rn,
            rep_node_id=node_id_by_name[rn],
            rep_x=rx, rep_y=ry,
            dist=round(dist, 1),
        ))

    # ── Write .rail ──
    xs = [x for _, _, x, _ in node_rows]
    ys = [y for _, _, _, y in node_rows]
    lines: List[str] = ['RAILDATA']
    lines.append(f"SCALE\t{min(xs)}\t{max(xs)}\t{min(ys)}\t{max(ys)}")

    # NODE — integer IDs
    for _, nid, x, y in node_rows:
        lines.append(f"NODE\t{nid}\t{x}\t{y}")

    # ── LINK + RAILLIST — junction-to-junction section merging ──
    # Section definition: the junction-free stretch from a merge/branch point (a node with
    # in-degree≠1 or out-degree≠1) to the next one. Interior nodes have in=out=1. Links of
    # the same section share a sec_id and are written in travel order (FROM→TO), since the
    # engine follows the node_list order.
    skipped = 0
    valid_rails = []
    for r in rails:
        if r['FROM_NODE'] in node_id_by_name and r['TO_NODE'] in node_id_by_name:
            valid_rails.append(r)
        else:
            skipped += 1

    in_deg: Dict[str, int] = {}
    out_deg: Dict[str, int] = {}
    rails_by_from: Dict[str, List[dict]] = {}
    for r in valid_rails:
        out_deg[r['FROM_NODE']] = out_deg.get(r['FROM_NODE'], 0) + 1
        in_deg[r['TO_NODE']] = in_deg.get(r['TO_NODE'], 0) + 1
        rails_by_from.setdefault(r['FROM_NODE'], []).append(r)

    def _is_junction(n: str) -> bool:
        return in_deg.get(n, 0) != 1 or out_deg.get(n, 0) != 1

    visited: set = set()

    def _walk_chain(start_rail: dict) -> List[dict]:
        """Chain rails from start_rail up to the next junction."""
        chain = [start_rail]
        visited.add(id(start_rail))
        cur = start_rail['TO_NODE']
        while not _is_junction(cur):
            nxt_list = [x for x in rails_by_from.get(cur, [])
                        if id(x) not in visited]
            if len(nxt_list) != 1:
                break  # already visited (loop closed) or data anomaly
            nxt = nxt_list[0]
            chain.append(nxt)
            visited.add(id(nxt))
            cur = nxt['TO_NODE']
        return chain

    chains: List[List[dict]] = []
    # Pass 1: chains starting at a junction
    for r in valid_rails:
        if id(r) not in visited and _is_junction(r['FROM_NODE']):
            chains.append(_walk_chain(r))
    # Pass 2: isolated loops without a junction — cut at an arbitrary point
    for r in valid_rails:
        if id(r) not in visited:
            chains.append(_walk_chain(r))

    for sec_id, chain in enumerate(chains, start=1):
        for r in chain:
            rail_type = 'CURVE' if str(r['CURVE']).strip() == '1' else 'LINE'
            fn_name, tn_name = r['FROM_NODE'], r['TO_NODE']
            fn_id = node_id_by_name[fn_name]
            tn_id = node_id_by_name[tn_name]
            x1, y1 = node_xy[fn_name]
            x2, y2 = node_xy[tn_name]
            length = math.hypot(x2 - x1, y2 - y1)
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))

            lines.append(f"LINK\t{sec_id}\t{rail_type}\t{fn_id}\t{tn_id}")
            # RAILLIST: name type from to x1 y1 x2 y2 angle length  (length is last)
            lines.append(
                f"RAILLIST\t{sec_id}\t{rail_type}\t{fn_id}\t{tn_id}\t"
                f"{x1}\t{y1}\t{x2}\t{y2}\t{angle:.4f}\t{length:.4f}"
            )

    # EQTONODEMAP — integer node IDs
    for g in sorted(group_node):
        rep_name = group_node[g]
        lines.append(f"EQTONODEMAP\t{g}\t{node_id_by_name[rep_name]}")

    # TEXT — EQ marker coordinates (legacy visualization)
    for g in sorted(group_node):
        rep_name = group_node[g]
        x, y = node_xy[rep_name]
        lines.append(f"TEXT\t{g}\tEQ\t{x}\t{y}")

    with open(out_rail, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    # ── Mapping report ──
    if out_map and map_report:
        with open(out_map, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(map_report[0].keys()))
            w.writeheader()
            w.writerows(map_report)

    # ── Statistics ──
    dists = [m['dist'] for m in map_report]
    return dict(
        nodes=len(node_rows),
        links=len(rails) - skipped,
        sections=len(chains),
        skipped_links=skipped,
        tool_groups=len(group_node),
        out_rail=out_rail, out_map=out_map,
        dist_min=round(min(dists), 1) if dists else 0,
        dist_max=round(max(dists), 1) if dists else 0,
        dist_avg=round(sum(dists) / len(dists), 1) if dists else 0,
    )


if __name__ == '__main__':
    import sys
    from ufast.paths import DATASET_DIR
    smat = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATASET_DIR, 'SMAT2022')
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DATASET_DIR, 'SMAT2022.rail')
    mp = os.path.splitext(out)[0] + '_family_node_map.csv'
    res = convert(smat, out, mp)
    print("[smat2022_to_rail] Conversion complete (legacy-compatible format)")
    print(f"  nodes {res['nodes']} / links {res['links']}"
          + (f" (skipped {res['skipped_links']})" if res['skipped_links'] else "")
          + f" / tool group {res['tool_groups']}")
    print(f"  representative-node distance mm: min {res['dist_min']} / avg {res['dist_avg']} / max {res['dist_max']}")
    print(f"  → {res['out_rail']}")
    print(f"  → {res['out_map']}")
