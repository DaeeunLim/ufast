"""
smat2022_to_rail.py — SMAT2022 CSV → U-FAST .rail 변환기 (legacy 호환).

SMAT2022 레이아웃 데이터를 U-FAST 가 읽는 .rail 포맷으로 변환.
legacy common/rail_io.py 와 신규 route/rail_parser.py 가 모두 읽을 수 있는
공통 포맷으로 출력한다.

입력 (SMAT2022/):
  Adress.csv     레일 노드 좌표
  Rail.csv       레일 링크 (방향성 FROM→TO)
  Equipment.csv  장비 → tool group, 좌표 (쉼표 소수점)

출력:
  *.rail                  RAILDATA / SCALE / NODE / LINK / RAILLIST /
                          EQTONODEMAP / TEXT
  *_family_node_map.csv   tool group → 대표 노드 매핑 리포트

설계 메모:
  - 노드 이름을 **정수 ID (1..N)** 로 재매핑.
    legacy rail_io.py 는 NODE id 를 int() 로 파싱하므로 필수.
    route.rail_parser 는 string 노드명도 받아들이므로 정수 string 도 호환.
  - LINK / EQTONODEMAP / RAILLIST 모두 정수 ID 사용.
  - RAILLIST 는 (geometry + length) 포함 — legacy 가 Section 객체 생성에 사용.
  - TEXT 는 EQ 마커 좌표 — legacy 시각화용.
  - bridge.build_mapping 의 `int(sec_name)` 요건은 sec_id 가 1..M 이라 자동 충족.
  - EQTONODEMAP 은 granularity (i): tool group 당 대표 노드 1개.
    규칙 (a): 해당 tool group 소속 장비들의 중심 좌표에 가장 가까운 레일 노드.
  - Port.csv 는 EQP_NAME 이 대부분 비어 있어(22120 중 17760 공란) 사용하지 않음.
  - Equipment.csv 좌표와 Adress.csv 좌표는 동일 좌표계임을 확인함.
"""
from __future__ import annotations
import csv
import math
import os
from typing import Dict, List, Optional, Tuple


def _num(s: Optional[str]) -> Optional[float]:
    """쉼표 소수점(2090,688)을 처리하는 float 변환."""
    if s is None or s == '':
        return None
    return float(s.replace(',', '.'))


def _read_tsv(path: str) -> List[dict]:
    """BOM 포함 탭 구분 CSV 읽기."""
    with open(path, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f, delimiter='\t'))


def convert(smat_dir: str, out_rail: str, out_map: Optional[str] = None) -> dict:
    """
    SMAT2022 디렉터리를 .rail 파일(legacy 호환)로 변환한다.

    Returns:
        통계 dict (nodes, links, tool_groups, ...)
    """
    nodes = _read_tsv(os.path.join(smat_dir, 'Adress.csv'))
    rails = _read_tsv(os.path.join(smat_dir, 'Rail.csv'))
    equips = _read_tsv(os.path.join(smat_dir, 'Equipment.csv'))

    # ── 노드: 이름 → 정수 ID(1..N) + 좌표 ──
    node_id_by_name: Dict[str, int] = {}
    node_xy: Dict[str, Tuple[float, float]] = {}
    node_rows: List[Tuple[str, int, float, float]] = []  # (name, id, x, y)
    for i, r in enumerate(nodes, start=1):
        nm = r['NAME']
        x, y = _num(r['POSITION_X']), _num(r['POSITION_Y'])
        node_id_by_name[nm] = i
        node_xy[nm] = (x, y)
        node_rows.append((nm, i, x, y))

    # ── tool group 별 장비 좌표 ──
    group_eq: Dict[str, List[Tuple[float, float]]] = {}
    for r in equips:
        g = r['TOOL_GROUP']
        x, y = _num(r['POSITION_X']), _num(r['POSITION_Y'])
        if x is None or y is None:
            continue
        group_eq.setdefault(g, []).append((x, y))

    # ── tool group 대표 노드 (규칙 a) ──
    name_xy_list = list(node_xy.items())

    def nearest_node(cx: float, cy: float) -> Tuple[str, float]:
        best, bd = None, float('inf')
        for nm, (nx, ny) in name_xy_list:
            d = (nx - cx) ** 2 + (ny - cy) ** 2
            if d < bd:
                bd, best = d, nm
        return best, math.sqrt(bd)

    group_node: Dict[str, str] = {}     # family → 대표 노드 이름(원본)
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

    # ── .rail 작성 ──
    xs = [x for _, _, x, _ in node_rows]
    ys = [y for _, _, _, y in node_rows]
    lines: List[str] = ['RAILDATA']
    lines.append(f"SCALE\t{min(xs)}\t{max(xs)}\t{min(ys)}\t{max(ys)}")

    # NODE — 정수 ID
    for _, nid, x, y in node_rows:
        lines.append(f"NODE\t{nid}\t{x}\t{y}")

    # ── LINK + RAILLIST — junction-to-junction 섹션 병합 ──
    # 섹션 정의: 합류/분기점(진입≠1 또는 진출≠1인 노드)에서 다음 합류/분기점까지의
    # junction-free 구간. 내부 노드는 in=out=1. 같은 섹션의 링크들은 sec_id 를
    # 공유하며, 주행 방향(FROM→TO) 순서로 기록된다 (엔진이 node_list 순서를 따름).
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
        """start_rail 부터 다음 junction 까지 rail 을 이어붙인다."""
        chain = [start_rail]
        visited.add(id(start_rail))
        cur = start_rail['TO_NODE']
        while not _is_junction(cur):
            nxt_list = [x for x in rails_by_from.get(cur, [])
                        if id(x) not in visited]
            if len(nxt_list) != 1:
                break  # 방문 완료(루프 복귀) 또는 데이터 이상
            nxt = nxt_list[0]
            chain.append(nxt)
            visited.add(id(nxt))
            cur = nxt['TO_NODE']
        return chain

    chains: List[List[dict]] = []
    # 1차: junction 에서 출발하는 체인
    for r in valid_rails:
        if id(r) not in visited and _is_junction(r['FROM_NODE']):
            chains.append(_walk_chain(r))
    # 2차: junction 없는 고립 루프 — 임의 지점에서 절단
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
            # RAILLIST: name type from to x1 y1 x2 y2 angle length  (length 는 마지막)
            lines.append(
                f"RAILLIST\t{sec_id}\t{rail_type}\t{fn_id}\t{tn_id}\t"
                f"{x1}\t{y1}\t{x2}\t{y2}\t{angle:.4f}\t{length:.4f}"
            )

    # EQTONODEMAP — 정수 노드 ID
    for g in sorted(group_node):
        rep_name = group_node[g]
        lines.append(f"EQTONODEMAP\t{g}\t{node_id_by_name[rep_name]}")

    # TEXT — EQ 마커 좌표 (legacy 시각화)
    for g in sorted(group_node):
        rep_name = group_node[g]
        x, y = node_xy[rep_name]
        lines.append(f"TEXT\t{g}\tEQ\t{x}\t{y}")

    with open(out_rail, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    # ── 매핑 리포트 ──
    if out_map and map_report:
        with open(out_map, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(map_report[0].keys()))
            w.writeheader()
            w.writerows(map_report)

    # ── 통계 ──
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
    print("[smat2022_to_rail] 변환 완료 (legacy 호환 포맷)")
    print(f"  노드 {res['nodes']} / 링크 {res['links']}"
          + (f" (skipped {res['skipped_links']})" if res['skipped_links'] else "")
          + f" / tool group {res['tool_groups']}")
    print(f"  대표노드 거리 mm: min {res['dist_min']} / avg {res['dist_avg']} / max {res['dist_max']}")
    print(f"  → {res['out_rail']}")
    print(f"  → {res['out_map']}")
