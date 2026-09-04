"""
graph.py - 네트워크 데이터 구조

Java RouteManager의 CNode, CSection을 포팅.
노드, 링크, 섹션으로 구성된 레일 네트워크를 표현한다.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Node:
    """
    레일 네트워크의 노드.
    Java CNode에서 시뮬레이션에 필요한 필드만 포팅.
    실시간 차량 관리 필드(m_vtDriveVehicleList 등)는 제외.
    """
    name: str
    x: float
    y: float
    use: bool = True
    hid: str = ""
    area: str = ""
    zone: str = ""
    virtual: bool = False
    traffic_penalty: float = 1.0

    # 소속 섹션 목록 (섹션 이름)
    section_list: List[str] = field(default_factory=list)

    # 인접 노드별 이동 시간 {neighbor_name: move_in_time}
    # Java: m_htMoveInNodeTable
    move_in_times: Dict[str, float] = field(default_factory=dict)

    # 인접 노드별 이동 방향 {neighbor_name: direction}
    # Java: m_htMoveInDirectionTable (True=forward, False=backward)
    move_in_directions: Dict[str, bool] = field(default_factory=dict)

    # Dijkstra 탐색용 임시 필드
    arrived_time: float = float('inf')
    arrived_length: float = float('inf')
    prev_node: Optional[str] = None  # 이전 노드 이름
    visited: bool = False

    # 커브 끝점 여부 (Java: m_bForwardCurveEnd, m_bBackwardCurveEnd)
    forward_curve_end: bool = False
    backward_curve_end: bool = False

    # 노드 각도 (LINE 섹션의 방향)
    angle: int = -1

    def get_length(self, other: Node) -> float:
        """두 노드 간 유클리드 거리"""
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    def set_move_in_time(self, time: float, neighbor_name: str, forward: bool):
        """
        인접 노드까지의 이동 시간 설정.
        Java: CNode.SetMoveInTime(dblMoveInTime, Node, bForward)
        """
        self.move_in_times[neighbor_name] = time
        self.move_in_directions[neighbor_name] = forward

    def add_section(self, section_name: str):
        """노드가 속한 섹션 추가"""
        if section_name not in self.section_list:
            self.section_list.append(section_name)

    def reset_search(self, max_cost: float = float('inf')):
        """Dijkstra 탐색 상태 초기화"""
        self.arrived_time = max_cost
        self.arrived_length = max_cost
        self.prev_node = None
        self.visited = False

    def set_arrived_time(
        self,
        time: float,
        prev_node_name: Optional[str] = None,
    ) -> bool:
        """
        Dijkstra에서 도착 시간 갱신.
        더 짧은 경로가 발견되면 갱신하고 True 반환.
        Java: CNode.SetArrivedTime
        """
        if time < self.arrived_time:
            self.arrived_time = time
            self.prev_node = prev_node_name
            return True
        return False

    def set_cost_arrived_time(
        self,
        time: float,
        length: float,
        prev_node_name: Optional[str] = None,
    ) -> bool:
        """
        CostSearch용 도착 시간/거리 갱신.
        Java: CNode.SetCostArrivedTime
        """
        if time < self.arrived_time:
            self.arrived_time = time
            self.arrived_length = length
            self.prev_node = prev_node_name
            return True
        return False


@dataclass
class Link:
    """
    두 노드를 연결하는 링크 (레일 구간).
    Java의 AddLink에서 Section으로 관리되는 노드 쌍.
    """
    from_node: str
    to_node: str
    section_name: str
    link_type: str = "LINE"  # LINE or CURVE
    two_way: bool = False
    distance: float = 0.0  # 자동 계산


class NetworkSection:
    """
    레일 네트워크의 섹션 (노드의 순서 있는 집합).
    Java CSection을 포팅.

    Note: core.components.Section (시뮬레이션용 섹션)과 구분하기 위해
    NetworkSection으로 명명.
    """

    def __init__(self, name: str, section_type: str = "LINE", two_way: bool = False):
        self.name = name
        self.section_type = section_type  # LINE or CURVE
        self.two_way = two_way
        self.node_list: List[str] = []  # 노드 이름의 순서 리스트
        self.node_index: Dict[str, int] = {}  # 노드 이름 → 인덱스 (Java: m_htNodeTable)

    def add_node(self, node_name: str):
        """섹션에 노드 추가"""
        self.node_list.append(node_name)

    def make_hash_table(self):
        """노드 인덱스 테이블 생성 (Java: MakeHashTable)"""
        self.node_index = {name: i for i, name in enumerate(self.node_list)}

    def find_node(self, node_name: str) -> int:
        """노드 인덱스 반환. 없으면 -1 (Java: FindNode)"""
        return self.node_index.get(node_name, -1)

    @property
    def first_node(self) -> Optional[str]:
        return self.node_list[0] if self.node_list else None

    @property
    def last_node(self) -> Optional[str]:
        return self.node_list[-1] if self.node_list else None

    @property
    def node_count(self) -> int:
        return len(self.node_list)

    def get_node(self, index: int) -> Optional[str]:
        if 0 <= index < len(self.node_list):
            return self.node_list[index]
        return None


class Network:
    """
    레일 네트워크 전체 구조.
    노드, 링크, 섹션, 설비 매핑을 관리한다.
    """

    def __init__(self):
        self.nodes: Dict[str, Node] = {}
        self.links: List[Link] = []
        self.sections: Dict[str, NetworkSection] = {}

        # 설비 ↔ 노드 매핑
        self.eq_to_node: Dict[str, str] = {}  # 설비명 → 노드명
        self.node_to_eq: Dict[str, str] = {}  # 노드명 → 설비명

        # 비활성/가상 노드 목록
        self.disabled_nodes: List[str] = []
        self.virtual_nodes: List[str] = []

    def add_node(
        self,
        name: str,
        x: float,
        y: float,
        use: bool = True,
        hid: str = "",
        area: str = "",
        zone: str = "",
        virtual: bool = False,
        traffic_penalty: float = 1.0,
    ) -> Node:
        """
        네트워크에 노드 추가.
        Java: RouteManager.AddNode()
        이미 존재하면 기존 노드 반환.
        """
        if name in self.nodes:
            return self.nodes[name]

        node = Node(
            name=name, x=x, y=y, use=use,
            hid=hid, area=area, zone=zone,
            virtual=virtual, traffic_penalty=traffic_penalty,
        )
        self.nodes[name] = node

        if not use:
            self.disabled_nodes.append(name)
        if virtual:
            self.virtual_nodes.append(name)

        return node

    def add_link(
        self,
        section_name: str,
        section_type: str,
        two_way: bool,
        first_node_name: str,
        second_node_name: str,
    ) -> Optional[Link]:
        """
        두 노드를 연결하는 링크 추가.
        Java: RouteManager.AddLink()

        섹션이 없으면 생성하고, 노드를 섹션에 추가한다.
        """
        first_node = self.nodes.get(first_node_name)
        second_node = self.nodes.get(second_node_name)
        if first_node is None or second_node is None:
            return None

        # 섹션 생성 또는 조회
        if section_name not in self.sections:
            section = NetworkSection(section_name, section_type, two_way)
            self.sections[section_name] = section
        else:
            section = self.sections[section_name]

        # 노드를 섹션에 추가 (중복 방지)
        # 같은 섹션에 여러 링크를 추가할 때 이미 있는 노드는 건너뜀
        first_node.add_section(section_name)
        if first_node_name not in section.node_list:
            section.add_node(first_node_name)

        second_node.add_section(section_name)
        if second_node_name not in section.node_list:
            section.add_node(second_node_name)

        # 커브 끝점 설정 (Java: CSection.AddNode의 커브 처리)
        if section_type == "CURVE":
            if section.node_count == 2:  # 첫 번째로 추가된 노드
                first_node.backward_curve_end = True
            second_node.forward_curve_end = True
        else:
            # LINE 타입: 각도 계산
            if section.node_count >= 2:
                first = self.nodes[section.first_node]
                angle = int(math.degrees(
                    math.atan2(second_node.y - first.y, second_node.x - first.x)
                ))
                if angle < 0:
                    angle += 180
                if section.node_count == 2:
                    first.angle = angle
                second_node.angle = angle

        # 링크 생성
        distance = first_node.get_length(second_node)
        link = Link(
            from_node=first_node_name,
            to_node=second_node_name,
            section_name=section_name,
            link_type=section_type,
            two_way=two_way,
            distance=distance,
        )
        self.links.append(link)

        return link

    def add_eq_mapping(self, eq_name: str, node_name: str):
        """설비 ↔ 노드 매핑 추가"""
        self.eq_to_node[eq_name] = node_name
        self.node_to_eq[node_name] = eq_name

    def get_neighbors(self, node_name: str) -> List[Tuple[str, float]]:
        """
        특정 노드의 인접 노드와 이동 시간 반환.
        [(neighbor_name, move_in_time), ...]
        """
        node = self.nodes.get(node_name)
        if node is None:
            return []
        return [(name, time) for name, time in node.move_in_times.items()]

    def get_section_nodes(self, section_name: str) -> List[str]:
        """섹션에 속한 노드 이름 리스트 반환"""
        section = self.sections.get(section_name)
        if section is None:
            return []
        return list(section.node_list)

    def finalize(self):
        """
        네트워크 구축 완료 후 해시 테이블 생성.
        모든 add_node/add_link 호출 후에 한 번 호출.
        """
        for section in self.sections.values():
            section.make_hash_table()

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def link_count(self) -> int:
        return len(self.links)

    @property
    def section_count(self) -> int:
        return len(self.sections)

    def __repr__(self) -> str:
        return (
            f"Network(nodes={self.node_count}, "
            f"links={self.link_count}, "
            f"sections={self.section_count}, "
            f"eq_mappings={len(self.eq_to_node)})"
        )
