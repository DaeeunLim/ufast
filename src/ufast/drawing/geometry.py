from dataclasses import dataclass, field
from typing import List, Optional, Any

@dataclass
class FigurePoint:
    x: float = 0.0
    y: float = 0.0

@dataclass
class Figure:
    name: str = ""
    type: str = ""
    layer_name: str = "Default"
    color: Any = None  # (r, g, b) tuple or hex or int index
    
    # Common properties
    start_x: float = 0.0
    start_y: float = 0.0
    end_x: float = 0.0
    end_y: float = 0.0

@dataclass
class CLine(Figure):
    def __init__(self, name, type_str, x1, y1, x2, y2, color):
        super().__init__(name=name, type=type_str, color=color)
        self.start_x = x1
        self.start_y = y1
        self.end_x = x2
        self.end_y = y2

@dataclass
class CCircle(Figure):
    # Java code uses bounding box (left, top, width, height) for rendering
    left: float = 0.0
    top: float = 0.0
    width: float = 0.0
    height: float = 0.0
    
    def __init__(self, name, type_str, left, top, width, height, color):
        super().__init__(name=name, type=type_str, color=color)
        self.left = left
        self.top = top
        self.width = width
        self.height = height

@dataclass
class CText(Figure):
    text: str = ""
    
    def __init__(self, text, type_str, x, y, color=None):
        super().__init__(name=text, type=type_str, color=color)
        self.text = text
        self.start_x = x
        self.start_y = y

@dataclass
class CQuadCurve(Figure):
    ctrl_x: float = 0.0
    ctrl_y: float = 0.0
    
    # Additional properties for arc reconstruction
    center_x: float = 0.0
    center_y: float = 0.0
    angle: float = 0.0

    def __init__(self, name, type_str, x1, y1, cx, cy, x2, y2, color):
        super().__init__(name=name, type=type_str, color=color)
        self.start_x = x1
        self.start_y = y1
        self.ctrl_x = cx
        self.ctrl_y = cy
        self.end_x = x2
        self.end_y = y2

@dataclass
class CLayer:
    layer_name: str = "Default"
    color: int = 7 # Default white/black
    is_turned_on: bool = True
    is_rail_layer: bool = False
    shape_list: List[Figure] = field(default_factory=list)

@dataclass
class FigureBlock:
    name: str = ""
    # Simplified: In Java, it had a FigureSet. Here we can just store lists of figures if needed.
    # For now, we'll assume the converter handles the explosion of blocks into shapes.
    figures: List[Figure] = field(default_factory=list)