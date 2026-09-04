# src/ufast/production — PySCFabSim-based production DES

## Role

**Production DES core that simulates lot-machine scheduling in a next-event manner** by reading SMT2020-format fab datasets (tab-separated text: tool/route/order/WIP/setup/downcal/pmcal, etc.) — a fork of PySCFabSim. It covers the machine/step/lot model, the event queue, dispatching rules (fifo/cr/random), batching, setups, rework, sampling and breakdowns/PM, and does not model the AMHS by itself (transport between steps is a static distribution from `fromto.txt`). For the U-FAST integrated run (production + AMHS), the following have been added relative to upstream: a **hook that can intercept transport** (`Instance._lot_ready_for_step`), tool/node mapping (`Machine.equipment_id`, `Machine.node_name`), and machine reservation (`reserved_lots`/`reserved_machine`).

## Files

| File | Key classes/functions | Role |
|---|---|---|
| `instance.py` | `Instance` — `next_step()`, `free_up_lots()`, `_lot_ready_for_step()`, `dispatch()`, `next_decision_point()` | **Simulation core.** Consumes events, advances lot steps (rework/sampling decisions), computes processing/setup/cascading times at dispatch, PM countdown, CQT violation checks, plugin hook calls. `_lot_ready_for_step()` is the extension point where the integrated run inserts the AMHS |
| `file_instance.py` | `FileInstance(Instance)` | Converts file dicts into domain objects. Creates `STNQTY` `Machine`s (+ attaches tool id/node), links `fromto` transport distributions to route steps, builds `Lot`s from order/WIP, and `BreakdownEvent`s from the setup table and downcal/pmcal |
| `classes.py` | `Machine`, `Step`, `Lot`, `Route`, `FileRoute`, `Product` | Domain model — Machine (load/unload, family, cascading, PM counters, setup state, min-run), Step (processing-time distribution, batch min/max, sampling/rework probability, CQT, dedication), Lot (priority, deadline, accumulated times, `cr()`) |
| `events.py` | `MachineDoneEvent`, `LotDoneEvent`, `ReleaseEvent`, `BreakdownEvent` | Event types. Breakdown/PM events re-register themselves for the next cycle |
| `event_queue.py` | `EventQueue` | Timestamp-ordered event queue (binary-search insertion) |
| `read.py` | `read_all()` | Parses `*.txt` in the dataset directory and returns `{filename: [dict, ...]}`. Preprocessors applied via the `NOWIP`/`NOBREAKDOWN`/`NOPM`/`NOREWORK`/`NOSAMPLING` environment variables |
| `dataset_preprocess.py` | 5 `Remove*` preprocessors | Dataset simplification — removes breakdowns/PM/WIP/rework/sampling |
| `tools.py` | `get_distribution()`, 3 distributions | Unit conversion (sec/min/hr/day -> seconds) and distribution object factory |
| `randomizer.py` | `Randomizer` (Singleton) | Single global random source. Reproducibility via the `SEED` environment variable or an externally injected seed |
| `greedy.py` | `get_lots_to_dispatch_by_machine()`, `get_lots_to_dispatch_by_lot()`, `run_greedy()` | Dispatch decision logic — machine-based (l4m) / lot-based (m4l), batch formation, swapping to a setup-matching machine. `run_greedy()` is the standalone CLI |
| `stats.py` | `print_statistics()` | Prints per-lot cycle time/throughput/on-time and per-family availability/utilisation/PM/breakdown statistics, and saves JSON |
| `dispatching/` | `Dispatchers`, `dispatcher_map`, `LotForMachineDispatchManager`, `MachineForLotDispatchManager` | Priority rules (fifo/cr/random ptuple) and l4m/m4l matching management |
| `plugins/` | `IPlugin`, `CostPlugin` | Observation hook contract (`on_dispatch`, `on_lot_done`, ... 11 hooks) and tardiness/incomplete cost KPIs |

## Data flow

1. Loading: `read_all()` -> `FileInstance` -> creates `Machine/Route/Step/Lot` + `BreakdownEvent`.
2. Loop: `next_decision_point()` -> `greedy` produces dispatch candidates -> `dispatch()` -> registers `MachineDoneEvent`/`LotDoneEvent`.
3. When a lot finishes, `free_up_lots()` calls `_lot_ready_for_step()` for the next step — the default implementation makes it dispatchable immediately; **the integrated-run subclass (`ufast.UFastInstance`) inserts OHT transport here**.
4. Observations go out through `IPlugin` hooks; at the end `finalize()` -> `on_sim_done`.

## External dependencies and caveats

- `src/ufast/common.equipment_kpi` — starvation/downtime instrumentation (added relative to upstream).
- No third-party dependencies (standard library only).
- Actual consumers are `src/ufast` (CLI) and `src/ufast/integration/production_runner.py` (GUI) — both use this package.
- Known leftover: the `--wandb`/`--chart` options of `greedy.run_greedy()` reference plugins that do not exist in this package and raise ImportError (standalone path only; unrelated to the integrated run path).
