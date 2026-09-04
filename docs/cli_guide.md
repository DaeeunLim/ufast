# U-FAST (Unified Fab-AMHS Simulation Toolkit) CLI Execution Guide

This document is a detailed guide to running the U-FAST semiconductor co-simulation simulator in
CLI (Command Line Interface) mode and to its main options.

U-FAST is a co-simulation engine that couples a production simulator (PySCFabSim) with a physical
logistics simulator (AMHS). It provides the following three main CLI scripts.

1. **Integrated co-simulation runner (`python -m ufast.cosim.run` / `ufast-run`):** runs a coupled
   production + logistics (AMHS) simulation on a single production dataset and rail layout and
   writes the analysis output.
2. **Fromto (logistics-only) runner (`python -m ufast.cosim.run_fromto` / `ufast-fromto`):** runs
   the **same AMHS layer** as the co-simulation (blocking model, kinematics, strategies, KPIs) from a
   rail layout + fromto.dat (transport demand table) only, without production data.
3. **Baseline comparison and caching script (`scripts/compare_baselines.py`):** compares the
   production KPIs (cycle time, throughput, on-time delivery rate, etc.) of the three simulators
   PySCFabSim, LogiFabSim and U-FAST, and caches results automatically to make large-scale
   simulation campaigns efficient.

---

## 1. Co-simulation runner (`python -m ufast.cosim.run`)

The main script: it integrates the production process with physical AMHS logistics and simulates
the actual vehicle trajectories and control strategies.

### Command structure
```bash
PYTHONPATH=src python -m ufast.cosim.run [dataset_dir] [rail_file] [options]
```

### Positional Arguments
* **`dataset_dir`**: path to the production dataset folder (default: `dataset/HVLM`)
  * Examples: `dataset/HVLM`, `dataset/LVHM`, `dataset/LVLM`
* **`rail_file`**: path to the logistics rail layout file (default: `dataset/SMAT2022.rail`)
  * Examples: `dataset/SMAT2022.rail`, `case1_SMT2020_106_nospur.rail`

### Detailed CLI Options

| Option | Type | Choices / format | Default | Description and effect |
| :--- | :--- | :--- | :--- | :--- |
| `--days` | `int` | positive integer | `1` | **Simulation horizon** (days). Longer horizons let the WIP state settle but increase computation time. |
| `--oht` | `int` | positive integer | `200` | **Number of OHT vehicles in the AMHS** (fleet size). Too few vehicles create a logistics bottleneck; too many sharply increase rail congestion. |
| `--seed` | `int` | integer | `0` | **Random seed**. Ensures reproducibility so that the same conditions always produce the same simulation result. |
| `--alpha` | `float` | `0.0` ~ `1.0` | `0.05` | **Congestion weight ($\alpha$)**. Penalty coefficient added to the edge weight per vehicle ahead during congestion-aware shortest-path search. |
| `--dispatcher` | `str` | `fifo`<br>`cr`<br>`random` | `fifo` | **Production dispatching rule**. Decision rule that picks the next lot to process from a tool queue.<br>• `fifo`: first-in first-out<br>• `cr`: Critical Ratio (urgency first)<br>• `edd`: Earliest Due Date<br>• `setup_avoidance`: minimises setup changes |
| `--strategy` | `str` | `fifo`<br>`nearest`<br>`same_section`<br>`congestion` | `fifo` | **AMHS OHT assignment strategy**. Decides how idle OHTs are matched to lots that issued a loading request.<br>• `fifo`: match in request order<br>• `nearest`: prefer the closest vehicle |
| `--congestion` | `str` | `queue`<br>`section_local`<br>`global_tip`<br>`off` | `queue` | **Logistics congestion model**.<br>• `queue` (**default**): **capacity-constrained blocking model** — each section has a finite number of FIFO slots (⌊section length / vehicle footprint⌋); entry into a full section is blocked and the vehicle waits. Deadlocks are resolved by wait-for cycle detection followed by forced entry, and the `blocked_events`/`blocked_time_s`/`deadlock_forced` metrics are added to the result (α unused).<br>• `section_local`: delay-based alternative — when several OHTs travel in the same section the travel time is inflated in proportion to occupancy (uses α).<br>• `global_tip`: LogiFabSim-style global TIP-proportional slowdown (reproduced for comparison).<br>• `off`: no congestion (free-flow). |
| `--idle` | `str` | `off`<br>`on` | `off` | **Idle OHT repositioning strategy**.<br>• `off`: idle vehicles wait at their destination<br>• `reposition`: idle vehicles are moved in advance to areas where demand is expected to be high. |
| `--routing` | `str` | `off`<br>`dynamic` | `off` | **OHT route search mode**.<br>• `off` / `static`: drive along the static shortest (free-flow) route<br>• `dynamic`: dynamic Dijkstra re-routing that reflects real-time rail congestion (including the α penalty) |
| `--machine-selection` | `str` | `exact`<br>`nearest` | `exact` | **Candidate matching mode for the next processing tool**.<br>• `exact` (default): computes a precise physical Dijkstra logistics route for the top 8 candidate tools and selects the one reachable in the shortest time.<br>• `nearest` (recommended): approximates the selection with the Euclidean (straight-line) distance instead of a physical route search. **(Cuts computation time by roughly 5x or more.)** |
| `--static-warmup-days` | `float` | positive real | `0.0` | **Static warm-up days**. During the initial backlog and warm-up period the physical AMHS model is skipped and a static transport-time constant is applied, so the heavily loaded warm-up phase passes almost instantly. |
| `--amhs-settling-days` | `float` | positive real | `0.0` | **AMHS adaptation/settling days**. Grace period after the static warm-up and before statistics collection starts, during which the real AMHS (OHT driving) runs so that the logistics flow settles. |
| `--custom-assignment` / `--custom-routing` / `--custom-idle` | `path` | `.py` / `.pkl` | none | **Custom strategy plugins** — replace the OHT assignment / routing / idle repositioning slot with a user file. Returning `None` falls back to the built-in rule. A bare file name is looked up in the `strategies/` folder. Conventions and examples: [strategies/README.md](../strategies/README.md), `examples/` |
| `--custom-routing-cost` | `path` | `.py` / `.pkl` | none | **Route-search edge cost function** plugin (`compute_cost(...)`). When set, the C-accelerated path search is disabled and the pure-Python path is used. The file names of all four applied plugins are recorded in the result JSON under `meta.custom_strategies` |
| `--viz` | `flag` | enabled when `--viz` is given | `False` | **Rerun visualisation data flag**. When enabled, generates the simulation driving and machine-state trajectory data (`_trajectories.json`) that can be replayed in the Rerun viewer, and automatically opens the Rerun browser for visualisation when the simulation ends. |
| `--strict` | `flag` | enabled when `--strict` is given | `False` | **Strict input consistency mode**. Aborts the run if a tool family (STNFAM) in the dataset cannot be resolved to a rail destination (eq_to_node or Equipment.csv). The default behaviour prints a warning and skips transports of that family (counted in `skipped_transport`). Delay virtual stations (STNFAMLOC=Delay) are excluded from the check. |
| `--vehicle-spec` | `path` | `.json` or SMAT2022 `VehicleType.csv` | `dataset/vehicle_spec.json` | **OHT vehicle specification file**. Speed, acceleration/deceleration, vehicle length, minimum headway, straight/curve speed limits. Default file values = SMAT2022 OHTV0. The flags below override the file values. |
| `--oht-speed` / `--oht-accel` / `--oht-decel` | `float` | m/s, m/s², m/s² | file values (5 / 2 / 3.5) | Maximum speed, acceleration, deceleration (used in the trapezoidal-kinematics free-flow time) |
| `--oht-length` / `--oht-headway` | `float` | mm | file values (784 / 125) | Vehicle length and minimum headway. Their sum (footprint, default 909 mm) determines the section capacity of the `queue` model = max(1, ⌊section length / footprint⌋) |
| `--line-speed` / `--curve-speed` | `float` | m/s | file values (5 / 1) | Speed limits on straight/curved links |

### Output files

See [output_files.md](output_files.md) for the field-level specification. Every run creates a new
`results/<run_id>/` directory (`run_id` = `YYYY-MM-DD_HH-MM-SS_microseconds`). Previous runs are
never overwritten, and all files in one directory share the same base name.

**Base name (co-simulation)**

```
<dataset>_<days>d_<OHT>oht_a<alpha>_<production dispatcher>_<AMHS strategy>_<congestion>[extra options]_s<seed>
e.g. HVLM_180d_15oht_a0.05_fifo_fifo_queue_sw120_as10_s0
```

Congestion abbreviations: `queue` as is, `section_local`→`sl`, `global_tip`→`gt`, `off`→`no`. Extra
options are appended only when they differ from the default: `_idleon`, `_rdyn` (dynamic routing),
`_ms<machine-selection>`, `_sw<days>` (static warm-up), `_as<days>` (AMHS settling).

**Base name (logistics-only)**: `fromto_<rail>_<FromTo>_<seconds>s_<OHT>oht_<strategy>_<congestion>_s<seed>`
(e.g. `fromto_case1_case1_Fromto_3600s_50oht_nearest_queue_s0`).

| File | Created when | Content |
|---|---|---|
| `<name>.json` | always | KPI summary + run metadata (table below) |
| `<name>_lots.csv` | always (co-simulation) | One row per completed lot: `lot_name, release_at, done_at, deadline_at, cycle_time_days, tardiness_s, waiting_s, processing_s, transport_s, on_time`. Times are in sim seconds. Source for measurement-window filtering (e.g. `done_at ≥ 130 d`) and quantile analysis |
| `<name>_trips.csv` | `--viz` | One row per OHT transport: `oht_id, request_time, assignment_time, delivery_time, empty_duration, loaded_duration, total_duration, congestion, empty_path_len, loaded_path_len` |
| `<name>_kpi_timeseries.csv` | `--viz` | Periodic snapshots: `sim_time, busy_oht, section_inflight_sum, pending_queue, cumulative_delivered, max_section_inflight` |
| `<name>_machines.csv` | `--viz` (co-simulation) | Tool busy intervals: `family, start_time, end_time, duration_s` (for Gantt/load visualisation) |
| `<name>_trajectories.json` | `--viz` | Rerun replay log bundling the trips, snapshots and tool intervals above with the initial OHT positions (`ufast.viz.rerun_replay.show_run`) |
| `results/aggregate/aggregate.{json,csv}` | `ufast-aggregate results/` | Multi-seed `mean/std/min/max` table grouped by configuration (base file name without `_s<seed>`) |

**JSON blocks** (co-simulation; logistics-only has a `transport` block instead of
`production`/`equipment`)

| Block | Main keys |
|---|---|
| `meta` | `mode` (production/fromto), `run_id`, input paths (`dataset_dir`, `rail_file`), `days`/`sim_duration_s`, `num_oht`, `seed`, all policy slots (`dispatcher`, `amhs_strategy`, `congestion_model`, `alpha`, `idle_positioning`, `routing_model`, `machine_selection`), warm-up (`static_warmup_days`, `amhs_settling_days`, `measurement_start_days`), **`vehicle`** (length, headway, footprint, speed, accel/decel, straight/curve speed limits, source), `wall_time_s`, `dispatch_steps`. This block alone is enough to reproduce the same run |
| `production` | `completed` (**cumulative over the whole run** — for measurement-window statistics use `done_at` in `_lots.csv`), `active`, transport counts (`transport_count`, `static_transport_count`, `skipped_transport`, `same_node_transport`), `by_lot_type`·`by_category` (throughput, mean/median cycle time, on-time, waiting/processing/transport time per Regular/Hot/SuperHot) |
| `equipment` | measurement window (`measurement_start_s`~`_end_s`), tool count, `busy_time_s`, `setup_time_s`, `utilization_pct`, starvation total/count/quantiles, **`transport_blocked_starvation_s/_pct`** (starvation that would not have occurred with instantaneous transport) vs `upstream_starvation_s`, breakdown/PM time, per-`by_family` breakdown |
| `amhs` | `total_requested_jobs`, `total_jobs`, `avg_free_flow_s`, `avg_transport_s`, `avg_empty_travel_s`, `avg_loaded_travel_s`, `avg_delivery_s` (request→delivery), `avg_oht_wait_s`, `delivery_/transport_{p50,p95,p99,max}_s`, `avg_congestion_factor`, `avg_utilization`, `max_queue`, `max_node_inflight`, queue-model-only `blocked_events`, `blocked_time_s`, `deadlock_forced`, per-section `blocked_by_section`·`blocked_time_by_section` (blocking heatmap data), `reposition_count` |
| `transport` (logistics-only) | `lots_generated` (number of requests), `lots_completed`, `completion_rate`, `oht_count`, `oht_final_status` |

`ufast-analyze [json]` reads this JSON and prints a console report (including a comparison with
literature reference values). The raw values of the per-section **occupancy** heatmap
(`*_sections.csv`) are not a runner output; they are produced by
`scripts/make_queue_occupancy_heatmap.py`, which performs an instrumented run.

---

## 2. Fromto (logistics-only) runner (`python -m ufast.cosim.run_fromto` / `ufast-fromto`)

For input scenarios without production data (SMT2020 route/order/tool), runs the AMHS logistics
simulation from the rail layout and fromto.dat (transport demand table) only. It uses the **same
`AMHSExecutor`** as the co-simulation runner, so capacity-constrained blocking (queue), deadlock
resolution, kinematic free-flow, assignment/routing/idle strategies, custom plugins, KPI JSON/CSV and
Rerun replay are identical. Only the event queue is handled by `HeapInstance` instead of the
production layer.

### Command structure
```bash
PYTHONPATH=src python -m ufast.cosim.run_fromto [rail_file] [fromto_file] [options]
```

### Positional Arguments
* **`rail_file`**: `.rail` layout file (default: `dataset/case1.rail`)
* **`fromto_file`**: `fromto.dat` — tab-separated text, each row `from_eq \t to_eq \t rate_per_hour`
  (the 3rd column is the rate per hour [events/hour]; row order is the order of time periods;
  events are generated at a fixed interval of `3600/rate` seconds) (default:
  `dataset/case1_Fromto.dat`)

### Detailed CLI Options

| Option | Type | Choices / format | Default | Description and effect |
| :--- | :--- | :--- | :--- | :--- |
| `--oht` | `int` | positive integer | `50` | Number of OHT vehicles (fleet size) |
| `--duration` | `float` | positive real | `3600.0` | Simulation end time (sim seconds) |
| `--seed` | `int` | integer | `0` | Random seed |
| `--strategy` | `str` | `fifo`<br>`nearest`<br>`same_section`<br>`congestion` | `nearest` | OHT assignment strategy (same as co-simulation) |
| `--congestion` | `str` | `queue`<br>`section_local`<br>`global_tip`<br>`off` | `queue` | Congestion model — same as §1 |
| `--alpha` | `float` | real | `0.05` | Congestion coefficient α of the delay models |
| `--idle` / `--routing` | `str` | `off`/`on`, `off`/`dynamic` | `off` | Idle repositioning, congestion-reactive re-routing — same as §1 |
| `--custom-routing` / `--custom-assignment` / `--custom-idle` / `--custom-routing-cost` | `path` | `.py` / `.pkl` | none | Custom strategy plugins — same as §1. A bare file name is looked up in `strategies/` |
| `--vehicle-spec` | `path` | `.json` or SMAT2022 `VehicleType.csv` | `dataset/vehicle_spec.json` | **OHT vehicle specification file**. Speed, acceleration/deceleration, vehicle length, minimum headway, straight/curve speed limits. Default file values = SMAT2022 OHTV0. The flags below override the file values. |
| `--oht-speed` / `--oht-accel` / `--oht-decel` | `float` | m/s, m/s², m/s² | file values (5 / 2 / 3.5) | Maximum speed, acceleration, deceleration (used in the trapezoidal-kinematics free-flow time) |
| `--oht-length` / `--oht-headway` | `float` | mm | file values (784 / 125) | Vehicle length and minimum headway. Their sum (footprint, default 909 mm) determines the section capacity of the `queue` model = max(1, ⌊section length / footprint⌋) |
| `--line-speed` / `--curve-speed` | `float` | m/s | file values (5 / 1) | Speed limits on straight/curved links |
| `--strict` | `flag` | enabled when `--strict` is given | `False` | **Strict input consistency mode**. Aborts the run if fromto.dat references equipment that is not in the EQ list of the rail layout. The default behaviour prints a summary of unmatched names as a warning and excludes those records from event generation. |
| `--viz` | `flag` | enabled when `--viz` is given | `False` | Generate and replay Rerun visualisation data |
| `--engine` | `str` | `amhs`<br>`legacy` | `amhs` | `legacy` is the previous GUI-controller-based runner (`run_legacy.py`: constant speed 1 m/s, no blocking) — for comparison and regression |

### Input consistency (Data Consistency)
Both runners check name matching between inputs before the simulation starts and print a one-line
`consistency[...]` summary.
* **fromto mode**: from/to equipment names in fromto.dat ↔ EQ list of the `.rail` (exact match)
* **production mode**: STNFAM in tool.txt ↔ eq_to_node of the `.rail` (case-insensitive) or the
  `Equipment.csv` mapping
* Unmatched entries are **skipped with a warning** by default (results may be distorted); with
  `--strict` the run aborts immediately with exit code 1.

---

## 3. Baseline comparison and caching script (`scripts/compare_baselines.py`)

Experiment automation script that compares the results of PySCFabSim (production only),
LogiFabSim (production + simple queueing logistics) and U-FAST (production + physical AMHS), and
batch-produces 3-way KPI plots and a performance summary report.

### Command structure
```bash
python scripts/compare_baselines.py [options]
```

### Detailed CLI Options

| Option | Type | Choices / format | Default | Description and effect |
| :--- | :--- | :--- | :--- | :--- |
| `--days` | `int` | positive integer | `30` | **Simulation horizon**. Reference horizon for baseline generation and U-FAST runs. |
| `--datasets` | `list` | `HVLM` / `LVHM` / `LVLM`<br>(multiple allowed) | `["HVLM", "LVHM", "LVLM"]` | **Production datasets to experiment on**. Several may be given separated by spaces (e.g. `--datasets HVLM LVLM`). |
| `--seed` | `int` | integer | `0` | Seed for random number generation. |
| `--dispatcher` | `str` | `fifo` / `random` / `cr` etc. | `fifo` | Production dispatching rule applied to the simulators. |
| `--alg` | `str` | `l4m`<br>`m4l` | `l4m` | **Lot allocation algorithm**.<br>• `l4m` (default): tool-based matching (Lot-for-Machine)<br>• `m4l`: lot-based matching (Machine-for-Lot) |
| `--oht` | `int` | positive integer | `100` | Number of OHTs deployed when running the U-FAST co-sim. |
| `--simulators` | `list` | `pysc` / `logi` / `fills`<br>(multiple allowed) | `["pysc", "logi", "fills"]` | **Simulators to evaluate and compare**. When analysing cached data you can select only specific simulators to control the run time. |
| `--logi-cf` | `list` | `none` / `flat` / `linear` / `exp` | `["none"]` | List of LogiFabSim logistics congestion factor (CF) options. |
| `--out-dir` | `str` | folder path | `results/baseline_compare` | Directory where the plots and summary tables (`.csv`, `.png`) are stored. |
| `--summarize-only` | `flag` | enabled when `--summarize-only` is given | `False` | **Report regeneration mode**. Use when you only want to rebuild the plots and summary report from the per-simulator result JSON files already stored in `--out-dir`, without re-running the simulations. |
| `--baseline-python` | `str` | executable path | `None` | **Python interpreter used for the baseline simulators only**. A separate interpreter (e.g. the path to a `PyPy3` binary) can be given for baseline performance comparison or fast measurement. If not given, the baselines run with the same Python as U-FAST. |
| `--fills-source` | `str` | `run`<br>`load` | `run` | **How U-FAST results are obtained**.<br>• `run`: run a fresh U-FAST simulation live and compare.<br>• `load`: load U-FAST result files from a previous run present in the cache or output path. |
| `--warmup-days` | `float` | positive real | `0.0` | **Period excluded from statistics** (days). Filters out the unstable lot-completion statistics of the initial warm-up period so that only steady-state data enter the KPIs. |
| `--static-warmup-days` | `float` | positive real | `0.0` | Static warm-up days passed to the U-FAST co-sim run. |
| `--amhs-settling-days` | `float` | positive real | `0.0` | AMHS settling days passed to the U-FAST co-sim run. |
| `--machine-selection` | `str` | `exact`<br>`nearest` | `exact` | Tool selection mode passed to the U-FAST co-sim run. |
| `--mode` | `str` | `compare`<br>`sweep` | `compare` | **Operating mode**.<br>• `compare`: performs the 3-way KPI comparison and draws the performance tables and plots.<br>• `sweep`: runs the fleet-size sweep over the number of OHTs and produces the Logi CF overlay plot. |
| `--oht-list` | `list` | list of integers | `[20, 30, 50, 100, 200]` | List of OHT fleet sizes to test with U-FAST in `sweep` mode. |

---

### 💡 How the baseline cache works

`PySCFabSim` and `LogiFabSim` have static logistics flows or a fixed emulation method on every run,
so with the same random seed and horizon they guarantee fully deterministic results.

To avoid wasting simulation time, the following caching policy is therefore applied.
1. Before running a simulation, `compare_baselines.py` scans the `results/baseline/` directory.
2. If a cache file of the form `[simulator]_[dataset]_[days]d_s[seed]_[cf].json` exists, **the
   simulator is not run and the cached data are restored immediately**.
3. On restore, statistics-exclusion periods passed on the CLI such as `--warmup-days` are
   re-aggregated on the fly, so the aggregation window can be adjusted flexibly even with a cache.
4. If no cache exists, the full simulation is run once and written to the cache directory
   automatically for reuse from the next run on.

> [!TIP]
> **Using a large baseline cache:**
> Build the long-horizon (e.g. 365-day) baseline data once in advance; then, while rapidly tuning
> U-FAST control strategies (`--machine-selection nearest` etc.), the baseline comparison plots can
> be produced in about one second each time.

> [!TIP]
> **Recommended optimisation combination for practice and research (speed + physical fidelity):**
> The most practical baseline combination that gives both **"speed and realistic physics"** for
> industrial and research use is `--machine-selection exact` + `--routing off`. Tuning under this
> combination yields excellent reliability and execution speed at the same time.
>
> **💡 Routing options and how real-time congestion is reflected:**
> In the U-FAST simulator, route selection and the physical driving simulation operate
> independently. Therefore, even with `--routing off`, slowdown penalties caused by real-time
> congestion are still applied normally.
> * **1. What is reflected in real time (physical travel-time delay):**
>   With the `--congestion section_local` (default) model, every time an OHT passes through a rail
>   section, the real-time number of other OHTs travelling in that section is checked. If vehicles
>   ahead are backed up and there are 4 OHTs in the section, the speed is reduced according to the
>   traversal-time formula `Base time * (1 + alpha * 4)`, so **real-time congestion delay occurs
>   physically as expected**.
>   Thus even when tools are chosen with `exact`, the decision is based on "the accel/decel travel
>   time along the static shortest route to that tool", but once the vehicle departs, delay
>   accumulates in real time according to the situation on the rail.
> * **2. What is not reflected (real-time detour re-routing around congestion):**
>   * `--routing off` (recommended): the vehicle always drives the fixed static shortest route from
>     origin to destination. Even with 10 vehicles backed up ahead it "does not detour and keeps
>     following that route" (though it suffers a large slowdown delay under rule 1). ➡️ *Closest to
>     real fab logistics control, and very fast to compute.*
>   * `--routing dynamic`: whenever the vehicle searches for a route it looks at the current rail
>     state and, if a section is jammed, takes a somewhat longer but clear detour — a fresh
>     congestion-aware Dijkstra search every time. This detour search is computationally very
>     expensive, so the run time grows dramatically.

---

## 4. Frequently used execution scenarios (Recipes)

### 🚀 Scenario A: 1-day co-simulation demo with Rerun visualisation
The quickest way to see the co-simulation running and replay the OHT movements on the rail in
3D/2D.
```bash
PYTHONPATH=src python -m ufast.cosim.run dataset/HVLM dataset/SMAT2022.rail --days 1 --oht 150 --viz
```

### ⚡ Scenario B: very fast U-FAST simulation (warm-up optimisation + nearest selection)
Runs a 60-day long simulation while accelerating the warm-up phase with static time constants and
combining it with nearest-distance tool selection, cutting the run time by roughly 80% or more.
```bash
PYTHONPATH=src python -m ufast.cosim.run dataset/HVLM dataset/SMAT2022.rail --days 60 --static-warmup-days 50 --amhs-settling-days 5 --machine-selection nearest
```

### 📊 Scenario C: building a large one-year (365-day) baseline cache
Without running U-FAST, computes the one-year reference performance metrics of the production-only
simulators PySC and Logi and stores them permanently in the `results/baseline/` cache folder.
```bash
python scripts/compare_baselines.py --days 365 --simulators pysc logi --datasets HVLM LVHM LVLM
```

### 📈 Scenario D: U-FAST performance comparison from cached data
Restores and loads the already built 365-day baseline cache, runs only U-FAST quickly for 60 days
(with a 50-day warm-up), and aggregates the performance of the three engines into a 3-way
comparison plot and CSV.
```bash
python scripts/compare_baselines.py --days 60 --static-warmup-days 50 --warmup-days 50 --machine-selection nearest --simulators pysc logi fills
```

### 🔍 Scenario E: performance sweep over the OHT fleet size
Gradually increases the number of OHT vehicles from 20 to 200 to collect the U-FAST performance
trend, and produces a visualisation that overlays the Logi CF (congestion factor) options to compare
logistics bottleneck behaviour.
```bash
python scripts/compare_baselines.py --days 30 --mode sweep --oht-list 20 50 100 150 200 --datasets HVLM
```
