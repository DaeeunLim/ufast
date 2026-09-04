"""
Rail 파일 읽기/쓰기 모듈
Java 원본의 탭 구분 텍스트 형식과 호환

파일 파싱은 common/rail_format.py(공통 파서)가 담당하고,
이 모듈은 그 결과(RailData)를 시각화용 CLayer와
SimulatorDataSet의 Section/EQ 구조로 변환한다.
"""
from typing import Dict, List, Tuple
from ufast.drawing.geometry import CLayer, CLine, CQuadCurve, CText
from ufast.core.components import Section, EQ
from ufast.core.data_set import SimulatorDataSet
from ufast.common.rail_format import parse_rail_file


def load_rail_file(filepath: str) -> List[CLayer]:
    """
    .rail 파일을 읽어 CLayer 리스트(시각화용)를 반환한다.
    동시에 SimulatorDataSet에 Section/EQ/연결 정보를 직접 구축한다.
    """
    ds = SimulatorDataSet.get_instance()
    ds.sections.clear()
    ds.section_id_to_index.clear()
    ds.eq_list.clear()

    # ── 1단계: 공통 파서로 파일 파싱 ──────────────────────
    # require_header=False: 과거 GUI가 헤더 없이 저장한 .rail도 열 수 있게 관대하게.
    data = parse_rail_file(filepath, require_header=False)

    nodes: Dict[int, Tuple[float, float]] = {
        int(n.name): (n.x, n.y) for n in data.nodes
    }
    rail_list: List[dict] = [{
        'sec_id': int(r.name),
        'type': r.rail_type,
        'from_node': int(r.first_node),
        'to_node': int(r.second_node),
        'length': r.length,
    } for r in data.rail_list]
    node_to_eq: Dict[int, str] = {
        int(k): v for k, v in data.node_to_eq_list.items()
    }
    eq_to_node: Dict[str, int] = {
        k: int(v) for k, v in data.eq_to_node.items()
    }
    texts: List[Tuple[str, float, float]] = [
        (t.name, t.x, t.y) for t in data.texts
    ]
    # CURVE 섹션의 제어점 정보: sec_id → (x1,y1,cx,cy,x2,y2,center_x,center_y,angle)
    curve_figures: Dict[int, tuple] = {
        int(c.name): (c.x1, c.y1, c.cx, c.cy, c.x2, c.y2,
                      c.center_x, c.center_y, c.angle)
        for c in data.curves
    }

    # 시각화 레이어 구성
    vis_layer = CLayer(layer_name="Rail", is_rail_layer=True, is_turned_on=True)
    for t in data.texts:
        vis_layer.shape_list.append(
            CText(text=t.name, type_str="EQ", x=t.x, y=t.y, color=None))
    for l in data.lines:
        if l.type_str == 'ARROW':
            continue
        vis_layer.shape_list.append(
            CLine(name=l.name, type_str=l.type_str,
                  x1=l.x1, y1=l.y1, x2=l.x2, y2=l.y2, color=None))
    for c in data.curves:
        curve = CQuadCurve(
            name=c.name, type_str=c.type_str,
            x1=c.x1, y1=c.y1, cx=c.cx, cy=c.cy, x2=c.x2, y2=c.y2, color=None)
        curve.center_x = c.center_x
        curve.center_y = c.center_y
        curve.angle = c.angle
        vis_layer.shape_list.append(curve)

    # ── 2단계: node → 어떤 섹션의 from/to인지 매핑 ────────
    # RAILLIST 기준: from_node가 섹션의 시작, to_node가 끝
    node_to_section: Dict[int, int] = {}   # node_id → sec_id (to_node 기준)
    from_node_to_section: Dict[int, int] = {}  # node_id → sec_id (from_node 기준)

    for r in rail_list:
        node_to_section[r['to_node']] = r['sec_id']
        from_node_to_section[r['from_node']] = r['sec_id']

    # ── 3단계: Section 객체 생성 ───────────────────────────
    for r in rail_list:
        sec_id = r['sec_id']
        length = r['length']
        if length <= 0:
            length = 1.0

        section = Section(sec_id)
        section.length = length
        section.add_buffer(length, f"N_{r['from_node']}", f"N_{r['to_node']}")

        # 시각화 figure: CURVE면 CQuadCurve, LINE이면 CLine
        fn = nodes.get(r['from_node'])
        tn = nodes.get(r['to_node'])
        if fn and tn:
            if r['type'] == 'CURVE' and sec_id in curve_figures:
                x1, y1, cx, cy, x2, y2, ctr_x, ctr_y, angle = curve_figures[sec_id]
                fig = CQuadCurve(name=str(sec_id), type_str='CURVE',
                                 x1=x1, y1=y1, cx=cx, cy=cy,
                                 x2=x2, y2=y2, color=None)
                fig.center_x = ctr_x
                fig.center_y = ctr_y
                fig.angle = angle
            else:
                fig = CLine(name=str(sec_id), type_str=r['type'],
                            x1=fn[0], y1=fn[1], x2=tn[0], y2=tn[1], color=None)
            section.figures = [fig]

        ds.sections.append(section)
        ds.section_id_to_index[sec_id] = len(ds.sections) - 1

    # ── 4단계: 섹션 연결 (next_sections / prev_sections) ───
    # 섹션 A의 to_node == 섹션 B의 from_node → A→B
    to_node_of: Dict[int, int] = {}    # sec_id → to_node
    from_node_of: Dict[int, int] = {}  # sec_id → from_node

    for r in rail_list:
        to_node_of[r['sec_id']] = r['to_node']
        from_node_of[r['sec_id']] = r['from_node']

    # from_node 값 → 해당 섹션 id 역매핑
    from_node_sec_map: Dict[int, List[int]] = {}  # from_node → [sec_id, ...]
    for r in rail_list:
        fn = r['from_node']
        if fn not in from_node_sec_map:
            from_node_sec_map[fn] = []
        from_node_sec_map[fn].append(r['sec_id'])

    for sec in ds.sections:
        sid = sec.section_id
        to_nd = to_node_of.get(sid)
        if to_nd is None:
            continue
        # 이 섹션의 to_node를 from_node로 가진 다른 섹션들이 next
        next_secs = from_node_sec_map.get(to_nd, [])
        for next_sid in next_secs:
            if next_sid != sid:
                sec.next_sections.append(next_sid)
                next_idx = ds.section_id_to_index.get(next_sid)
                if next_idx is not None:
                    ds.sections[next_idx].prev_sections.append(sid)

    # ── 5단계: EQ 생성 및 섹션 연결 ──────────────────────
    # EQTONODEMAP: eq_name → node_id
    # node_id가 어느 섹션의 to_node 또는 from_node인지 찾아 연결
    for eq_name, node_id in eq_to_node.items():
        eq = EQ(eq_name)

        # TEXT에서 좌표 찾기
        for (tname, tx, ty) in texts:
            if tname == eq_name:
                eq.left = tx
                eq.top = ty
                break

        # node_id가 속한 섹션 찾기 (to_node 우선, 없으면 from_node)
        sec_id = node_to_section.get(node_id)
        if sec_id is None:
            # from_node로도 확인
            for r in rail_list:
                if r['from_node'] == node_id:
                    sec_id = r['sec_id']
                    break

        if sec_id is not None:
            idx = ds.section_id_to_index.get(sec_id)
            if idx is not None:
                section = ds.sections[idx]
                eq.section_id = sec_id
                section.eq_list.append(eq)

        ds.eq_list[eq_name] = eq

    print(f"[RailIO] Sections: {len(ds.sections)}, EQs: {len(ds.eq_list)}, "
          f"Nodes: {len(nodes)}")

    return [vis_layer]


def save_rail_file(filepath: str, layers: List[CLayer]):
    """
    Rail 레이어의 도형들을 .rail 파일로 저장한다.
    (DXF → Rail 변환 결과 저장용 - 기존 형식 유지)
    """
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('RAILDATA\n')
        for layer in layers:
            if not layer.is_rail_layer:
                continue
            for fig in layer.shape_list:
                if isinstance(fig, CQuadCurve):
                    f.write('\t'.join([
                        'CURVE', fig.name, fig.type,
                        str(fig.start_x), str(fig.start_y),
                        str(fig.ctrl_x), str(fig.ctrl_y),
                        str(fig.end_x), str(fig.end_y),
                        str(fig.center_x), str(fig.center_y),
                        str(fig.angle),
                        '128', '128', '128'
                    ]) + '\n')
                elif isinstance(fig, CLine):
                    f.write('\t'.join([
                        'LINE', fig.name, fig.type,
                        str(fig.start_x), str(fig.start_y),
                        str(fig.end_x), str(fig.end_y),
                        '128', '128', '128'
                    ]) + '\n')

        # SCALE 레코드
        min_x, min_y = float('inf'), float('inf')
        max_x, max_y = float('-inf'), float('-inf')
        for layer in layers:
            if not layer.is_rail_layer:
                continue
            for fig in layer.shape_list:
                for attr in ['start_x', 'end_x']:
                    v = getattr(fig, attr, None)
                    if v is not None:
                        min_x = min(min_x, v)
                        max_x = max(max_x, v)
                for attr in ['start_y', 'end_y']:
                    v = getattr(fig, attr, None)
                    if v is not None:
                        min_y = min(min_y, v)
                        max_y = max(max_y, v)
                if isinstance(fig, CQuadCurve):
                    min_x = min(min_x, fig.ctrl_x)
                    max_x = max(max_x, fig.ctrl_x)
                    min_y = min(min_y, fig.ctrl_y)
                    max_y = max(max_y, fig.ctrl_y)

        if min_x < max_x:
            f.write('\t'.join([
                'SCALE', str(min_x), str(max_x), str(min_y), str(max_y)
            ]) + '\n')
