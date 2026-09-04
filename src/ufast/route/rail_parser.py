"""
rail_parser.py - .rail → Network converter

File parsing is handled by common/rail_format.py (the shared parser);
this module converts its result (RailData) into the Network object used
for route search (node/link/section graph).

Ports the graph-construction logic of Java Rail.java.
"""

from __future__ import annotations
from typing import Dict, List
from dataclasses import dataclass

from ufast.common.rail_format import parse_rail_file, RailData
from .graph import Network


@dataclass
class RailGeometry:
    """RAILLIST record: rail geometry between two nodes"""
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
    """SCALE record: drawing extent"""
    min_x: float = 0.0
    max_x: float = 0.0
    min_y: float = 0.0
    max_y: float = 0.0


class RailParser:
    """
    Parses a .rail file and builds a Network object.

    Usage:
        parser = RailParser()
        network = parser.parse("layout.rail")
        # network.nodes, network.links, network.sections etc. are then available
    """

    def __init__(self):
        self.network: Network = Network()
        self.rail_geometries: List[RailGeometry] = []
        self.scale: ScaleInfo = ScaleInfo()

        # Reverse mapping (node → equipment list)
        self.node_to_eq_map: Dict[str, str] = {}
        self.node_to_eq_list: Dict[str, str] = {}

    def parse(self, filepath: str) -> Network:
        """
        Parse a .rail file and return a Network.

        Raises:
            FileNotFoundError: if the file does not exist
            ValueError: if the file format is invalid
        """
        data = parse_rail_file(filepath, require_header=True)
        return self.build(data)

    def build(self, data: RailData) -> Network:
        """Build a Network from RailData (the shared parser's output)."""
        self.network = Network()
        self.rail_geometries = []

        # NODE → Network node
        # Java: saveNodeData → AddNode(name, x, y, true, "A", "B", "C", false, 1)
        for n in data.nodes:
            self.network.add_node(
                name=n.name, x=n.x, y=n.y,
                use=True, hid="A", area="B", zone="C",
                virtual=False, traffic_penalty=1.0,
            )

        # LINK → Network link (+ optional traffic penalty)
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

        # EQTONODEMAP → EQ mapping
        for eq_name, node_name in data.eq_to_node.items():
            self.network.add_eq_mapping(eq_name, node_name)

        self.node_to_eq_map = dict(data.node_to_eq_map)
        self.node_to_eq_list = dict(data.node_to_eq_list)

        # RAILLIST → rail geometry (only records that carry geometry fields)
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

        # Build the network hash tables
        self.network.finalize()

        return self.network

    def get_rail_geometries(self) -> List[RailGeometry]:
        """Return the parsed rail geometry (used for visualization etc.)"""
        return self.rail_geometries

    def get_scale(self) -> ScaleInfo:
        """Return the drawing extent"""
        return self.scale
