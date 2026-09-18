# U-FAST Project Structure

> U-FAST (Unified Fab-AMHS Simulation Toolkit) — a co-simulation toolkit that integrates production
> (fab) simulation with logistics (AMHS) simulation.
> This document summarises the role of each folder in the repository and how they relate to each
> other.

## At a glance

Standard src-layout: `src/ufast/` is the distributed package; data and generated output live in the
repository root.

```
repo/
├── pyproject.toml       # package definition (pip install -e .)
├── dataset/             # SMT2020 datasets + SMAT2022 rail layout + vehicle spec (about 31 MB)
├── docs/                # user documentation (this file, CLI guide, output file specification)
├── examples/            # 4 custom strategy plugin examples (templates for the four slots)
├── strategies/          # your own strategy files (.py/.pkl), found by bare name (git-ignored)
├── scripts/             # experiment batches, baseline benchmark, heatmap figures, engine check
├── tests/               # pytest suite
├── results/             # experiment results (generated, git-ignored)
├── logs/                # GUI run logs (generated, git-ignored)
├── external/            # (optional, not bundled) baseline simulator checkouts for scripts/compare_baselines.py
└── src/
    └── ufast/           # ★ distributed package (details below)
```

There are two execution modes:

- **CLI mode** — headless execution for experiments.
  Co-simulation `ufast-run` (`python -m ufast.cosim.run`) and logistics-only `ufast-fromto`
  (`python -m ufast.cosim.run_fromto`), plus `ufast-analyze` and `ufast-aggregate` for post-processing.
  `cosim → production + route(+bridge) → common/viz`
- **GUI mode** (`python -m ufast.main_ui`) — PyQt6 integrated UI (`pip install -e ".[gui]"`).
  `main_ui → gui/core/drawing/control/layout/integration → route`

## src/ufast/ subpackages

### Core (CLI path)

| Subpackage | Role |
|------|------|
| `ufast/cosim/` | **Co-simulation runner.** `run.py` is the main entry point (`ufast-run`). Couples the production DES and the AMHS event by event (`instance.py`), executes transports with the capacity-constrained queue model or the delay-based models (`amhs.py`), vehicle specification and kinematics (`vehicle.py`, `kinematics.py`), warm-up policy (`warmup.py`), result saving (`results.py`), comparison with literature reference values (`analyze.py`, `reference.py`), multi-seed aggregation (`aggregate.py`). The logistics-only runner (`run_fromto.py`, `fromto_amhs.py`, `heap_instance.py`) drives the same AMHS layer from a FromTo demand table. Details in `src/ufast/cosim/README.md` |
| `ufast/production/` | **Production simulator** — PySCFabSim-derived DES. Dataset loading (`read.py`), events/dispatching (`events.py`, `dispatching/`), machine and lot models. Details in `src/ufast/production/README.md` |
| `ufast/route/` | **AMHS route management** — Dijkstra search (+scipy C acceleration), congestion penalty, detour limits, co-sim bridge, assignment strategies. Details in `src/ufast/route/ROUTE_MANAGER.md` |
| `ufast/common/` | Shared utilities — KPI logger (`logger.py`, `equipment_kpi.py`), parsers (`fromto_parser.py`, `dxf_parser.py`, `rail_format.py`), input consistency check (`consistency.py`), **SMAT2022→.rail converter** (`smat2022_to_rail.py`), rail I/O (`rail_io.py`), custom strategy loader (`strategy_loader.py`) |
| `ufast/viz/` | Rerun-based visualisation — trajectory recording (`trajectory.py`), replay (`rerun_replay.py`), layout (`rerun_layout.py`), post-run report (`report.py`). Usable from the CLI via `--viz` (`pip install -e ".[viz]"`) |
| `ufast/paths.py` | Repository-relative path constants (`DATASET_DIR`, `RESULTS_DIR`) |

### GUI stack (optional, requires PyQt6)

| Subpackage | Role |
|------|------|
| `ufast/main_ui.py` | GUI main entry point (PyQt6 integrated UI) |
| `ufast/gui/` | GUI widgets — main viewer (`viewer.py`, including the live KPI panel), layer panel (`layer_dialog.py`) |
| `ufast/core/` | Simulation entities for the GUI (Section, OHT, EQ, buffers), event queue, global dataset |
| `ufast/drawing/` | CAD shape data model (`geometry.py` — CLine, CText, CLayer, etc.) |
| `ufast/control/` | Logistics event controller (`controllers.py`) — OHT state transitions, lot creation/delivery events |
| `ufast/layout/` | Rail drawing → Section conversion (`rail_manager.py`) |
| `ufast/integration/` | Running the production simulation and timeline view from the GUI (`production_runner.py`, `production_view.py`, `timeline.py`) |
| `ufast/verification/` | Post-run rule violation check (`run_verification()` — CSV report after the simulation ends, called by main_ui) |

Each subpackage has its own `README.md` (the route package uses `ROUTE_MANAGER.md`).

### Generated output (git-ignored)

| Folder | Content |
|------|------|
| `results/` | CLI run outputs: `results/<run_id>/` per run, `results/aggregate/` from `ufast-aggregate`, `results/baseline/` cache and `results/baseline_compare/` figures from `scripts/compare_baselines.py` |
| `logs/` | GUI run outputs (`logs/logistics/`, `logs/replay/`, `logs/production/`) and batch-script logs |

## scripts/ details

| Script | Role |
|------|------|
| `compare_baselines.py` | U-FAST vs baselines (PySCFabSim, LogiFabSim) benchmark under identical conditions. Baselines are not bundled; see `scripts/README.md` |
| `verify_fast_route.py` | Verifies that the C Dijkstra engine and the pure-Python implementation yield identical costs |
| `make_queue_blocking_heatmap.py` / `make_queue_occupancy_heatmap.py` | Per-section blocking / occupancy heatmaps of the queue model on the rail layout (matplotlib) |
| `exp5_queue_fleet_sweep.sh` | Example experiment batch: OHT fleet sweep × seeds with the queue model |

## Entry point summary

All commands are run **from the repository root**. After `pip install -e .` the `ufast-run`,
`ufast-fromto`, `ufast-analyze` and `ufast-aggregate` console commands are available; without
installing, use `PYTHONPATH=src python -m ufast.cosim.run` etc.

```bash
# CLI co-simulation
ufast-run dataset/HVLM dataset/SMAT2022.rail --days 60 --oht 100 --static-warmup-days 50

# Logistics-only run on the same AMHS layer
ufast-fromto dataset/case1.rail dataset/case1_Fromto.dat --oht 50 --duration 3600

# Post-processing
ufast-analyze                 # KPI report for the latest run
ufast-aggregate results/      # multi-seed mean/std by configuration

# Baseline comparison (needs external baseline checkouts)
python scripts/compare_baselines.py --days 365 --simulators pysc logi --datasets HVLM LVHM LVLM

# GUI
python -m ufast.main_ui
```

## Dependency direction between subpackages

```
cosim ──► production                 (drives the production DES)
cosim ──► route.bridge ──► route.pathfinder(+fast_pathfinder)
cosim ──► common (logger/KPI/consistency/strategy_loader), viz (--viz), paths
cosim.run_legacy ──► core, control   (legacy fromto engine only)
route ──► common.rail_format         (shared .rail parser — pure stdlib)
gui/control/integration ──► route, core, drawing, common
scripts/compare_baselines ──► ufast.cosim (subprocess) + external/*
```

There are no reverse dependencies (production and route know nothing about cosim). The CLI path
works without PyQt6; the GUI stack is only imported by `main_ui`.
