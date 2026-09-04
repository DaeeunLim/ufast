# scripts — benchmark and experiment scripts

## Role

A collection of automation scripts that run and collect the experiments for the paper. The Python
scripts cover the 3-way simulator comparison (E1) and OHT sweep (E2) benchmarks and the
verification of the path-search engine; the shell scripts generate and run experiment batches.

## Files

| File | Role |
|---|---|
| `compare_baselines.py` | **Core benchmark runner.** Runs PySCFabSim (`external/PySCFabSim-release`, not included in the repository — `git clone https://github.com/prosysscience/PySCFabSim-release external/PySCFabSim-release`), LogiFabSim (`external/j3c-fork`, not included — obtain from the authors; the paths can be changed with the `UFAST_PYSC_DIR`/`UFAST_LOGI_DIR` environment variables. Neither is needed to run U-FAST itself) and U-FAST as subprocesses and normalises, summarises and plots the results as common KPIs. `--mode compare` (3-way comparison, E1) / `--mode sweep` (OHT fleet-size sweep, E2). Warm-up exclusion is based on the completion time (`done_at`). `--baseline-python` selects a separate interpreter (e.g. PyPy) for the baselines. Baseline results are cached in `results/baseline/` |
| `verify_fast_route.py` | Equivalence check between the scipy C Dijkstra (`route/fast_pathfinder`) and the pure-Python `cost_search` — confirms that the costs of random congestion-state queries agree to a relative error of 1e-9 (tied routes may differ, so costs are compared). Exits with 1 on mismatch |

## Usage examples

```bash
# 3-way comparison (E1)
python scripts/compare_baselines.py --days 365 --simulators pysc logi --datasets HVLM LVHM LVLM


# C engine regression check
python scripts/verify_fast_route.py
```
