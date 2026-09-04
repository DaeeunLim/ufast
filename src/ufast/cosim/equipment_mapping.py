from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List


def _num(value: Any) -> float:
    return float(str(value).replace(",", "."))


def _name_key(row: Dict[str, Any]) -> str:
    for key in row.keys():
        if key.lstrip("\ufeff") == "NAME":
            return key
    return "NAME"


def _nearest_node(network, x: float, y: float) -> str | None:
    best_name = None
    best_dist = float("inf")
    for name, node in network.nodes.items():
        dist = (node.x - x) ** 2 + (node.y - y) ** 2
        if dist < best_dist:
            best_name = name
            best_dist = dist
    return best_name


def load_machine_equipment(layout_dir: str | Path, network) -> Dict[str, List[Dict[str, Any]]]:
    """Build STNFAM/tool-group -> equipment metadata for production machines.

    SMAT2022.rail maps each tool group to one representative node.  For
    equipment-level AMHS destinations, use Equipment.csv coordinates and attach
    each physical equipment to the nearest parsed .rail node.
    """
    layout = Path(layout_dir)
    equipment_csv = layout / "Equipment.csv"
    if not equipment_csv.exists():
        return {}

    by_family: Dict[str, List[Dict[str, Any]]] = {}
    with equipment_csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if not row:
                continue
            name_key = _name_key(row)
            equipment_id = row.get(name_key)
            family = row.get("TOOL_GROUP")
            if not equipment_id or not family:
                continue
            try:
                x = _num(row["POSITION_X"])
                y = _num(row["POSITION_Y"])
            except (KeyError, TypeError, ValueError):
                continue
            node = _nearest_node(network, x, y)
            if node is None:
                continue
            by_family.setdefault(family, []).append({
                "equipment_id": equipment_id,
                "node": node,
                "x": x,
                "y": y,
            })

    for items in by_family.values():
        items.sort(key=lambda item: item["equipment_id"])
    return by_family


def infer_layout_dir(rail_file: str | Path) -> Path:
    rail = Path(rail_file)
    candidate = rail.with_suffix("")
    if candidate.is_dir():
        return candidate
    sibling = rail.parent / rail.stem
    return sibling
