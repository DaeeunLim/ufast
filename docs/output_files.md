# U-FAST Output File Specification (results/)

Describes the files produced by `ufast-run` (co-simulation) and `ufast-fromto` (logistics-only)
and the fields they contain. Everything needed for post-hoc analysis (pandas / R) and for
reproducing a run is in these files.

## 1. Folder and file naming rules

- **Folder**: `results/<YYYY-MM-DD_HH-MM-SS_microseconds>/` — a new folder is created for every run
  and previous runs are never overwritten. All files in one folder share the same base name.
- **Base name (co-simulation)**

  ```
  <dataset>_<days>d_<OHT count>oht_a<alpha>_<production dispatcher>_<AMHS strategy>_<congestion model>[extra options]_s<seed>
  e.g. HVLM_180d_15oht_a0.05_fifo_fifo_queue_sw120_as10_s0
  ```

  Congestion model abbreviations: `queue` (as is), `section_local`→`sl`, `global_tip`→`gt`,
  `off`→`no`. Extra options are appended only when they differ from the default: `_idleon` (idle
  repositioning), `_rdyn` (dynamic routing), `_ms<machine-selection>` (e.g. `_msnearest`),
  `_sw<days>` (static warm-up), `_as<days>` (AMHS settling).
  Fractional days are written like `0p2`.
- **Base name (logistics-only)**

  ```
  fromto_<rail>_<FromTo>_<seconds>s_<OHT count>oht_<strategy>_<congestion model>_s<seed>
  e.g. fromto_case1_case1_Fromto_3600s_50oht_nearest_queue_s0
  ```

## 2. Generated output files

| File | Created when | Content |
|---|---|---|
| `<name>.json` | always | Integrated KPIs + run metadata (§3) |
| `<name>_lots.csv` | always (co-simulation) | Completed lot history (§4) |
| `<name>_trips.csv` | `--viz` | OHT transport (trip) trace (§4) |
| `<name>_kpi_timeseries.csv` | `--viz` | Logistics snapshot time series (§4) |
| `<name>_machines.csv` | `--viz` (co-simulation) | Tool busy intervals (§4) |
| `<name>_trajectories.json` | `--viz` | Integrated trajectory log for Rerun replay (§5) |
| `results/aggregate/aggregate.{json,csv}` | `ufast-aggregate results/` | Multi-seed aggregation table (§6) |

## 3. Integrated KPI JSON (`<name>.json`)

Holds, hierarchically, the metadata needed to reproduce the run and the KPIs of the fab's three
perspectives (production, equipment, logistics).

| Top-level key | Contents |
|---|---|
| `meta` | • run mode (`mode`: `production` / `fromto`), `run_id`, (fromto) `engine`<br>• input paths: `dataset_dir`, `rail_file`, (fromto) `fromto_file` and their short names<br>• experiment conditions: `days` or `sim_duration_s`, `num_oht`, `seed`, `alpha`<br>• all policy slots: `dispatcher`, `amhs_strategy` (`strategy` for fromto), `congestion_model`, `idle_positioning`, `routing_model`, `machine_selection`<br>• warm-up: `static_warmup_days`, `amhs_settling_days`, `measurement_start_days`<br>• **`vehicle`**: full vehicle specification — `length_mm`, `headway_mm`, `footprint_mm`, `max_speed_mm_s`, `accel_mm_s2`, `decel_mm_s2`, `line_speed_mm_s`, `curve_speed_mm_s`, `source` (file/flag origin)<br>• `wall_time_s`, `sim_time_days` (or `sim_time_s`), `dispatch_steps` (or `events_processed`)<br>This block alone is enough to reproduce the same run. |
| `production` (co-simulation) | • `completed`: number of completed lots **cumulative over the whole run** (for measurement-window statistics count directly from `done_at` in `_lots.csv`), `active`: lots in progress<br>• transport counts: `transport_count` (actual AMHS transports), `static_transport_count` / `static_transport_time_s` (static warm-up transports), `skipped_transport` (families not mapped to the rail), `same_node_transport`, `reserved_transport`, `preassigned_machine`<br>• `by_lot_type`: per lot type (`Lot_3`, `HotLot_3` …) throughput, mean/median cycle time, on-time rate, waiting/processing/transport time<br>• `by_category`: the same aggregation per priority category (Regular / Hot / SuperHot) |
| `equipment` (co-simulation) | • measurement window: `measurement_start_s` ~ `measurement_end_s`, tool count<br>• `busy_time_s`, `setup_time_s`, `utilization_pct`<br>• starvation decomposition: total time and count, **`transport_blocked_starvation_s` / `_pct`** (starvation that would not have occurred with instantaneous transport), `upstream_starvation_s` (attributed to upstream production), mean, p95, max<br>• breakdown/PM: `breakdown_time_s`, `pm_time_s`<br>• `by_family`: busy/starvation/breakdown breakdown per tool family |
| `amhs` | • job counts: `total_requested_jobs`, `total_jobs` (completed)<br>• times: `avg_free_flow_s`, `avg_transport_s`, `avg_empty_travel_s`, `avg_loaded_travel_s`, `avg_delivery_s` (request→delivery), `avg_oht_wait_s` (waiting for assignment)<br>• quantiles: `delivery_{p50,p95,p99,max}_s`, `transport_{p50,p95,p99,max}_s`<br>• `avg_congestion_factor` (transport/free-flow), `avg_utilization` (fleet utilisation), `max_queue`, `max_node_inflight` (maximum simultaneous section occupancy)<br>• queue (blocking) model only: `blocked_events`, `blocked_time_s`, `deadlock_forced`; per-section `blocked_by_section`, `blocked_time_by_section` (blocking heatmap data; empty dict for the other models)<br>• `reposition_count`, `oht_count`, `congestion_alpha` |
| `transport` (logistics-only) | `lots_generated` (number of requests), `lots_completed`, `completion_rate`, `oht_count`, `oht_final_status`. Replaces `production`/`equipment`; the `amhs` block is the same as in co-simulation. |

`ufast-analyze [json]` reads this JSON and prints a console report (with no argument it uses the
most recent JSON under `results/`).

## 4. Raw data CSVs

A suffix is appended to the same base name as the JSON. All times are simulation seconds (sim s).

| File | Columns | Purpose |
|---|---|---|
| `_lots.csv` (always) | `lot_name, release_at, done_at, deadline_at, cycle_time_days, tardiness_s, waiting_s, processing_s, transport_s, on_time` | Measurement-window filtering (e.g. `done_at ≥ 130 d`), quantile/distribution analysis. Initial WIP lots have `release_at < 0` |
| `_trips.csv` (`--viz`) | `oht_id, request_time, assignment_time, delivery_time, empty_duration, loaded_duration, total_duration, congestion, empty_path_len, loaded_path_len` | Per-trip transport time and empty/loaded ratio analysis |
| `_kpi_timeseries.csv` (`--viz`) | `sim_time, busy_oht, section_inflight_sum, pending_queue, cumulative_delivered, max_section_inflight` | Fleet load and queue trends over time |
| `_machines.csv` (`--viz`, co-simulation) | `family, start_time, end_time, duration_s` | Tool busy Gantt chart, per-family load visualisation |

## 5. Trajectory file (`_trajectories.json`, `--viz`)

A log bundling per-trip section entry times and coordinates, initial OHT positions, tool busy
intervals and KPI snapshots. `ufast.viz.rerun_replay.show_run(rail, trajectories)` replays the
vehicle movements and KPI time series in the Rerun viewer.

## 6. Multi-seed aggregation (`ufast-aggregate`)

`ufast-aggregate results/` groups the JSON files by configuration (base file name without
`_s<seed>`) and writes `results/aggregate/aggregate.json` and `aggregate.csv` (metric, n, mean,
std, min, max).

## 7. Output summary by mode and option

| Invocation | Generated files |
|---|---|
| `ufast-run` | `<run>.json`, `<run>_lots.csv` |
| `ufast-run --viz` | above + `_trips.csv`, `_kpi_timeseries.csv`, `_machines.csv`, `_trajectories.json` |
| `ufast-fromto` | `fromto_<…>.json` |
| `ufast-fromto --viz` | above + `_trips.csv`, `_kpi_timeseries.csv`, `_trajectories.json` |
| `ufast-aggregate results/` | `results/aggregate/aggregate.json`, `aggregate.csv` |

Note: the raw values of the per-section **occupancy** heatmap (`*_sections.csv`: `section_id,
mean_occupancy_ohts, peak_occupancy_ohts, occupancy_integral_oht_s, section_capacity_ohts,
blocked_time_s, blocked_events`) are not a runner output; they are produced by
`scripts/make_queue_occupancy_heatmap.py`, which performs an instrumented run.
