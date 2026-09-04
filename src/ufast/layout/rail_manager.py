import math
from typing import List, Dict, Tuple
from ufast.drawing.geometry import CLayer, CLine, CText
from ufast.core.components import Section, EQ
from ufast.core.data_set import SimulatorDataSet

class RailManager:
    """
    CAD/Drawing 데이터(CLayer)를 시뮬레이션 데이터(Section, Node)로 변환하는 클래스
    """
    def __init__(self):
        self.data_set = SimulatorDataSet.get_instance()

    def build_layout(self, layers: List[CLayer], merge_lines: bool = True):
        # 1) Collect lines first
        all_lines = []
        for layer in layers:
            if not layer.is_rail_layer:
                continue
            for shape in layer.shape_list:
                if isinstance(shape, CLine):
                    all_lines.append(shape)

        # ✅ rail line이 없으면 기존 layout 유지 (깜빡임/초기화 방지)
        if not all_lines:
            return

        # 2) 이제부터 clear
        self.data_set.sections.clear()
        self.data_set.section_id_to_index.clear()

        # 2. Build Node Map (Node -> List[Line])
        node_map = {}
        for line in all_lines:
            # Round coordinates to avoid float precision issues
            p1 = (round(line.start_x, 1), round(line.start_y, 1))
            p2 = (round(line.end_x, 1), round(line.end_y, 1))
            
            if p1 not in node_map: node_map[p1] = []
            if p2 not in node_map: node_map[p2] = []
            
            node_map[p1].append(line)
            node_map[p2].append(line)

        # 3. Generate Sections (Merge connected lines)
        processed_lines = set()  # id 기반으로 추적
        sec_id_counter = 1
        node_to_section_map: Dict[Tuple[float, float], Section] = {}  # 전체 노드→Section 매핑
        
        # Helper to find other end of line
        def get_other_end(line, current_point):
            p1 = (round(line.start_x, 1), round(line.start_y, 1))
            p2 = (round(line.end_x, 1), round(line.end_y, 1))
            return p2 if p1 == current_point else p1

        # Start with junction nodes (degree != 2) or terminals
        start_nodes = [n for n, lines in node_map.items() if len(lines) != 2]
        
        # If no junctions (simple loop), pick any node
        if not start_nodes and node_map:
            start_nodes = [next(iter(node_map.keys()))]
            
        for start_node in start_nodes:
            connected_lines = node_map[start_node]
            for line in connected_lines:
                if id(line) in processed_lines: continue
                
                # Start a new section
                current_section_lines = []
                current_line = line
                current_node = start_node
                
                while current_line:
                    processed_lines.add(id(current_line))
                    current_section_lines.append(current_line)
                    
                    # Move to next node
                    next_node = get_other_end(current_line, current_node)
                    
                    # Check if next_node is a junction/terminal
                    if len(node_map[next_node]) != 2:
                        break # End of section
                    
                    # Continue traversing (degree == 2)
                    next_lines = node_map[next_node]
                    # Find the line that is not the current_line
                    next_line = next_lines[0] if next_lines[0] != current_line else next_lines[1]
                    
                    if id(next_line) in processed_lines:
                        break # Loop detected or already processed
                        
                    current_line = next_line
                    current_node = next_node
                
                # Create Section Object
                if current_section_lines:
                    sec_id = sec_id_counter
                    sec_id_counter += 1

                    section = Section(sec_id)
                    section.figures = current_section_lines

                    # Section에 속하는 모든 노드 좌표를 기록 (EQ 매핑용, merge 전)
                    for l in current_section_lines:
                        p = (round(l.start_x, 1), round(l.start_y, 1))
                        node_to_section_map[p] = section
                        p = (round(l.end_x, 1), round(l.end_y, 1))
                        node_to_section_map[p] = section

                    # 길이 계산
                    total_len = 0
                    for l in current_section_lines:
                        dx = l.end_x - l.start_x
                        dy = l.end_y - l.start_y
                        total_len += math.sqrt(dx*dx + dy*dy)
                    section.length = total_len

                    # 버퍼 추가
                    section.add_buffer(total_len, f"N_{sec_id}_S", f"N_{sec_id}_E")

                    # 다중 CLine → 단일 CLine 병합 (시각화·보간 단순화)
                    if merge_lines:
                        section.merge_figures()

                    self.data_set.sections.append(section)
                    self.data_set.section_id_to_index[sec_id] = len(self.data_set.sections) - 1

        # 4. Link Sections (Naive Geometry Matching)
        # 끝점과 시작점이 일치하면 연결 (Tolerance 1.0mm)
        for sec in self.data_set.sections:
            if not sec.figures: continue
            
            # 섹션의 시작점과 끝점 찾기 (첫 번째 라인의 시작점, 마지막 라인의 끝점)
            first_line = sec.figures[0]
            last_line = sec.figures[-1]
            
            # 섹션의 끝점 좌표
            end_x, end_y = last_line.end_x, last_line.end_y
            
            for other in self.data_set.sections:
                if sec == other: continue
                if not other.figures: continue
                
                other_start_line = other.figures[0]
                
                # 거리 계산
                dist = math.hypot(other_start_line.start_x - end_x, other_start_line.start_y - end_y)
                
                if dist < 1.0: # 연결됨
                    sec.next_sections.append(other.section_id)
                    other.prev_sections.append(sec.section_id)

        # 5. TEXT → 가장 가까운 Rail 노드 매핑 → EQ 생성 및 Section 연결
        self.data_set.eq_list.clear()
        if self.data_set.sections:
            self._bind_eq_from_text(layers, node_map, node_to_section_map)

    def _bind_eq_from_text(self, layers: List[CLayer],
                           node_map: Dict[Tuple[float, float], list],
                           node_to_section: Dict[Tuple[float, float], 'Section']):
        """
        모든 레이어의 CText를 찾아 가장 가까운 Rail 노드에 매핑하고,
        EQ 객체를 생성하여 해당 Section에 연결한다.
        node_to_section: merge 전 원본 라인의 모든 노드 → Section 매핑
        """
        node_coords = list(node_map.keys())
        if not node_coords:
            return

        # 모든 레이어에서 CText 수집
        texts: List[CText] = []
        for layer in layers:
            for shape in layer.shape_list:
                if isinstance(shape, CText):
                    texts.append(shape)

        MAX_EQ_DISTANCE = 5000.0  # DXF 단위에 맞게 조정 필요 (mm 기준)
        for text in texts:
            tx, ty = text.start_x, text.start_y
            best_node = None
            best_dist = float('inf')
            for node in node_coords:
                d = math.hypot(node[0] - tx, node[1] - ty)
                if d < best_dist:
                    best_dist = d
                    best_node = node

            # ✅ 너무 멀면 매핑 스킵
            if best_node is None or best_dist > MAX_EQ_DISTANCE:
                continue

            # EQ 생성
            eq_name = text.text
            if eq_name in self.data_set.eq_list:
                continue  # 동일 이름 EQ 중복 방지

            eq = EQ(eq_name)
            eq.left = tx
            eq.top = ty

            # 해당 노드가 속한 Section에 연결
            section = node_to_section.get(best_node)
            if section is not None:
                eq.section_id = section.section_id
                section.eq_list.append(eq)

            self.data_set.eq_list[eq_name] = eq