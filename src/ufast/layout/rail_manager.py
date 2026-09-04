import math
from typing import List, Dict, Tuple
from ufast.drawing.geometry import CLayer, CLine, CText
from ufast.core.components import Section, EQ
from ufast.core.data_set import SimulatorDataSet

class RailManager:
    """
    Converts CAD/drawing data (CLayer) into simulation data (Section, Node)
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

        # ✅ If there are no rail lines, keep the existing layout (avoids flicker/reset)
        if not all_lines:
            return

        # 2) Clear from here on
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
        processed_lines = set()  # tracked by id
        sec_id_counter = 1
        node_to_section_map: Dict[Tuple[float, float], Section] = {}  # mapping of every node → Section
        
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

                    # Record every node coordinate belonging to the Section (for EQ mapping, before merge)
                    for l in current_section_lines:
                        p = (round(l.start_x, 1), round(l.start_y, 1))
                        node_to_section_map[p] = section
                        p = (round(l.end_x, 1), round(l.end_y, 1))
                        node_to_section_map[p] = section

                    # Length computation
                    total_len = 0
                    for l in current_section_lines:
                        dx = l.end_x - l.start_x
                        dy = l.end_y - l.start_y
                        total_len += math.sqrt(dx*dx + dy*dy)
                    section.length = total_len

                    # Add buffer
                    section.add_buffer(total_len, f"N_{sec_id}_S", f"N_{sec_id}_E")

                    # Merge multiple CLines → a single CLine (simplifies visualization/interpolation)
                    if merge_lines:
                        section.merge_figures()

                    self.data_set.sections.append(section)
                    self.data_set.section_id_to_index[sec_id] = len(self.data_set.sections) - 1

        # 4. Link Sections (Naive Geometry Matching)
        # Connect when an end point coincides with a start point (tolerance 1.0mm)
        for sec in self.data_set.sections:
            if not sec.figures: continue
            
            # Find the section's start and end points (start of the first line, end of the last line)
            first_line = sec.figures[0]
            last_line = sec.figures[-1]
            
            # End-point coordinates of the section
            end_x, end_y = last_line.end_x, last_line.end_y
            
            for other in self.data_set.sections:
                if sec == other: continue
                if not other.figures: continue
                
                other_start_line = other.figures[0]
                
                # Distance computation
                dist = math.hypot(other_start_line.start_x - end_x, other_start_line.start_y - end_y)
                
                if dist < 1.0: # connected
                    sec.next_sections.append(other.section_id)
                    other.prev_sections.append(sec.section_id)

        # 5. TEXT → map to the nearest rail node → create EQ and attach to its Section
        self.data_set.eq_list.clear()
        if self.data_set.sections:
            self._bind_eq_from_text(layers, node_map, node_to_section_map)

    def _bind_eq_from_text(self, layers: List[CLayer],
                           node_map: Dict[Tuple[float, float], list],
                           node_to_section: Dict[Tuple[float, float], 'Section']):
        """
        Find the CText in every layer, map each to the nearest rail node,
        create an EQ object, and attach it to the corresponding Section.
        node_to_section: mapping of every node of the original (pre-merge) lines → Section
        """
        node_coords = list(node_map.keys())
        if not node_coords:
            return

        # Collect CText from all layers
        texts: List[CText] = []
        for layer in layers:
            for shape in layer.shape_list:
                if isinstance(shape, CText):
                    texts.append(shape)

        MAX_EQ_DISTANCE = 5000.0  # needs adjusting to the DXF units (assumes mm)
        for text in texts:
            tx, ty = text.start_x, text.start_y
            best_node = None
            best_dist = float('inf')
            for node in node_coords:
                d = math.hypot(node[0] - tx, node[1] - ty)
                if d < best_dist:
                    best_dist = d
                    best_node = node

            # ✅ Skip mapping if too far away
            if best_node is None or best_dist > MAX_EQ_DISTANCE:
                continue

            # Create EQ
            eq_name = text.text
            if eq_name in self.data_set.eq_list:
                continue  # avoid duplicate EQs with the same name

            eq = EQ(eq_name)
            eq.left = tx
            eq.top = ty

            # Attach to the Section that owns the node
            section = node_to_section.get(best_node)
            if section is not None:
                eq.section_id = section.section_id
                section.eq_list.append(eq)

            self.data_set.eq_list[eq_name] = eq