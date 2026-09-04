import math
from typing import List, Dict, Any, Tuple
from ufast.drawing.geometry import CLayer, CLine, CQuadCurve, CText


class DXFParser:
    def __init__(self):
        self.layers: Dict[str, CLayer] = {}
        self.blocks: Dict[str, List[Dict[str, Any]]] = {}
        self.current_section = None
        self.current_block_name = None

    def parse(self, filepath: str) -> List[CLayer]:
        self.layers = {}
        self.blocks = {}
        self.current_section = None
        self.current_block_name = None

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [line.strip() for line in f.readlines()]
        except Exception as e:
            print(f"Error reading file: {e}")
            return []

        # Convert to a list of group code/value pairs
        pairs = []
        i = 0
        while i < len(lines) - 1:
            code = lines[i].strip()
            value = lines[i + 1].strip()
            pairs.append((code, value))
            i += 2

        # Iterate pair by pair
        idx = 0
        while idx < len(pairs):
            code, value = pairs[idx]

            if code == '0':
                if value == 'SECTION':
                    if idx + 1 < len(pairs) and pairs[idx + 1][0] == '2':
                        self.current_section = pairs[idx + 1][1]
                        idx += 2
                        continue
                elif value == 'ENDSEC':
                    self.current_section = None
                    idx += 1
                    continue
                elif value == 'BLOCK':
                    data, idx = self._read_entity(pairs, idx)
                    self.current_block_name = data.get('name')
                    if self.current_block_name:
                        self.blocks[self.current_block_name] = []
                    continue
                elif value == 'ENDBLK':
                    self.current_block_name = None
                    idx += 1
                    continue
                elif value == 'EOF':
                    break
                else:
                    data, idx = self._read_entity(pairs, idx)

                    if self.current_section == 'BLOCKS' and self.current_block_name:
                        self.blocks[self.current_block_name].append(data)
                    elif self.current_section == 'ENTITIES':
                        self._process_entity_data(data)
                    continue
            idx += 1

        return list(self.layers.values())

    def _read_entity(self, pairs: List[Tuple[str, str]], start: int) -> Tuple[Dict[str, Any], int]:
        entity_type = pairs[start][1]
        data = {'type': entity_type}
        polyline_points = []

        idx = start + 1
        while idx < len(pairs):
            code, value = pairs[idx]

            if code == '0':
                break

            if code == '1':
                data['text'] = value
            elif code == '8':
                data['layer'] = value
            elif code == '2':
                data['name'] = value
            elif code == '7':
                data['text_style'] = value
            elif code == '10':
                if entity_type == 'LWPOLYLINE':
                    polyline_points.append({'x': float(value), 'y': 0.0})
                else:
                    data['x1'] = float(value)
            elif code == '20':
                if entity_type == 'LWPOLYLINE' and polyline_points:
                    polyline_points[-1]['y'] = float(value)
                else:
                    data['y1'] = float(value)
            elif code == '11':
                data['x2'] = float(value)
            elif code == '21':
                data['y2'] = float(value)
            elif code == '40':
                data['radius'] = float(value)
            elif code == '41':
                data['x_scale'] = float(value)
            elif code == '42':
                data['y_scale'] = float(value)
            elif code == '50':
                data['start_angle'] = float(value)
            elif code == '51':
                data['end_angle'] = float(value)

            idx += 1

        if polyline_points:
            data['points'] = polyline_points

        return data, idx

    def _process_entity_data(self, data: Dict[str, Any], transform: Dict[str, float] = None):
        entity_type = data.get('type')
        layer_name = data.get('layer', '0')

        if layer_name not in self.layers:
            self.layers[layer_name] = CLayer(layer_name=layer_name)
        layer = self.layers[layer_name]

        def tx(x, y):
            if not transform:
                return x, y
            sx = transform.get('x_scale', 1.0)
            sy = transform.get('y_scale', 1.0)
            rad = math.radians(transform.get('rotation', 0))
            nx = (x * sx) * math.cos(rad) - (y * sy) * math.sin(rad) + transform.get('x', 0)
            ny = (x * sx) * math.sin(rad) + (y * sy) * math.cos(rad) + transform.get('y', 0)
            return nx, ny

        if entity_type == 'LINE':
            x1, y1 = tx(data.get('x1', 0), data.get('y1', 0))
            x2, y2 = tx(data.get('x2', 0), data.get('y2', 0))
            layer.shape_list.append(
                CLine(name="", type_str="LINE", x1=x1, y1=y1, x2=x2, y2=y2, color=None))

        elif entity_type == 'LWPOLYLINE':
            points = data.get('points', [])
            for k in range(len(points) - 1):
                x1, y1 = tx(points[k]['x'], points[k]['y'])
                x2, y2 = tx(points[k + 1]['x'], points[k + 1]['y'])
                layer.shape_list.append(
                    CLine(name="", type_str="LINE", x1=x1, y1=y1, x2=x2, y2=y2, color=None))

        elif entity_type == 'ARC':
            self._process_arc(data, layer, transform)

        elif entity_type == 'CIRCLE':
            self._process_circle(data, layer, transform)

        elif entity_type in ('TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'):
            text_content = data.get('text', '').strip()
            if text_content:
                x, y = tx(data.get('x1', 0), data.get('y1', 0))
                layer.shape_list.append(
                    CText(text=text_content, type_str="EQ", x=x, y=y, color=None))

        elif entity_type == 'INSERT':
            block_name = data.get('name')
            if block_name in self.blocks:
                new_transform = {
                    'x': data.get('x1', 0), 'y': data.get('y1', 0),
                    'x_scale': data.get('x_scale', 1.0),
                    'y_scale': data.get('y_scale', 1.0),
                    'rotation': data.get('angle', data.get('start_angle', 0))
                }
                for block_entity in self.blocks[block_name]:
                    self._process_entity_data(block_entity, new_transform)

    def _process_arc(self, data: Dict[str, Any], layer: CLayer, transform=None):
        """Convert an ARC entity to CQuadCurve or CLine (pattern from the Java original)"""
        cx = data.get('x1', 0)
        cy = data.get('y1', 0)
        radius = data.get('radius', 0)
        start_deg = data.get('start_angle', 0)
        end_deg = data.get('end_angle', 0)

        def tx(x, y):
            if not transform:
                return x, y
            sx = transform.get('x_scale', 1.0)
            sy = transform.get('y_scale', 1.0)
            rad = math.radians(transform.get('rotation', 0))
            nx = (x * sx) * math.cos(rad) - (y * sy) * math.sin(rad) + transform.get('x', 0)
            ny = (x * sx) * math.sin(rad) + (y * sy) * math.cos(rad) + transform.get('y', 0)
            return nx, ny

        # Compute the angular span of the arc
        if start_deg < end_deg:
            dtheta = end_deg - start_deg
        else:
            dtheta = end_deg + 360 - start_deg

        if dtheta <= 100:
            # Convert to QuadCurve
            start_rad = math.radians(start_deg)
            end_rad = math.radians(end_deg)

            from_x = cx + radius * math.cos(start_rad)
            from_y = cy + radius * math.sin(start_rad)
            to_x = cx + radius * math.cos(end_rad)
            to_y = cy + radius * math.sin(end_rad)

            # Control point: intersection of the tangents at the arc midpoint
            mid_angle = start_rad + math.radians(dtheta / 2)
            # Actual coordinates of the arc midpoint
            mid_x = cx + radius * math.cos(mid_angle)
            mid_y = cy + radius * math.sin(mid_angle)

            # Control point = compute the tangent intersection
            # The point where the tangents at the start and end meet
            # Simplification: push the arc midpoint outward
            # To approximate an arc with a quadratic bezier: ctrl = 2*mid - 0.5*(start + end)
            ctrl_x = 2 * mid_x - 0.5 * (from_x + to_x)
            ctrl_y = 2 * mid_y - 0.5 * (from_y + to_y)

            from_x, from_y = tx(from_x, from_y)
            to_x, to_y = tx(to_x, to_y)
            ctrl_x, ctrl_y = tx(ctrl_x, ctrl_y)

            curve = CQuadCurve(
                name="", type_str="CURVE",
                x1=from_x, y1=from_y,
                cx=ctrl_x, cy=ctrl_y,
                x2=to_x, y2=to_y, color=None)
            curve.center_x, curve.center_y = tx(cx, cy)
            curve.angle = dtheta
            layer.shape_list.append(curve)
        else:
            # Large arc: split into line segments (10-degree steps)
            num_segments = max(2, int(dtheta / 10))
            step = dtheta / num_segments

            for i in range(num_segments):
                a1 = math.radians(start_deg + step * i)
                a2 = math.radians(start_deg + step * (i + 1))

                x1 = cx + radius * math.cos(a1)
                y1 = cy + radius * math.sin(a1)
                x2 = cx + radius * math.cos(a2)
                y2 = cy + radius * math.sin(a2)

                x1, y1 = tx(x1, y1)
                x2, y2 = tx(x2, y2)

                layer.shape_list.append(
                    CLine(name="", type_str="LINE", x1=x1, y1=y1, x2=x2, y2=y2, color=None))

    def _process_circle(self, data: Dict[str, Any], layer: CLayer, transform=None):
        """Split a CIRCLE entity into line segments"""
        cx = data.get('x1', 0)
        cy = data.get('y1', 0)
        radius = data.get('radius', 0)

        def tx(x, y):
            if not transform:
                return x, y
            sx = transform.get('x_scale', 1.0)
            sy = transform.get('y_scale', 1.0)
            rad = math.radians(transform.get('rotation', 0))
            nx = (x * sx) * math.cos(rad) - (y * sy) * math.sin(rad) + transform.get('x', 0)
            ny = (x * sx) * math.sin(rad) + (y * sy) * math.cos(rad) + transform.get('y', 0)
            return nx, ny

        # Approximate the circle with 36 segments (10-degree steps)
        num_segments = 36
        for i in range(num_segments):
            a1 = math.radians(i * 360 / num_segments)
            a2 = math.radians((i + 1) * 360 / num_segments)

            x1, y1 = tx(cx + radius * math.cos(a1), cy + radius * math.sin(a1))
            x2, y2 = tx(cx + radius * math.cos(a2), cy + radius * math.sin(a2))

            layer.shape_list.append(
                CLine(name="", type_str="LINE", x1=x1, y1=y1, x2=x2, y2=y2, color=None))
