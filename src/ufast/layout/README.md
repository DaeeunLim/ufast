# src/ufast/layout — layout conversion

## Role

The **layout conversion layer** bridging the CAD/drawing world and the simulation world. `rail_manager.py` interprets the lines of the rail layers among the loaded `CLayer` figures as a graph, converts them into a network of `Section` objects (connectivity, length, buffers), and maps the drawing text to the nearest rail node to create `EQ`s.


## Files

| File | Key classes/functions | Role |
|---|---|---|
| `rail_manager.py` | `RailManager.build_layout(layers)` | Core conversion pipeline — (1) collect the `CLine`s of the rail layers (keep the existing layout if there are none) (2) build node_map by rounding coordinates (3) starting from degree≠2 junction/terminal nodes, merge consecutive segments into a single `Section` (4) link `next_sections`/`prev_sections` when the end-point distance is <1.0 (5) EQ binding |
| | `_bind_eq_from_text()` | Map the `CText` of every layer to the nearest rail node (`MAX_EQ_DISTANCE=5000.0`) → create `EQ` and register it with the Section |

## Data flow

- `main_ui`'s `load_rail_file()`/`load_layout_file()` (DXF) → `rail_manager.build_layout(layers)` → fills `SimulatorDataSet.sections`/`eq_list` → `viewer.draw_layout()`. "Convert to Rail" in the layer panel follows the same path.
- `RailManager` writes directly into the singleton without returning a value.

## Dependencies

Internal: `ufast.drawing.geometry`, `ufast.core.components`, `ufast.core.data_set`. No third-party dependencies.
