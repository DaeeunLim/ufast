# scripts — benchmark and experiment scripts

## Role

Automation scripts around the runners: the 3-way simulator comparison and OHT fleet sweep
benchmark, a regression check of the C-accelerated path-search engine, heatmap figures of the queue
(blocking) model, and an example batch script for multi-seed experiments. None of them is needed to
run U-FAST itself.

## Files

| File | Role |
|---|---|
| `compare_baselines.py` | **Benchmark runner.** Runs PySCFabSim, LogiFabSim and U-FAST as subprocesses under identical conditions and normalises, summarises and plots the results as common KPIs. `--mode compare` (3-way KPI comparison) / `--mode sweep` (OHT fleet-size sweep with LogiFabSim congestion-factor overlay). Warm-up exclusion is based on the lot completion time (`done_at`). `--baseline-python` selects a separate interpreter (e.g. PyPy) for the baselines; `--ufast-congestion` picks the U-FAST congestion model (default `queue`). Baseline results are cached in `results/baseline/`, figures go to `results/baseline_compare/`. Options are listed in [docs/cli_guide.md](../docs/cli_guide.md) §3 |
| `verify_fast_route.py` | Equivalence check between the scipy C Dijkstra (`route/fast_pathfinder`) and the pure-Python `cost_search` — confirms that the costs of random congestion-state queries agree to a relative error of 1e-9 (tied routes may differ, so costs are compared). Exits with 1 on mismatch |
| `make_queue_blocking_heatmap.py` | Draws `amhs.blocked_time_by_section` from a result JSON on top of the rail layout (`python scripts/make_queue_blocking_heatmap.py <result.json> [rail_file] [out.png]`) |
| `make_queue_occupancy_heatmap.py` | Runs an instrumented short co-simulation with the queue model and draws the time-averaged per-section occupancy on the rail layout (PNG + PDF). Controlled by the `UFAST_HEATMAP_DAYS` / `UFAST_HEATMAP_OHT` / `UFAST_HEATMAP_CONGESTION` environment variables |
| `exp5_queue_fleet_sweep.sh` | Example experiment batch: HVLM fleet sweep (5–300 OHTs) × 3 seeds with the queue model. Writes one command per run to `logs/exp5_queue/commands.txt` and executes them in parallel |

The heatmap scripts need matplotlib (`pip install -e ".[viz]"`).

### Baseline simulators (not bundled)

`compare_baselines.py` expects the baseline simulators as separate checkouts:

- **PySCFabSim** — `git clone https://github.com/prosysscience/PySCFabSim-release external/PySCFabSim-release`
  (or set `UFAST_PYSC_DIR`).
- **LogiFabSim** — obtain from the authors (Rank & Betker, 2025) and place it at `external/j3c-fork`
  (or set `UFAST_LOGI_DIR`).

The `external/` folder is git-ignored.

## Usage examples

```bash
# Build a 365-day baseline cache (PySCFabSim + LogiFabSim only)
python scripts/compare_baselines.py --days 365 --simulators pysc logi --datasets HVLM LVHM LVLM

# 3-way comparison against the cache, U-FAST run for 60 days with a 50-day warm-up
python scripts/compare_baselines.py --days 60 --static-warmup-days 50 --warmup-days 50 --simulators pysc logi fills

# C engine regression check
python scripts/verify_fast_route.py

# Blocking heatmap of an existing run
python scripts/make_queue_blocking_heatmap.py results/<run_id>/<name>.json
```
