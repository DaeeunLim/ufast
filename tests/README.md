# tests — pytest suite

## Role

A lightweight regression test suite (48 cases, about 2 seconds) that protects the core KPI
calculations, the result-saving conventions, the input parsers and the behaviour of the queue
(blocking) model. Instead of heavy simulations it is designed to run quickly with
`SimpleNamespace` dummy objects and zero-day smoke runs.
GitHub Actions (`.github/workflows/tests.yml`) runs it on every push/PR on the ubuntu·macOS ×
Python 3.10·3.13 matrix.

## Files

| File | What it verifies |
|---|---|
| `test_equipment_kpi.py` | Measurement-window semantics of `common/equipment_kpi` — clipping of starvation at measurement_start, union of breakdown+PM, separation of censored (unfinished) starvation, the `run_to` upper bound |
| `test_logistics_kpi.py` | OHT time allocation of `SimulationLogger` — idempotence of repeated `finalize_oht_times` calls after state transitions, idle/assigned/loaded allocation and utilisation calculation |
| `test_result_paths.py` | File-name and directory conventions of `cosim/results` — grouping under `results/<run_id>/`, the `HVLM_60d_100oht_a0.05_fifo_fifo_sl_s0.json` format, preservation of `meta.run_id` |
| `test_ufast_smoke.py` | (1) `requirements.txt` is UTF-8 and includes numpy/scipy (2) `run_ufast` zero-day smoke run — machine loading, comparison table output (3) `strategy_loader` return-value normalisation |
| `test_queue_model.py` | Capacity-constrained queue model — section capacity calculation, FIFO entry blocking/wake-up, wait-for cycle deadlock detection and forced-entry accounting |
| `test_fromto.py` | `common/fromto_parser` — interpretation of the 3rd column as an hourly rate, fixed-interval event generation at `3600/rate`, cycling of rates over time periods; `run_fromto` 1-hour smoke run (case1, blocking engine, mode/engine/vehicle/blocking keys in the result JSON) |
| `test_vehicle_spec.py` | `cosim/vehicle` — default file = SMAT2022 OHTV0, CSV↔JSON agreement, flag overrides and unit conversion (m/s→mm/s), partial user JSON, meta and kinematics conversion |
| `test_consistency.py` | `common/consistency` — FromTo equipment names ↔ `.rail` EQ list, dataset tool family ↔ rail destination consistency checks and `--strict` behaviour |

## Running

```bash
python -m pytest tests/ -q
```

`test_ufast_smoke.py` uses the real `dataset/HVLM` + `dataset/SMAT2022.rail` data, so a broken
dataset/.rail format is caught here first. The routing/AMHS logic itself is verified separately by
`scripts/verify_fast_route.py`.
