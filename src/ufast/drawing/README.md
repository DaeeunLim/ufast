# src/ufast/drawing — CAD figure data model

## Role

The lowest layer, holding only the **CAD figure dataclasses** of the drawing (DXF/.rail) world. It represents lines, circles, text, quadratic Bezier curves, and layers; the parsers (`common/dxf_parser`, `common/rail_io`) create these figures, and the layout conversion (`layout/rail_manager`) and GUI rendering (`gui/viewer`) consume them. It is kept separate from the simulation entities (`core`) so that parsers and renderers do not depend on simulation code.

> Regular package (`__init__.py` present).

## Files

| File | Key classes | Role |
|---|---|---|
| `geometry.py` | `Figure`, `CLine`, `CCircle`, `CText`, `CQuadCurve`, `CLayer`, `FigureBlock` | CAD figure dataclasses — line, circle, text (used to extract EQ names), quadratic Bezier, layer (`is_rail_layer` flag) |

## Used by

- `common/dxf_parser.py`, `common/rail_io.py` — parse DXF/.rail files and create figures
- `layout/rail_manager.py` — figure → `Section` graph conversion
- `core/components.py` — `Section.figures` visualization figures
- `gui/viewer.py`, `gui/layer_dialog.py`, `viz/replay_recorder.py` — rendering and recording

## Dependencies

Standard library only (`dataclasses`). No third-party or PyQt dependencies.
