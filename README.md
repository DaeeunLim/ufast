# U-FAST — Unified Fab-AMHS Simulation Toolkit

U-FAST is an open research toolkit for **co-simulation of semiconductor fab production
scheduling and AMHS (OHT) material handling**. Instead of modeling transport as static
lookup times, U-FAST executes every lot movement with a finite fleet of overhead hoist
transport (OHT) vehicles on a section-based rail network — production and logistics
share a single event queue and a single clock, so congestion in the transport system
feeds back into production KPIs and vice versa.

## Key features

- **Integrated co-simulation** — a PySCFabSim-derived discrete-event production
  simulator dispatches lots; between process steps, transport jobs are executed
  physically by the AMHS layer (vehicle assignment, routing, section-by-section
  movement, delivery callback).
- **Section-based AMHS model** — rail layout parsed into a routed network
  (Dijkstra with optional SciPy C acceleration), per-section occupancy, four
  congestion treatments (`queue`, `section_local`, `global_tip`, `off`), vehicle
  acceleration/deceleration kinematics.
- **Two logistics fidelities** — the default is a *capacity-constrained
  queue* model (finite FIFO vehicle slots per section; entry blocks when the
  downstream section is full, with wait-for-graph deadlock detection), which
  also drives the GUI. A *delay-based* model (section occupancy inflates
  traversal time, no capacity limit) is available via
  `--congestion section_local`, making congestion fidelity a one-flag
  ablation (`off` / `global_tip` / `section_local` / `queue`). Both models
  share the same rail parser, routing, and dispatch strategies. The
  logistics-only mode (`ufast-fromto`) runs on this same AMHS layer.
- **Policy slots** — AMHS dispatch strategies (`fifo`, `nearest`, `same_section`,
  `congestion`), idle-vehicle repositioning, congestion-aware dynamic routing,
  destination machine selection, and **custom strategy plugins** loadable from
  user `.py`/`.pkl` files (`--custom-assignment/-routing/-idle/-routing-cost`;
  drop them in `strategies/`, templates in `examples/`).
- **Experiment workflow** — warm-up policies for steady-state measurement,
  JSON + CSV results export, KPI report with literature reference comparison,
  multi-seed aggregation (mean/std), and baseline benchmarking scripts.
- **Visualization** — [Rerun](https://rerun.io)-based replay of vehicle
  trajectories and rail-congestion heatmaps (`--viz`), post-run HTML report,
  plus an optional PyQt6 GUI with live animation and replay modes.

## Installation

Requires Python ≥ 3.10.

```bash
git clone https://github.com/DaeeunLim/ufast.git
cd ufast
pip install -e .            # core (CLI co-simulation)
pip install -e ".[viz]"     # + Rerun replay, charts, reports
pip install -e ".[gui]"     # + PyQt6 GUI
```

Editable install registers the console commands `ufast-run`, `ufast-fromto`,
`ufast-analyze`, and `ufast-aggregate`. Without installing, prefix commands with
`PYTHONPATH=src` and use `python -m ufast.cosim.run` etc. from the repository
root.

## Quick start

Run one simulated day of the SMT2020 HVLM fab with 100 OHTs on the SMAT2022 layout:

```bash
ufast-run dataset/HVLM dataset/SMAT2022.rail --days 1 --oht 100
```

A steady-state experiment with warm-up, congestion model, and visualization:

```bash
ufast-run dataset/HVLM dataset/SMAT2022.rail --days 60 --oht 100 \
    --static-warmup-days 50 --amhs-settling-days 5 \
    --congestion section_local --strategy nearest --viz
```

Results are written to `results/<run_id>/` as an aggregated KPI JSON plus raw CSV
exports (`trips`, `kpi_timeseries`, `lots`, `machines`). Then:

```bash
ufast-analyze                 # KPI report for the latest run (+ literature comparison)
ufast-aggregate results/      # multi-seed mean/std aggregation by configuration
```

Without production data, run the **same AMHS layer** (blocking model,
kinematics, strategies, KPI outputs) alone from a rail layout and a FromTo
demand table (`from_eq`, `to_eq`, jobs per hour):

```bash
ufast-fromto dataset/case1.rail dataset/case1_Fromto.dat --oht 50 --duration 3600
```

OHT vehicle parameters (speed, acceleration/deceleration, body length and
headway, straight/curve speed limits) default to
[dataset/vehicle_spec.json](dataset/vehicle_spec.json) (the SMAT2022 vehicle)
and can be overridden per run with `--vehicle-spec PATH` or the
`--oht-speed/--oht-accel/--oht-decel/--oht-length/--oht-headway/--line-speed/--curve-speed`
flags in both modes; the values used are recorded in the result JSON.

Both runners validate the inputs against each other before simulating (fromto
equipment ↔ rail EQ list; dataset tool families ↔ rail destinations) and print a
`consistency[...]` summary. Unmatched names are skipped with a warning by
default; add `--strict` to abort instead.

See `ufast-run --help` for all options (dispatch rules, congestion models, idle
repositioning, dynamic routing, custom strategy plugins),
[docs/cli_guide.md](docs/cli_guide.md) for a full guide, and
[docs/output_files.md](docs/output_files.md) for the result files and their fields. The GUI is started with
`python -m ufast.main_ui`.

## Repository structure

```
pyproject.toml        package definition (src-layout)
dataset/              SMT2020 production datasets (HVLM/LVHM/LVLM) + SMAT2022 rail layout
examples/             custom strategy plugin examples (templates for the four slots)
strategies/           your own strategy files (.py/.pkl) — found by bare name
scripts/              experiment batches, baseline benchmark, remote-run helpers
tests/                pytest suite (incl. zero-day co-simulation smoke test)
results/, logs/       run outputs (generated, git-ignored)
src/ufast/            the Python package
├── cosim/            co-simulation runner: run / amhs / instance / results / analyze
├── production/       production DES (derived from PySCFabSim)
├── route/            rail network, Dijkstra routing, congestion, dispatch strategies
├── common/           parsers, KPI logging, strategy loader, SMAT2022→.rail converter
├── viz/              Rerun visualization, trajectory replay, reports
└── gui/, core/, drawing/, control/, layout/, integration/, verification/   GUI stack
```

Run the test suite with `python -m pytest tests/`.

## Provenance and citations

U-FAST builds on prior open research software, which we gratefully acknowledge:

- **Production layer** (`src/ufast/production/`): derived from
  **PySCFabSim** — B. Kovács, P. Tassel, R. Ali, M. El-Kholany, M. Gebser,
  G. Seidel, *"A customizable open-source simulator for semiconductor fab
  scheduling research"*, ASMC 2022.
  U-FAST extensions: transport interception hooks, equipment/node mapping,
  machine reservation, equipment KPIs.
- **Vehicle kinematics** (`src/ufast/cosim/kinematics.py`): reimplemented
  following the edge travel-time model of **LogiFabSim** — S. Rank, V. Betker,
  *"Comprehensive Simulation of Semiconductor Production: LogiFabSim —
  Integrating Dynamic Transport Times in Production Planning"*,
  IFAC-PapersOnLine, 2025. The global-TIP congestion factor variant and the
  reference KPI tables used by `ufast-analyze` also follow this paper.
- **Datasets**: SMT2020 semiconductor manufacturing testbed — D. Kopp,
  M. Hassoun, A. Kalir, L. Mönch, *"SMT2020 — A Semiconductor Manufacturing
  Testbed"*, IEEE Transactions on Semiconductor Manufacturing, 2020.
  SMAT2022 fab layout (rail network and equipment coordinates) as distributed
  with LogiFabSim.

The section-based AMHS logistics layer, the co-simulation seam, and the policy
plugin architecture are original contributions of U-FAST.

A software paper describing U-FAST has been submitted to *SoftwareX*; the
citation entry (see also [CITATION.cff](CITATION.cff)) will be updated upon
publication.

## License

U-FAST is released under the **MIT License** (see [LICENSE.txt](LICENSE.txt)).
The production layer derives from
[PySCFabSim](https://github.com/prosysscience/PySCFabSim-release), which is
likewise MIT-licensed; its copyright notice is retained in the third-party
notices section of LICENSE.txt.

## Contact

Daeeun Lim — daeeun.lim@gmail.com
