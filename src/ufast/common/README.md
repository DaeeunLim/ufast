# src/ufast/common — Shared utilities

## Role

A collection of utilities reused throughout U-FAST. It covers layout/logistics input file parsers (.dxf, .rail, FromTo), simulation KPI collection and CSV logging, dynamic loading of custom strategy plugins, and the input consistency check run before every simulation. Both the simulation core (`production`, `cosim`, `control`) and the GUI depend on this folder.

## Files

| File | Key classes/functions | Role |
|---|---|---|
| `logger.py` | `SimulationLogger`, `get_logger()` | Accumulates Lot/OHT/EQ events received via callbacks; `save()` selectively writes summary/lot/oht/eq/supply-delay CSVs according to `kpi_mode` (logistics/production/both) and `simulator_mode` |
| `equipment_kpi.py` | `open_starvation`, `close_starvation`, `record_equipment_downtime`, `collect_equipment_kpis` | Records equipment starvation/breakdown/PM intervals; after clipping to the measurement window, aggregates per-family and overall KPIs (mean, p95, max, censored, union downtime) |
| `strategy_loader.py` | `load_strategy`, `invoke_*_strategy`, `normalize_*` | Dynamically loads user `.py` strategy files for 4 kinds (`routing`/`assignment`/`idle_positioning`/`routing_cost`), tries multiple call signatures, and normalizes return values to a standard form |
| `rail_format.py` | `parse_rail_file`, `RailData` | **Common `.rail` parser** (pure stdlib) — parses the file into a neutral record structure. Both `rail_io.py` and `route/rail_parser.py` consume its output |
| `rail_io.py` | `load_rail_file`, `save_rail_file` | `RailData` → `CLayer` for visualization + `SimulatorDataSet` Section/EQ construction (legacy path) |
| `smat2022_to_rail.py` | `convert(smat_dir, out_rail, out_map)` | SMAT2022 CSV (`Adress/Rail/Equipment.csv`) → U-FAST `.rail` converter. Remaps node names to integer IDs, merges junction-to-junction sections, picks a representative node per tool group + mapping report |
| `dxf_parser.py` | `DXFParser.parse` | AutoCAD DXF → converts LINE/ARC/CIRCLE/TEXT/BLOCK into `CLayer` geometry |
| `fromto_parser.py` | `load_fromto(filepath)`, `generate_fixed_interval_events()` | Tab-separated FromTo file → list of `(from_eq, to_eq, rate)` and fixed-interval (3600/rate s) event generation |
| `consistency.py` | `check_fromto`, `check_production_families`, `ConsistencyReport`, `ConsistencyError` | Input consistency check — FromTo equipment names ↔ `.rail` EQ list, dataset tool families ↔ rail destinations. Prints a `consistency[...]` summary; raises in `--strict` mode |
| `config_loader.py` | `ConfigLoader` (singleton) | Loads viewer appearance settings (colors, line widths) from an optional `config/settings.json` (not shipped), falling back to built-in defaults |

## Usage context

- `cosim/run.py`, `cosim/run_fromto.py`: `consistency`, `strategy_loader`, `equipment_kpi`, `fromto_parser`.
- `main_ui.py`: uses all parsers + logger + strategy_loader.
- The production core (`production/instance.py`, `events.py`) and `integration/production_runner.py`: call `equipment_kpi`.
- `control/controllers.py`, `cosim/amhs.py`: inject custom strategies via `strategy_loader`.
- The KPI tests under `tests/` unit-test these modules directly.
