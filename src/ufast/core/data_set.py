import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
import time

@dataclass(order=True)
class Event:
    """
    Simulation event class
    Uses order=True so heapq sorts by time
    """
    time_scheduled: float
    priority: int = field(compare=False, default=0) # used when a priority among same-time events is needed
    event_type: str = field(compare=False, default="")
    from_node: str = field(compare=False, default="")
    to_node: str = field(compare=False, default="")
    oht_id: str = field(compare=False, default=None)
    time_enter_section: float = field(compare=False, default=0.0)
    
    # Counterpart of Java's clone
    def clone(self):
        return Event(
            time_scheduled=self.time_scheduled,
            priority=self.priority,
            event_type=self.event_type,
            from_node=self.from_node,
            to_node=self.to_node,
            oht_id=self.oht_id,
            time_enter_section=self.time_enter_section
        )

class SimulatorDataSet:
    """
    Global data store using the Singleton pattern
    """
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = SimulatorDataSet()
        return cls._instance

    def __init__(self):
        if SimulatorDataSet._instance is not None:
            raise Exception("This class is a singleton!")
        else:
            SimulatorDataSet._instance = self
            self.clear()

    def clear(self):
        # Core Data Structures
        self.sections: List[Any] = [] # List[Section]
        self.layers: List[Any] = [] # List[CLayer] - Raw CAD Layers
        self.eq_list: Dict[str, Any] = {} # Dict[str, EQ]
        self.oht_list: Dict[str, Any] = {} # Dict[str, OHT]
        
        # Mapping Data
        self.node_position: Dict[str, str] = {} # "NodeID": "x_y"
        self.section_id_to_index: Dict[int, int] = {}
        self.node_section_list: Dict[str, int] = {}
        self.discovered_section_ids_per_from_to: Dict[str, List[int]] = {}
        
        # Event Management
        self.fab_event_map: Dict[str, Event] = {}
        self.event_queue: List[Event] = [] # Priority Queue (heap)
        
        # Simulation Status
        self.main_clock: float = 0.0
        self.simulation_start_time: float = 0.0
        self.lot_count: int = 0
        self.num_of_processed_lot: int = 0
        
        # Logs & Outputs
        self.completed_trs: Dict[str, Any] = {}
        
        # Rail & Layout (Placeholder)
        self.rail = None

    def add_event(self, evt: Event, section=None):
        """
        Register an event with the scheduler.
        If section is None it is a global event (FAB Event); otherwise a section-internal event
        """
        if section is None:
            heapq.heappush(self.event_queue, evt)
        else:
            section.section_event_list.append(evt)
            section.section_event_list.sort(key=lambda x: x.time_scheduled)

    def remove_event_from_queue(self, key_prefix: str, time_val: float):
        # Python's heapq does not support arbitrary removal, so the usual approach is to
        # validate on pop or to remove from fab_event_map so the event is ignored when processed.
        # Here we mark it by removing it from the map.
        keys_to_remove = [k for k in self.fab_event_map.keys() if k.startswith(key_prefix)]
        for k in keys_to_remove:
            del self.fab_event_map[k]

    def increase_lot_count(self):
        self.lot_count += 1

    def plus_num_of_processed_lot(self):
        self.num_of_processed_lot += 1