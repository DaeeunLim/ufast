"""
graph.py - network data structures

Ports CNode and CSection from the Java RouteManager.
Represents a rail network made of nodes, links, and sections.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Node:
    """
    A node of the rail network.
    Ports only the fields of Java CNode that the simulation needs.
    Real-time vehicle management fields (m_vtDriveVehicleList etc.) are omitted.
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

    # Sections this node belongs to (section names)
    section_list: List[str] = field(default_factory=list)

    # Travel time per neighbouring node {neighbor_name: move_in_time}
    # Java: m_htMoveInNodeTable
    move_in_times: Dict[str, float] = field(default_factory=dict)

    # Travel direction per neighbouring node {neighbor_name: direction}
    # Java: m_htMoveInDirectionTable (True=forward, False=backward)
    move_in_directions: Dict[str, bool] = field(default_factory=dict)

    # Scratch fields for Dijkstra search
    arrived_time: float = float('inf')
    arrived_length: float = float('inf')
    prev_node: Optional[str] = None  # name of the previous node
    visited: bool = False

    # Curve end-point flags (Java: m_bForwardCurveEnd, m_bBackwardCurveEnd)
    forward_curve_end: bool = False
    backward_curve_end: bool = False

    # Node angle (direction of the LINE section)
    angle: int = -1

    def get_length(self, other: Node) -> float:
        """Euclidean distance between two nodes"""
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    def set_move_in_time(self, time: float, neighbor_name: str, forward: bool):
        """
        Set the travel time to a neighbouring node.
        Java: CNode.SetMoveInTime(dblMoveInTime, Node, bForward)
        """
        self.move_in_times[neighbor_name] = time
        self.move_in_directions[neighbor_name] = forward

    def add_section(self, section_name: str):
        """Add a section this node belongs to"""
        if section_name not in self.section_list:
            self.section_list.append(section_name)

    def reset_search(self, max_cost: float = float('inf')):
        """Reset the Dijkstra search state"""
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
        Update the arrival time during Dijkstra.
        If a shorter path is found, update and return True.
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
        Update the arrival time/distance for CostSearch.
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
    A link connecting two nodes (a rail segment).
    The node pair managed as a Section by Java's AddLink.
    """
    from_node: str
    to_node: str
    section_name: str
    link_type: str = "LINE"  # LINE or CURVE
    two_way: bool = False
    distance: float = 0.0  # computed automatically


class NetworkSection:
    """
    A section of the rail network (an ordered set of nodes).
    Ports Java CSection.

    Note: named NetworkSection to distinguish it from
    core.components.Section (the simulation section).
    """

    def __init__(self, name: str, section_type: str = "LINE", two_way: bool = False):
        self.name = name
        self.section_type = section_type  # LINE or CURVE
        self.two_way = two_way
        self.node_list: List[str] = []  # ordered list of node names
        self.node_index: Dict[str, int] = {}  # node name → index (Java: m_htNodeTable)

    def add_node(self, node_name: str):
        """Add a node to the section"""
        self.node_list.append(node_name)

    def make_hash_table(self):
        """Build the node index table (Java: MakeHashTable)"""
        self.node_index = {name: i for i, name in enumerate(self.node_list)}

    def find_node(self, node_name: str) -> int:
        """Return the node index, or -1 if absent (Java: FindNode)"""
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
    The complete rail network structure.
    Manages nodes, links, sections, and equipment mappings.
    """

    def __init__(self):
        self.nodes: Dict[str, Node] = {}
        self.links: List[Link] = []
        self.sections: Dict[str, NetworkSection] = {}

        # Equipment ↔ node mapping
        self.eq_to_node: Dict[str, str] = {}  # equipment name → node name
        self.node_to_eq: Dict[str, str] = {}  # node name → equipment name

        # Disabled / virtual node lists
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
        Add a node to the network.
        Java: RouteManager.AddNode()
        If it already exists, return the existing node.
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
        Add a link connecting two nodes.
        Java: RouteManager.AddLink()

        Creates the section if it does not exist and adds the nodes to it.
        """
        first_node = self.nodes.get(first_node_name)
        second_node = self.nodes.get(second_node_name)
        if first_node is None or second_node is None:
            return None

        # Create or look up the section
        if section_name not in self.sections:
            section = NetworkSection(section_name, section_type, two_way)
            self.sections[section_name] = section
        else:
            section = self.sections[section_name]

        # Add nodes to the section (avoid duplicates)
        # When several links are added to the same section, nodes already present are skipped
        first_node.add_section(section_name)
        if first_node_name not in section.node_list:
            section.add_node(first_node_name)

        second_node.add_section(section_name)
        if second_node_name not in section.node_list:
            section.add_node(second_node_name)

        # Curve end-point flags (Java: curve handling in CSection.AddNode)
        if section_type == "CURVE":
            if section.node_count == 2:  # the first node added
                first_node.backward_curve_end = True
            second_node.forward_curve_end = True
        else:
            # LINE type: compute the angle
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

        # Create the link
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
        """Add an equipment ↔ node mapping"""
        self.eq_to_node[eq_name] = node_name
        self.node_to_eq[node_name] = eq_name

    def get_neighbors(self, node_name: str) -> List[Tuple[str, float]]:
        """
        Return the neighbouring nodes of a node together with the travel times.
        [(neighbor_name, move_in_time), ...]
        """
        node = self.nodes.get(node_name)
        if node is None:
            return []
        return [(name, time) for name, time in node.move_in_times.items()]

    def get_section_nodes(self, section_name: str) -> List[str]:
        """Return the list of node names belonging to a section"""
        section = self.sections.get(section_name)
        if section is None:
            return []
        return list(section.node_list)

    def finalize(self):
        """
        Build the hash tables once network construction is complete.
        Call once after all add_node/add_link calls.
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
