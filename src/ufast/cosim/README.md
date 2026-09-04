# src/ufast/cosim — U-FAST co-simulation runner

## Role

`ufast.cosim` is the **integrated simulation runner that drives the production simulator (PySCFabSim-based `src/ufast/production`) and the logistics simulator (section-level AMHS) together on one event queue and one clock** (moved from the former `src/ufast` top level into the `ufast/cosim` subpackage in the 2026-08-05 src-layout reorganisation). Whenever the production layer issues a transport job between process steps, the AMHS layer physically executes that transport with a finite OHT pool as section-transition events and wakes the production layer through a completion callback. The same package also contains the headless execution path for `fromto.dat`-only scenarios without production data (legacy/fromto modes), plus result JSON/CSV saving, KPI analysis and Rerun visualisation integration.

## Files

| File | Key classes/functions | Role |
|---|---|---|
| `run.py` | `run_ufast(...)` | **Entry point for production mode.** Load rail/dataset → assemble RouteManager, SectionNodeBridge, AMHSExecutor and UFastInstance → run the dispatch loop → save/analyse/visualise results. The CLI is argparse-based (see `--help`): `--days --oht --seed --alpha --strategy --dispatcher --congestion --idle --routing --machine-selection --static-warmup-days --amhs-settling-days --viz` + custom strategy files `--custom-routing/--custom-assignment/--custom-idle` (.py/.pkl, same loader as the GUI Custom strategies) |
| `amhs.py` | `AMHSExecutor`, `OHT`, `TransportJob`, `SectionEnterEvent`, `ArriveEvent`, `RepositionTickEvent` | **Logistics core.** OHT assignment, section-sequence route computation, one event per section entry, traversal-time correction (slowdown) based on occupancy at entry time (`section_inflight`), idle repositioning, KPI snapshot / trip log collection |
| `instance.py` | `UFastInstance(FileInstance)`, `StaticTransportDoneEvent` | **Production↔AMHS seam.** Overrides `_lot_ready_for_step()` to delegate inter-step transport to `amhs.request_transport()`, and makes the lot dispatchable again in the delivery callback (`_on_delivered`). Destination machine selection (`machine_selection='exact'|'nearest'`); the route's static `transport_time` is replaced with 0 |
| `vehicle.py` | `VehicleSpec`, `load_vehicle_spec()`, `add_vehicle_args()`, `spec_from_args()` | **OHT vehicle specification** (length, minimum headway, speed, acceleration/deceleration, straight/curve speed limits). Priority: flags > `--vehicle-spec` file > `dataset/vehicle_spec.json` > built-in values. Shared by both runners; the values are recorded in the result JSON under `meta['vehicle']` |
| `kinematics.py` | `VehicleKinematics.edge_time()`, `.path_time()` | Free-flow transport time from OHT acceleration/deceleration (trapezoidal) kinematics. Reimplementation of the LogiFabSim transport-time model (reimplemented following Rank & Betker, 2025) — a module for aligning the baseline with the paper |
| `warmup.py` | `WarmupPolicy` (frozen dataclass) | Defines the static warm-up / AMHS settling periods. During warm-up the static transport distribution is used instead of AMHS, and the KPI measurement start time is set |
| `equipment_mapping.py` | `load_machine_equipment()`, `infer_layout_dir()` | Parses the equipment coordinates in `Equipment.csv`, builds a per-tool-group (STNFAM) equipment list, and attaches each piece of equipment to the nearest rail node → machine-level transport destinations |
| `results.py` | `collect_results()`, `save_results()`, `save_csv_exports()`, `auto_result_path()` | Aggregates run results into a KPI dict, saves JSON and exports 4 CSVs (`trips`/`kpi_timeseries`/`lots`/`machines`). Aggregation is per lot type and per category (Regular/Hot/SuperHot) |
| `analyze.py` | `analyze_one()`, `main()` | Reads a result JSON, prints a console KPI report and compares against LogiFabSim paper Tables 1/2/3. Run without arguments to auto-load the newest JSON under `results/` |
| `reference.py` | `PAPER_TABLE_1_HVLM`, `PAPER_TABLE_2_WALL_TIMES_1Y`, `PAPER_TABLE_3_HMLV_BASELINE` | Reference KPI values from the LogiFabSim paper (Rank & Betker, IFAC 2025) — the comparison baseline for `analyze.py` |
| `machine_activity.py` | `MachineActivityPlugin(IPlugin)` | Plugin recording machine busy periods `(start, end, family)` — for per-family load visualisation in Rerun replay |
| `heap_instance.py` | `HeapInstance` | Lightweight shim standing in for the event queue when AMHSExecutor runs without the production layer |
| `fromto_amhs.py` | `setup_fromto_amhs()` | Drives `fromto.dat` demand with the AMHSExecutor + HeapInstance combination. Fixed-interval (3600/rate s) schedule based on hourly demand rates |
| `run_fromto.py` | `run_fromto()`, `main()` | **Entry point for fromto (logistics-only) mode (`ufast-fromto`).** Connects a `.rail` + FromTo demand table to AMHSExecutor (blocking engine) via `setup_fromto_amhs()` and runs with the same options, KPIs, CSVs and Rerun as co-simulation |
| `run_legacy.py` | `run_legacy_fromto()` | Former fromto runner (`ufast-fromto --engine legacy`). Drives the legacy components (`common/rail_io`, `core/data_set`, `control/controllers`: constant speed 1 m/s, no blocking) in a pure next-event loop without PyQt timers — kept for comparison and regression |
| `legacy_trajectory.py` | `LegacyTrajectoryRecorder` | Observes the OHT states of the legacy `VehicleController` and converts them into the same `TrajectoryLog` format as production mode (shared Rerun replay) |

## Data flow

1. **Production mode**: `run_ufast()` → load dataset/rail → assemble `AMHSExecutor` + `UFastInstance`.
2. Main loop: repeat `instance.next_decision_point()` → `production.greedy.get_lots_to_dispatch_by_machine()` → `instance.dispatch()` — **production events and AMHS events consume the same queue**.
3. Seam: when a lot is ready for its next step, `_lot_ready_for_step()` → `request_transport()` → `SectionEnterEvent` chain → `ArriveEvent` → `_on_delivered()` → production resumes.
4. During the warm-up period AMHS is bypassed and static transport times are used via `StaticTransportDoneEvent`.
5. After completion: save `results` → `analyze` comparison report, and Rerun replay if `--viz`.

## External dependencies

- `src/ufast/production` — DES core, dispatchers, random source
- `src/ufast/route` — `RouteManager`, `SectionNodeBridge`, assignment strategies
- `src/ufast/common` — custom strategy loader, equipment KPIs, rail/fromto parsers
- `src/ufast/core`, `src/ufast/control` — legacy mode only
- `src/ufast/viz` — optional (lazy Rerun import)
- Third-party: standard library only. Callers are the CLI and `gui/cosim_dialog.py`
