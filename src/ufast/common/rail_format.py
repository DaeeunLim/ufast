"""
rail_format.py — common .rail file parser

The single place where .rail files are parsed. Reads the file into a neutral record
structure (RailData); each consumer handles its own purpose-specific conversion:
  - common/rail_io.py     : RailData → CLayer (visualization) + SimulatorDataSet (Section/EQ)
  - route/rail_parser.py    : RailData → route.graph.Network (node/link graph)

No intra-project dependencies (pure stdlib) — importable from any package.

.rail file format (tab-separated, first line "RAILDATA"):
  NODE\tid\tx\ty                                       node coordinates
  LINK\tsec\ttype\tfrom\tto[\tpenalty1\tpenalty2]      section link
  RAILLIST\tsec\ttype\tfrom\tto\tsx\tsy\tex\tey\tangle\tlength   section definition
  EQTONODEMAP\teq\tnode                                 EQ→node
  NODETOEQMAP\tid1\tid2 / NODETOEQLIST\tnode\teq        node→EQ
  TEXT\teq\ttype\tx\ty                                  EQ label (visualization)
  LINE\tsec\ttype\tx1\ty1\tx2\ty2\tr\tg\tb              shape (visualization)
  CURVE\tsec\ttype\tx1\ty1\tcx\tcy\tx2\ty2\tcenterX\tcenterY\tangle\tr\tg\tb
  SCALE\tmin_x\tmax_x\tmin_y\tmax_y                     viewport extent
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class NodeRec:
    name: str          # node ID (string in the file — integer conversion is up to the consumer)
    x: float
    y: float


@dataclass
class LinkRec:
    name: str          # section ID
    link_type: str     # LINE or CURVE
    first_node: str
    second_node: str
    penalty1: Optional[float] = None
    penalty2: Optional[float] = None


@dataclass
class RailListRec:
    name: str          # section ID
    rail_type: str     # LINE or CURVE
    first_node: str
    second_node: str
    start_x: float = 0.0
    start_y: float = 0.0
    end_x: float = 0.0
    end_y: float = 0.0
    angle: float = 0.0
    length: float = 0.0
    has_geometry: bool = False


@dataclass
class TextRec:
    name: str          # EQ name
    type_str: str
    x: float
    y: float


@dataclass
class LineRec:
    name: str
    type_str: str
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class CurveRec:
    name: str
    type_str: str
    x1: float
    y1: float
    cx: float
    cy: float
    x2: float
    y2: float
    center_x: float
    center_y: float
    angle: float


@dataclass
class ScaleRec:
    min_x: float = 0.0
    max_x: float = 0.0
    min_y: float = 0.0
    max_y: float = 0.0


@dataclass
class RailData:
    nodes: List[NodeRec] = field(default_factory=list)
    links: List[LinkRec] = field(default_factory=list)
    rail_list: List[RailListRec] = field(default_factory=list)
    eq_to_node: Dict[str, str] = field(default_factory=dict)      # EQTONODEMAP
    node_to_eq_map: Dict[str, str] = field(default_factory=dict)  # NODETOEQMAP
    node_to_eq_list: Dict[str, str] = field(default_factory=dict) # NODETOEQLIST
    texts: List[TextRec] = field(default_factory=list)
    lines: List[LineRec] = field(default_factory=list)
    curves: List[CurveRec] = field(default_factory=list)
    scale: Optional[ScaleRec] = None


def parse_rail_file(filepath: str, require_header: bool = True) -> RailData:
    """
    Parse a .rail file into RailData.

    - Encoding errors are ignored (errors='ignore'); malformed lines are skipped with a warning.
    - With require_header=True, a ValueError is raised if the first line lacks "RAILDATA".
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Rail file not found: {filepath}")

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    if not lines:
        raise ValueError("Empty rail file")

    if require_header and "RAILDATA" not in lines[0]:
        raise ValueError(f"Invalid rail file header: {lines[0].strip()}")

    data = RailData()

    for line_num, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        p = line.split("\t")
        rec = p[0]

        try:
            if rec == "NODE" and len(p) >= 4:
                data.nodes.append(NodeRec(name=p[1], x=float(p[2]), y=float(p[3])))

            elif rec == "LINK" and len(p) >= 5:
                link = LinkRec(name=p[1], link_type=p[2],
                               first_node=p[3], second_node=p[4])
                if len(p) > 6:
                    try:
                        link.penalty1 = float(p[5])
                        link.penalty2 = float(p[6])
                    except ValueError:
                        pass
                data.links.append(link)

            elif rec == "RAILLIST" and len(p) >= 5:
                r = RailListRec(name=p[1], rail_type=p[2],
                                first_node=p[3], second_node=p[4])
                if len(p) >= 10:
                    r.start_x, r.start_y = float(p[5]), float(p[6])
                    r.end_x, r.end_y = float(p[7]), float(p[8])
                    r.angle = float(p[9])
                    r.has_geometry = True
                if len(p) > 10:
                    r.length = float(p[-1])  # the last column is the length
                data.rail_list.append(r)

            elif rec == "EQTONODEMAP" and len(p) >= 3:
                data.eq_to_node[p[1]] = p[2]

            elif rec == "NODETOEQMAP" and len(p) >= 3:
                data.node_to_eq_map[p[1]] = p[2]

            elif rec == "NODETOEQLIST" and len(p) >= 3:
                data.node_to_eq_list[p[1]] = p[2]

            elif rec == "TEXT" and len(p) >= 5:
                data.texts.append(TextRec(name=p[1], type_str=p[2],
                                          x=float(p[3]), y=float(p[4])))

            elif rec == "LINE" and len(p) >= 10:
                data.lines.append(LineRec(
                    name=p[1], type_str=p[2],
                    x1=float(p[3]), y1=float(p[4]),
                    x2=float(p[5]), y2=float(p[6])))

            elif rec == "CURVE" and len(p) >= 13:
                data.curves.append(CurveRec(
                    name=p[1], type_str=p[2],
                    x1=float(p[3]), y1=float(p[4]),
                    cx=float(p[5]), cy=float(p[6]),
                    x2=float(p[7]), y2=float(p[8]),
                    center_x=float(p[9]), center_y=float(p[10]),
                    angle=float(p[11])))

            elif rec == "SCALE" and len(p) >= 5:
                data.scale = ScaleRec(min_x=float(p[1]), max_x=float(p[2]),
                                      min_y=float(p[3]), max_y=float(p[4]))

        except (IndexError, ValueError) as e:
            print(f"Warning: Failed to parse line {line_num}: {e}")
            continue

    return data
