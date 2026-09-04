"""
rail_parser.py - .rail → Network 변환기

파일 파싱은 common/rail_format.py(공통 파서)가 담당하고,
이 모듈은 그 결과(RailData)를 경로 탐색용 Network 객체
(노드/링크/섹션 그래프)로 변환한다.

Java Rail.java의 그래프 구축 로직을 포팅.
"""

from __future__ import annotations
from typing import Dict, List
from dataclasses import dataclass

from ufast.common.rail_format import parse_rail_file, RailData
from .graph import Network


@dataclass
class RailGeometry:
    """RAILLIST 레코드: 노드 간 레일 기하 정보"""
    name: str
    rail_type: str  # LINE or CURVE
    first_node: str
    second_node: str
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    angle: float = 0.0
    length: float = 0.0


@dataclass
class ScaleInfo:
    """SCALE 레코드: 도면 영역"""
    min_x: float = 0.0
    max_x: float = 0.0
    min_y: float = 0.0
    max_y: float = 0.0


class RailParser:
    """
    .rail 파일을 파싱하여 Network 객체를 생성한다.

    Usage:
        parser = RailParser()
        network = parser.parse("layout.rail")
        # network.nodes, network.links, network.sections 등 사용 가능
    """

    def __init__(self):
        self.network: Network = Network()
        self.rail_geometries: List[RailGeometry] = []
        self.scale: ScaleInfo = ScaleInfo()

        # 역방향 매핑 (노드 → 설비 리스트)
        self.node_to_eq_map: Dict[str, str] = {}
        self.node_to_eq_list: Dict[str, str] = {}

    def parse(self, filepath: str) -> Network:
        """
        .rail 파일을 파싱하여 Network 반환.

        Raises:
            FileNotFoundError: 파일이 없는 경우
            ValueError: 파일 형식이 올바르지 않은 경우
        """
        data = parse_rail_file(filepath, require_header=True)
        return self.build(data)

    def build(self, data: RailData) -> Network:
        """RailData(공통 파서 결과)로부터 Network를 구축한다."""
        self.network = Network()
        self.rail_geometries = []

        # NODE → Network 노드
        # Java: saveNodeData → AddNode(name, x, y, true, "A", "B", "C", false, 1)
        for n in data.nodes:
            self.network.add_node(
                name=n.name, x=n.x, y=n.y,
                use=True, hid="A", area="B", zone="C",
                virtual=False, traffic_penalty=1.0,
            )

        # LINK → Network 링크 (+선택적 traffic penalty)
        # Java: saveLinkData → AddLink(name, type, false, firstNode, secondNode)
        for l in data.links:
            self.network.add_link(
                section_name=l.name,
                section_type=l.link_type,
                two_way=False,
                first_node_name=l.first_node,
                second_node_name=l.second_node,
            )
            if l.penalty1 is not None and l.first_node in self.network.nodes:
                self.network.nodes[l.first_node].traffic_penalty = l.penalty1
            if l.penalty2 is not None and l.second_node in self.network.nodes:
                self.network.nodes[l.second_node].traffic_penalty = l.penalty2

        # EQTONODEMAP → EQ 매핑
        for eq_name, node_name in data.eq_to_node.items():
            self.network.add_eq_mapping(eq_name, node_name)

        self.node_to_eq_map = dict(data.node_to_eq_map)
        self.node_to_eq_list = dict(data.node_to_eq_list)

        # RAILLIST → 레일 기하 정보 (기하 필드가 있는 레코드만)
        for r in data.rail_list:
            if not r.has_geometry:
                continue
            self.rail_geometries.append(RailGeometry(
                name=r.name, rail_type=r.rail_type,
                first_node=r.first_node, second_node=r.second_node,
                start_x=r.start_x, start_y=r.start_y,
                end_x=r.end_x, end_y=r.end_y,
                angle=r.angle, length=r.length,
            ))

        if data.scale is not None:
            self.scale = ScaleInfo(
                min_x=data.scale.min_x, max_x=data.scale.max_x,
                min_y=data.scale.min_y, max_y=data.scale.max_y,
            )

        # 네트워크 해시 테이블 생성
        self.network.finalize()

        return self.network

    def get_rail_geometries(self) -> List[RailGeometry]:
        """파싱된 레일 기하 정보 반환 (시각화 등에 활용)"""
        return self.rail_geometries

    def get_scale(self) -> ScaleInfo:
        """도면 영역 정보 반환"""
        return self.scale
