# src/ufast/integration — production simulation integration and timeline replay

## Role

Layer that **integrates the AMHS simulator and the production simulator in a single GUI**. It consists of the "video record/replay" engine shared by both modes (`TimelineRecorder`), an adapter that drives the production greedy loop in a background QThread while filling snapshots (`ProductionRunner`), and a dashboard widget dedicated to production metrics (`ProductionDashboard`).

## Files

| File | Key classes | Role |
|---|---|---|
| `timeline.py` | `Snapshot` | Minimal state to restore one moment — `sim_time`, `mode`, OHT/EQ status dicts, common metrics |
| | `TimelineRecorder` | Snapshot store in ascending sim-time order — `maybe_record()` (records only once the interval has elapsed), `at_fraction()` (bisect lookup). Pure Python, no PyQt dependency |
| `production_runner.py` | `ProductionParams` | Production run parameters (dataset, days, dispatcher, congestion_factor, ...) |
| | `ProductionRunner(QThread)` | Runs the production greedy loop to completion at full speed in the background while filling the timeline. `progress`/`results_ready`/`failed` signals, `request_stop()` |
| | `_build_result_summary()` | Throughput/CT/on-time/tardiness per lot type, utilisation/starvation/breakdown/PM per family, WIP/downtime summary |
| `production_view.py` | `ProductionDashboard(QWidget)` | Central screen in production mode — Lot Metrics grid, two gauges, result summary (two tables) |

## Data flow

- **AMHS mode**: `SimulationThread` records a snapshot every step via `timeline_recorder.maybe_record()` -> dragging the progress bar calls `at_fraction()` -> `viewer.render_logistics_snapshot()`.
- **Production mode**: `start_production_simulation()` -> `ProductionRunner` starts -> on completion `results_ready(summary)` -> dashboard refresh; subsequent scrubbing goes through `dashboard.set_snapshot()`.
- `ProductionDashboard` is inserted directly into the central stack by `gui.viewer` — no back-reference from integration to gui (one-way).

## External dependencies

Internal: **`ufast.production`** (greedy, dispatcher, file_instance, plugins), `ufast.common.equipment_kpi`. Third-party: PyQt6 (only `timeline.py` is fully independent).
