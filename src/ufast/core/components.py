from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from ufast.drawing.geometry import Figure


@dataclass
class OHTBuffer:
    capacity: int
    buffer: List[Optional[str]] = field(default_factory=list)
    initial_node: str = ""
    terminal_node: str = ""
    rail_length: float = 0.0

    def __post_init__(self):
        self.buffer = [None] * self.capacity

    @property
    def buffer_state(self) -> int:
        return sum(1 for item in self.buffer if item is not None)

    def vacant_buffer_exist(self) -> bool:
        return self.buffer_state < self.capacity

    def find_vehicle(self, oht_name: str) -> int:
        try:
            return self.buffer.index(oht_name)
        except ValueError:
            return -1

    def is_front_vehicle(self, oht_name: str) -> bool:
        idx = self.find_vehicle(oht_name)
        return idx == 0

    def add_vehicle(self, oht_name: str) -> int:
        for i in range(self.capacity):
            if self.buffer[i] is None:
                self.buffer[i] = oht_name
                return i
        return -1

    def remove_vehicle(self, oht_name: str) -> int:
        idx = self.find_vehicle(oht_name)
        if idx < 0:
            return -1
        self.buffer[idx] = None
        compact = [item for item in self.buffer if item is not None]
        compact += [None] * (self.capacity - len(compact))
        self.buffer = compact[:self.capacity]
        return idx

    def pop_front(self) -> Optional[str]:
        if not self.buffer or self.buffer[0] is None:
            return None
        name = self.buffer[0]
        self.remove_vehicle(name)
        return name

    def msg_receiver(self, msg: str, param: Any):
        if msg == "ADD":
            return self.add_vehicle(param)
        elif msg == "POP":
            if param is None:
                return self.pop_front()
            self.remove_vehicle(param)
            return param
        return None


class OHT:
    def __init__(self, name: str):
        self.name = name
        self.status = "IDLE"  # IDLE, ASSIGNED, LOADED, RUN, UNLOADING
        self.current_section_id: int = -1
        self.current_buffer_index: int = -1
        self.current_eq_index: int = -1
        self.current_slot_index: int = -1

        self.x: float = 0.0
        self.y: float = 0.0

        self.destination_eq: Optional[str] = None
        self.loaded_lot_info: Optional[str] = None
        self.path_in_section_ids: List[int] = []

        self.time_enter_current_section: float = 0.0

        # Visualization props
        self.color = "BLUE"

    def set_position(self, x: float, y: float):
        self.x = x
        self.y = y

    def update_index_in_section(self, section_id: int, buffer_idx: int, eq_idx: int, slot_idx: int = -1):
        self.current_section_id = section_id
        self.current_buffer_index = buffer_idx
        self.current_eq_index = eq_idx
        self.current_slot_index = slot_idx


class EQ:
    def __init__(self, name: str):
        self.name = name
        self.section_id: int = -1
        self.port_buffer: List[Optional[str]] = [None]  # Port capacity 1
        self.internal_buffer: List[str] = []  # Infinite capacity in Java code
        self.tr_command: List[str] = []  # TR Commands

        # Visualization
        self.left: float = 0.0
        self.top: float = 0.0
        self.is_loaded: bool = False

        # EQ status (for visualization)
        # "IDLE"           : no lot (default gray)
        # "WAITING"        : lot at port, OHT not yet assigned (orange)
        # "OHT_COMING"     : OHT on its way to pick up (green)
        # "PROCESS_WAITING": waiting for a new lot to arrive / rundown measurement started (light blue)
        # "PROCESSING"     : processing started after a new lot arrived (green)
        self.eq_status: str = "IDLE"
        self.waiting_lot_count: int = 0  # for the source EQ: port + internal combined
        self.inbound_lot_count: int = 0  # for the destination EQ: number of lots expected to arrive
        self.rundown_wait_lot_id: Optional[str] = None
        self.processing_lot_id: Optional[str] = None

    def message_receiver(self, msg: str, param: str, vehicle_controller):
        if msg == "LOT_CREATED":
            if self.port_buffer[0] is None:
                self.port_buffer[0] = param
                self.is_loaded = True
            else:
                self.internal_buffer.append(param)
            self.waiting_lot_count = (1 if self.port_buffer[0] else 0) + len(self.internal_buffer)
            # WAITING if no OHT has been assigned yet
            if self.eq_status == "IDLE":
                self.eq_status = "WAITING"

        elif msg == "OHT_ASSIGNED":
            # An OHT has departed toward this EQ (called from controllers)
            self.eq_status = "OHT_COMING"

        elif msg == "LOT_TRANSPORTED":
            # The OHT has arrived and taken the lot
            oht = vehicle_controller.oht_list.get(param)
            if oht and self.port_buffer[0]:
                oht.loaded_lot_info = self.port_buffer[0]
                self.port_buffer[0] = None

                # Move from the internal buffer to the port
                if self.internal_buffer:
                    self.port_buffer[0] = self.internal_buffer.pop(0)
                    self.is_loaded = True
                    self.eq_status = "WAITING"  # waiting for the next lot
                else:
                    self.is_loaded = False
                    self.eq_status = "IDLE"
            self.waiting_lot_count = (1 if self.port_buffer[0] else 0) + len(self.internal_buffer)

        elif msg == "LOT_INBOUND":
            # A loaded OHT is delivering to this EQ: equipment-side processing wait starts
            self.inbound_lot_count += 1
            lot_info = str(param) if param is not None else ""
            self.rundown_wait_lot_id = lot_info.split("@")[0] if lot_info else None
            self.eq_status = "PROCESS_WAITING"

        elif msg == "LOT_DELIVERED":
            # A loaded OHT has delivered the lot to this EQ: equipment-side processing starts
            if self.inbound_lot_count > 0:
                self.inbound_lot_count -= 1
            lot_info = str(param) if param is not None else ""
            self.processing_lot_id = lot_info.split("@")[0] if lot_info else self.rundown_wait_lot_id
            self.rundown_wait_lot_id = None
            self.eq_status = "PROCESSING"


class Section:
    def __init__(self, section_id: int):
        self.section_id = section_id
        self.length: float = 0.0
        self.virtual_speed: float = 0.0
        self.is_fab_in: bool = False
        self.is_fab_out: bool = False

        self.next_sections: List[int] = []
        self.prev_sections: List[int] = []

        self.oht_buffers: List[OHTBuffer] = []
        self.eq_list: List[EQ] = []
        self.section_event_list: List[Any] = []  # List[Event]

        self.figures: List[Figure] = []  # Visualization figures

    def add_buffer(self, rail_length: float, initial_node: str, terminal_node: str):
        # Assumes OHT Length = 1.0 (VehicleSpec)
        capacity = int(rail_length / 1.0)
        if capacity == 0: capacity = 1

        buf = OHTBuffer(capacity, initial_node=initial_node, terminal_node=terminal_node, rail_length=rail_length)
        self.oht_buffers.insert(0, buf)  # Java: add(0, buffer)

    def merge_figures(self):
        """
        Merge multiple CLines within the Section into a single CLine.
        - start point of the first figure → end point of the last figure
        - self.length is not changed (the original path length is preserved)
        - Sections containing a CQuadCurve are not merged
        """
        from ufast.drawing.geometry import CLine, CQuadCurve

        if len(self.figures) <= 1:
            return
        if any(isinstance(fig, CQuadCurve) for fig in self.figures):
            return
        if not all(isinstance(fig, CLine) for fig in self.figures):
            return

        first = self.figures[0]
        last = self.figures[-1]
        merged = CLine(
            name=first.name, type_str=first.type,
            x1=first.start_x, y1=first.start_y,
            x2=last.end_x, y2=last.end_y,
            color=first.color)
        self.figures = [merged]

    def message_receiver(self, msg: str, target_section, from_buf_idx: int, to_buf_idx: int, oht_name: str):
        if from_buf_idx < 0 or from_buf_idx >= len(self.oht_buffers):
            return False
        src = self.oht_buffers[from_buf_idx]
        if src.find_vehicle(oht_name) < 0:
            return False
        if not src.is_front_vehicle(oht_name):
            return False

        if msg == "MOVE_TO_NEXT_BUFFER":
            if to_buf_idx < 0 or to_buf_idx >= len(self.oht_buffers):
                return False
            dst = self.oht_buffers[to_buf_idx]
            if not dst.vacant_buffer_exist():
                return False
            removed = src.remove_vehicle(oht_name)
            if removed < 0:
                return False
            added = dst.add_vehicle(oht_name)
            return added >= 0

        elif msg == "MOVE_TO_NEXT_SECTION":
            if target_section is None or to_buf_idx < 0 or to_buf_idx >= len(target_section.oht_buffers):
                return False
            dst = target_section.oht_buffers[to_buf_idx]
            if not dst.vacant_buffer_exist():
                return False
            removed = src.remove_vehicle(oht_name)
            if removed < 0:
                return False
            added = dst.add_vehicle(oht_name)
            return added >= 0
        return False
