# src/ufast/viz — Rerun 3D visualisation

## Role

Module that visualises the fab layout and OHT movement in the [Rerun](https://rerun.io) viewer. Phase 1 logs the static layout (rail nodes/links/tool family markers); Phase 2 replays, on a time axis, the OHT trip trajectories recorded during a simulation. Coordinates are in mm, and colours/sizes are tuned for the SMAT2022 fab (about 300 m x 153 m). This is a visualisation path that can be demonstrated from the CLI without the GUI.

## Files

| File | Key classes/functions | Role |
|---|---|---|
| `rerun_layout.py` | `show_layout(rail_file, ...)`, `log_layout_entities` | Parses `.rail` with `RouteManager` and logs node points, link segments and tool family markers as Rerun entities. Can be run standalone with `python3 -m ufast.ufast.viz.rerun_layout [--rail ...]` |
| `trajectory.py` | `Trip`, `TrajectoryLog`, `interpolate_trip_position` | Records one OHT transport as an empty leg (to pickup) and a loaded leg (to delivery). Saves/loads `results/<run_id>/*_trajectories.json`; computes (x, y, state) at time t by linear interpolation on the cumulative-distance fraction |
| `rerun_replay.py` | `show_run(rail_file, trajectory_path, time_step_s=60.0, ...)` | Logs OHT positions frame by frame on the `sim_time` timeline over the static layout -> scrub/play in the viewer. Colour per state (IDLE grey / EMPTY blue / LOADED orange / AT_DEST green) |
| `replay_recorder.py` | `ReplayRecorder` — `on_step()`, `finalize()` | Recorder for the GUI AMHS **Replay mode**. Down-samples section congestion, OHT/EQ status and KPIs on the simulation thread under a frame budget (default 10k) -> on exit saves Parquet (`logs/replay/<ts>/`) and launches the Rerun viewer (rail heatmap + synchronised KPI timeline) |

## Usage context

- `ufast/cosim/run.py --viz`: enables trip recording -> saves the `TrajectoryLog` JSON on exit -> launches the viewer via `show_run()`. `run_legacy.py` behaves the same.
- Saved results can be replayed later with `python3 -m ufast.ufast.viz.rerun_replay <trajectory.json>`.
- The `rerun` package is an optional dependency — without it `_ensure_rerun()` fails explicitly, and the simulation run itself is unaffected.
