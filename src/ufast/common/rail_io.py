"""
Rail file read/write module
Compatible with the tab-separated text format of the Java original

File parsing is handled by common/rail_format.py (the common parser);
this module converts its result (RailData) into CLayer for visualization
and into the Section/EQ structures of SimulatorDataSet.
"""
from typing import Dict, List, Tuple
from ufast.drawing.geometry import CLayer, CLine, CQuadCurve, CText
from ufast.core.components import Section, EQ
from ufast.core.data_set import SimulatorDataSet
from ufast.common.rail_format import parse_rail_file


def load_rail_file(filepath: str) -> List[CLayer]:
    """
    Read a .rail file and return a list of CLayers (for visualization).
    At the same time, build Section/EQ/link information directly in SimulatorDataSet.
    """
    ds = SimulatorDataSet.get_instance()
    ds.sections.clear()
    ds.section_id_to_index.clear()
    ds.eq_list.clear()

    # ── Step 1: parse the file with the common parser ──────────────────────
    # require_header=False: lenient so .rail files saved by the old GUI without a header still open.
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
    # Control-point info for CURVE sections: sec_id → (x1,y1,cx,cy,x2,y2,center_x,center_y,angle)
    curve_figures: Dict[int, tuple] = {
        int(c.name): (c.x1, c.y1, c.cx, c.cy, c.x2, c.y2,
                      c.center_x, c.center_y, c.angle)
        for c in data.curves
    }

    # Build visualization layers
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

    # ── Step 2: map node → which section's from/to ────────
    # Per RAILLIST: from_node is the section start, to_node the end
    node_to_section: Dict[int, int] = {}   # node_id → sec_id (by to_node)
    from_node_to_section: Dict[int, int] = {}  # node_id → sec_id (by from_node)

    for r in rail_list:
        node_to_section[r['to_node']] = r['sec_id']
        from_node_to_section[r['from_node']] = r['sec_id']

    # ── Step 3: create Section objects ───────────────────────────
    for r in rail_list:
        sec_id = r['sec_id']
        length = r['length']
        if length <= 0:
            length = 1.0

        section = Section(sec_id)
        section.length = length
        section.add_buffer(length, f"N_{r['from_node']}", f"N_{r['to_node']}")

        # Visualization figure: CQuadCurve for CURVE, CLine for LINE
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

    # ── Step 4: link sections (next_sections / prev_sections) ───
    # Section A's to_node == section B's from_node → A→B
    to_node_of: Dict[int, int] = {}    # sec_id → to_node
    from_node_of: Dict[int, int] = {}  # sec_id → from_node

    for r in rail_list:
        to_node_of[r['sec_id']] = r['to_node']
        from_node_of[r['sec_id']] = r['from_node']

    # Reverse map: from_node value → section id
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
        # Other sections whose from_node equals this section's to_node are next
        next_secs = from_node_sec_map.get(to_nd, [])
        for next_sid in next_secs:
            if next_sid != sid:
                sec.next_sections.append(next_sid)
                next_idx = ds.section_id_to_index.get(next_sid)
                if next_idx is not None:
                    ds.sections[next_idx].prev_sections.append(sid)

    # ── Step 5: create EQs and link them to sections ──────────────────────
    # EQTONODEMAP: eq_name → node_id
    # Find which section has node_id as to_node or from_node and link it
    for eq_name, node_id in eq_to_node.items():
        eq = EQ(eq_name)

        # Look up coordinates from TEXT
        for (tname, tx, ty) in texts:
            if tname == eq_name:
                eq.left = tx
                eq.top = ty
                break

        # Find the section containing node_id (to_node first, else from_node)
        sec_id = node_to_section.get(node_id)
        if sec_id is None:
            # Also check by from_node
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
    Save the shapes of the Rail layer to a .rail file.
    (For saving DXF → Rail conversion results - keeps the existing format)
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

        # SCALE record
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
