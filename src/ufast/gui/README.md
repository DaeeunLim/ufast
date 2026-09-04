# src/ufast/gui — PyQt6 widget/dialog layer

## Role

The package holding the **screen components (widgets, docks, dialogs)** of the U-FAST desktop GUI. The main window (`SimulationViewer`) draws the rail drawing into a QGraphicsScene and animates OHTs/EQs in real time. It consists of (1) the menu bar (File is grouped into Layout/Flow Data/Production Data), (2) the central stack (rail view / production dashboard), (3) the Layer panel (hidden by default, shown when a DXF is loaded), (4) the Simulation panel (▶Run/■Stop + mode + Input Files + per-mode resident Run Options + Speed), (5) the Timeline dock, and the Results dock (statistics/legends) that appears only during a run. It contains no simulation logic; it is a pure View layer that delegates to `main_ui.SimulationApp` through signals (`pyqtSignal`).

> Regular package (`__init__.py` present); modules are imported as `from ufast.gui.viewer import ...`.

## Files

| File | Key classes | Role |
|---|---|---|
| `viewer.py` | `SimulationViewer(QMainWindow)` | Main window — grouped menu bar, central `QStackedWidget` (0=rail view, 1=production dashboard), Simulation panel (resident Run Options — logistics: Run mode Live/Replay, number of OHTs, run time, KPI basis, strategy button / production: dataset, days, seed, etc.), Results dock, Timeline dock. Exposes options via `get_logistics_options()`/`get_production_options()` and delegates to the app logic through signals. EQ status colors come from the single source `EQ_STATUS_STYLES` |
| | `CADGraphicsView` | CAD viewport with wheel zoom / middle-button pan / mouse coordinate tracking |
| | `OHTItem` | Circular OHT item — linear/Bezier interpolation of the position along the section figure, snapshot restore (`apply_snapshot`) |
| | `update_statistics()` | Lot/OHT counts + live KPIs from `common.logger` (transport, delivery, call wait, rundown, WIP) |
| `layer_dialog.py` | `LayerPanel(QDockWidget)` | CAD layer table dock (Name/On/Rail). Layer names containing "RAIL" are checked automatically |


## Data flow

- The single entry point is the `SimulationViewer` created by `main_ui.SimulationApp`. Rendering data is not owned by the widgets; they pull from the `SimulatorDataSet.get_instance()` singleton every frame.
- For past playback, `render_logistics_snapshot()` is called → `_playback_mode=True` prevents clashes with the live animation.

## Dependencies

Internal: `core.data_set`, `drawing.geometry`, `core.components`, `integration.production_view` (try/except), `common.config_loader`, `common.logger`. Third-party: PyQt6.
