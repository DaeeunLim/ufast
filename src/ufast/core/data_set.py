import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
import time

@dataclass(order=True)
class Event:
    """
    시뮬레이션 이벤트 클래스
    heapq에서 시간순으로 정렬하기 위해 order=True 사용
    """
    time_scheduled: float
    priority: int = field(compare=False, default=0) # 동시간대 우선순위 필요시 사용
    event_type: str = field(compare=False, default="")
    from_node: str = field(compare=False, default="")
    to_node: str = field(compare=False, default="")
    oht_id: str = field(compare=False, default=None)
    time_enter_section: float = field(compare=False, default=0.0)
    
    # Java의 clone 대응
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
    Singleton 패턴을 활용한 전역 데이터 저장소
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
        이벤트를 스케줄러에 등록합니다.
        section이 None이면 전역 이벤트(FAB Event), 아니면 섹션 내부 이벤트로 처리
        """
        if section is None:
            heapq.heappush(self.event_queue, evt)
        else:
            section.section_event_list.append(evt)
            section.section_event_list.sort(key=lambda x: x.time_scheduled)

    def remove_event_from_queue(self, key_prefix: str, time_val: float):
        # Python heapq는 임의 삭제가 어렵으므로, pop할 때 유효성 검사를 하거나
        # fab_event_map에서 제거하여 처리 시점에 무시하도록 구현하는 것이 일반적임.
        # 여기서는 map에서 제거하는 것으로 마킹함.
        keys_to_remove = [k for k in self.fab_event_map.keys() if k.startswith(key_prefix)]
        for k in keys_to_remove:
            del self.fab_event_map[k]

    def increase_lot_count(self):
        self.lot_count += 1

    def plus_num_of_processed_lot(self):
        self.num_of_processed_lot += 1