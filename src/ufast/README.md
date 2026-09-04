# U-FAST Integrated Simulator

A simulator that handles both the **AMHS layer (AMHS/OHT transport)** and the
**production layer (lot–machine scheduling)** of a semiconductor fab. It can be run in two ways.

- **CLI (primary)** — `python -m ufast.cosim.run` runs the integrated production + AMHS simulation.
  This is the main path for experiments and papers; see `ufast/cosim/README.md` for options and
  result formats.
- **GUI (secondary)** — `python -m ufast.main_ui` provides an OHT animation on top of the rail
  drawing plus a production dashboard. Use it to visually verify policies or for demos.

Both paths use **the same production engine (`production/`, a PySCFabSim fork)**.

---

## 1. Running the GUI

```bash
pip install -e ".[gui]"    # core + PyQt6; or PYTHONPATH=src
python3 -m ufast.main_ui   # from the repository root
```

The **Simulation** panel on the right is the control tower — ▶Run/■Stop at the top, the mode
selector, and the per-mode **Run Options** live in the panel (no dialog at Run time).
Options are locked while a run is in progress, and statistics/legends are shown in the
**Results** panel that appears when a run starts.

| Mode | Input | Execution |
|------|-------|-----------|
| **Logistics (AMHS)** | Layout (.dxf/.rail) + From-To file | Live (real-time animation) or Replay (record, then replay) |
| **Production (PySCFabSim)** | SMT2020-format dataset folder | Full-speed computation, then result tables |

The File menu is grouped by input type:
**Layout** (Load DXF/Rail, Save Rail) · **Flow Data** (Load FromTo) ·
**Production Data** (Add Dataset Folder…). Loaded inputs are always listed in the panel's
**Input Files** group. **Reset All** (⌘N) clears everything after confirmation.

### Logistics mode
1. File → Layout → Load Rail (or DXF) + Flow Data → Load FromTo
2. Choose the **Run mode** in Run Options:
   - **Live** — the simulation advances in real time and the OHTs/EQs move on screen.
     Adjust the speed factor with the Speed slider (0.1–20x) and scrub the past with the
     Timeline panel at the bottom. Intended for verifying entity behaviour (logic).
   - **Replay** — runs at full speed without animation while recording aggregates, and the
     **Rerun viewer** opens automatically at the end. The rail congestion heatmap, OHT/EQ
     states, and KPI time series are synchronized to the `sim_time` timeline and can be
     replayed with speed control and scrubbing. Intended for aggregate analysis — even
     long (1 year+) runs stay bounded by the frame cap (10,000).
3. Set the number of OHTs, run time, KPI-saving basis, etc. in the panel and press ▶ Run.
   The four custom strategies (.py/.pkl) are chosen via the "Custom strategies…" button.

### Production mode
1. File → Production Data → **Add Dataset Folder…** to add a dataset folder (required
   files are validated automatically). Production mode cannot be selected until a valid
   dataset exists.
2. Set days/Dispatcher/Algorithm/Seed in Run Options and press ▶ Run.
3. As soon as the computation finishes, the dashboard shows throughput, cycle time, and
   on-time rate per lot type together with machine utilization, and the results are saved
   under `logs/production/<timestamp>/`.

> The dataset folder must contain `tool.txt.1l`, `fromto.txt`, `order.txt`, `WIP.txt`,
> `part.txt`, `setup.txt`, `setupgrp.txt`, `downcal.txt`, `pmcal.txt`,
> `attach.txt`, and at least one `route_*.txt`.

---

## 2. Saved results

```
logs/
├── logistics/<timestamp>/    # AMHS Live runs — KPI / route / verification reports
├── replay/<timestamp>/       # AMHS Replay runs
│   ├── kpi_timeseries.parquet      # KPI time series
│   ├── section_congestion.parquet  # frame × section congestion (heatmap source)
│   └── replay.rrd                  # Rerun recording (reopen with `rerun replay.rrd`)
└── production/<timestamp>/   # production runs — summary.json + 4 CSV files
```

CLI experiments repeated with different seeds can be aggregated into per-configuration KPI
mean/std with `python3 -m ufast.cosim.aggregate`.

---

## 3. Timeline (Live mode)

- **While running in real time**: the progress bar follows the present; dragging it shows a
  past scene without stopping the simulation. "● Go LIVE" returns to the present.
- **After stop/completion**: ▶Play and the progress bar replay the recording like a video.
- In Replay mode the Rerun viewer takes over playback and speed control instead of this panel.

---

## 4. Directory structure

```
repo/
├── pyproject.toml           # pip install -e . (console commands: ufast-run/analyze/aggregate)
├── dataset/                 # rail (.rail), From-To, and SMT2020 datasets
├── results/                 # CLI experiment results (generated)
├── examples/                # custom strategy examples (.py)
└── src/
    └── ufast/               # ★ distributed package
        ├── main_ui.py       # GUI entry point (AMHS app logic + mode integration)
        ├── cosim/           # ★ CLI integrated runner + analysis (run/analyze/aggregate)
        ├── production/      # ★ production DES core (PySCFabSim fork) — shared by CLI and GUI
        ├── route/           # section-based routing/dispatch (U-FAST AMHS)
        ├── control/ core/ layout/  # AMHS simulator (From-To driven)
        ├── drawing/         # CAD figure model (geometry.py)
        ├── common/          # shared utilities: parsers, loggers, config
        ├── integration/     # GUI integration layer (timeline / production_runner / view)
        ├── gui/             # PyQt6 widgets (viewer / layer panel)
        ├── viz/             # Rerun visualization (layout, trajectory replay, GUI Replay recording)
        └── verification/    # result verification reports
```

> 2026-08-05: reorganized into the standard src-layout — every subpackage now lives under
> `src/ufast/`, and `dataset`/`results` moved to the repository root. The former `src/ufast`
> (runner) became `ufast/cosim/`.

---

## Acknowledgements

- **Production engine**: `production/` is a code fork of **PySCFabSim** (B. Kovács, P. Tassel).
  U-FAST extensions: transport interception hook (`_lot_ready_for_step`), equipment/node
  mapping, machine reservation.
- **Transport-time model**: `cosim/kinematics.py` is a reimplementation of the
  acceleration/deceleration transport-time model of **LogiFabSim** (Rank & Betker,
  2025 IFAC) (reimplemented following Rank & Betker, 2025), and the global TIP congestion
  factor form in `cosim/amhs.py` follows the same paper. The comparison reference values in
  `cosim/reference.py` come from the same source.
- **Datasets**: SMT2020 (production), SMAT2022 (layout/transport).
- **Visualization**: Rerun (rerun.io), PyQt6.
