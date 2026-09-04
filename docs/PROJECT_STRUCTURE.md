# U-FAST Project Structure

> U-FAST (Unified Fab-AMHS Simulation Toolkit) — a co-simulation toolkit that integrates production
> (fab) simulation with logistics (AMHS) simulation.
> This document summarises the role of each folder in the repository and how they relate to each
> other. (Written 2026-08-01; updated 2026-08-05 for the src-layout reorganisation.)

## At a glance

Standard src-layout: `src/ufast/` is the distributed package; data and generated output live in the
repository root.

```
repo/
├── pyproject.toml       # package definition (pip install -e .)
├── dataset/             # SMT2020 datasets + rail layouts (227MB, outside the package)
├── results/             # experiment results (generated, gitignored)
├── logs/                # experiment logs (generated, gitignored)
├── examples/            # 4 custom strategy plugin examples
├── scripts/             # experiment and operations scripts
├── tests/               # pytest suite
├── docs/                # user documentation (CLI guide, AMHS modes, structure)
├── external/            # (optional, not bundled) baseline simulator checkouts (PySCFabSim, LogiFabSim)
└── src/
    ├── ufast/           # ★ distributed package (details below)
```

There are two execution modes:

- **CLI co-sim mode** (`python -m ufast.cosim.run`) — headless execution for paper experiments.
  `cosim → production + route(+bridge) → common/viz`
- **GUI mode** (`python -m ufast.main_ui`) — PyQt6 integrated UI.
  `main_ui → gui/core/drawing/control/layout/integration → route`

## src/ufast/ subpackages

### Core (CLI co-sim path — released with the SoftwareX paper)

| Subpackage | Role |
|------|------|
| `ufast/cosim/` | **Co-simulation runner.** `run.py` is the main entry point (`ufast-run`). Couples the production DES and the AMHS event by event (`instance.py`), executes transports (`amhs.py`), vehicle kinematics (`kinematics.py`), warm-up policy (`warmup.py`), result saving (`results.py`), comparison with literature reference values (`analyze.py`, `reference.py`), multi-seed aggregation (`aggregate.py`) |
| `ufast/production/` | **Production simulator** — PySCFabSim-based DES. Dataset loading (`read.py`), events/dispatching (`events.py`, `dispatching/`), machine and lot models |
| `ufast/route/` | **AMHS route management** — Dijkstra search (+scipy C acceleration), congestion penalty, detour limits, co-sim bridge, assignment strategies. Details in `src/ufast/route/ROUTE_MANAGER.md` |
| `ufast/common/` | Shared utilities — KPI logger (`logger.py`, `equipment_kpi.py`), parsers (`fromto_parser.py`, `dxf_parser.py`), **SMAT2022→.rail converter** (`smat2022_to_rail.py`), rail I/O (`rail_io.py`), custom strategy loader (`strategy_loader.py`) |
| `ufast/viz/` | Rerun-based 3D visualisation — trajectory recording (`trajectory.py`), replay (`rerun_replay.py`), layout (`rerun_layout.py`), post-run report (`report.py`). Demo possible without the GUI via `--viz` |
| `ufast/paths.py` | Repository-relative path constants (`DATASET_DIR`, `RESULTS_DIR`) |

### GUI stack (candidate for exclusion in a CLI-only release)

| Subpackage | Role |
|------|------|
| `ufast/main_ui.py` | GUI main entry point (PyQt6 integrated UI) |
| `ufast/gui/` | GUI widgets — main viewer (`viewer.py`, including the live KPI panel), layer panel (`layer_dialog.py`) |
| `ufast/core/` | Simulation core for the GUI — components (Section, equipment, etc.), event queue, global dataset |
| `ufast/drawing/` | CAD shape data model (`geometry.py` — CLine, CText, CLayer, etc.) |
| `ufast/control/` | Logistics event controller (`controllers.py`) — OHT state transitions, lot creation/delivery events |
| `ufast/layout/` | Rail drawing → Section conversion (`rail_manager.py`) |
| `ufast/integration/` | Running the production simulation and timeline view from the GUI (`production_runner.py`, `production_view.py`, `timeline.py`) |
| `ufast/verification/` | Post-run rule violation check (`run_verification()` — CSV report after the simulation ends, called by main_ui) |

### Generated output and internal use (not released)

| Folder/file | Role | Notes |
|------|------|------|
| `results/` | Experiment results (timestamped folders, baseline_compare, etc.) | generated, gitignored |
| `logs/` | Experiment logs | generated, gitignored |

## scripts/ details

| Script | Role |
|------|------|
| `compare_baselines.py` | U-FAST vs baselines (PySCFabSim, LogiFabSim) benchmark under identical conditions — produces the wall-time figures for the paper |
| `verify_fast_route.py` | Verifies that the C Dijkstra engine and the pure-Python implementation yield identical costs |
| `exp*_*.sh` | Generate/run experiment batches |

## Entry point summary

All commands are run **from the repository root**. After `pip install -e .` they work without
`PYTHONPATH=src`, and the `ufast-run`/`ufast-analyze`/`ufast-aggregate` console commands become
available.

```bash
# CLI co-sim (paper experiments)
PYTHONPATH=src python -m ufast.cosim.run dataset/HVLM dataset/SMAT2022.rail --days 365 --oht 100 --static-warmup-days 50

# Baseline comparison
python scripts/compare_baselines.py --days 365 --simulators pysc logi --datasets HVLM LVHM LVLM

# GUI
PYTHONPATH=src python -m ufast.main_ui
```

## Dependency direction between subpackages

```
cosim ──► production          (drives the production DES)
cosim ──► route.bridge ──► route.pathfinder(+fast_pathfinder)
cosim ──► common (logger/KPI), viz (--viz), paths
route ──► common.rail_format (shared .rail parser — pure stdlib)
gui/control/integration ──► route, core, drawing, common
scripts/compare_baselines ──► ufast.cosim (subprocess) + external/*
```

There are no reverse dependencies (production and route know nothing about cosim). The CLI path
works independently even if the whole GUI stack is removed — which is what makes a CLI-centred
release possible.
