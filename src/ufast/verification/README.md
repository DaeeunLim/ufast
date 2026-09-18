# src/ufast/verification — post-run verification

## Role

Does not interfere with the simulation; generates a rule violation report from the
state at the end of the run and the logger records. Kept in its own folder because its nature differs from the shared utilities
(`src/ufast/common/`).

## Files

| File | Key function | Role |
|---|---|---|
| `verification.py` | `run_verification()` | Post-run rule violation checks — OHT status/section (V0xx), buffers (V1xx), lot timestamps (V2xx), rundown ordering (V3xx), tracker registration (V4xx); CSV report |

## Usage context

- `main_ui.py` calls `run_verification()` when saving logs after the simulation ends.
