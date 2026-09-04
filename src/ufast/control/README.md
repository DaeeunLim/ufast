# src/ufast/control — Logistics event controllers

## Role

The **brain** of the AMHS logistics simulation (GUI/legacy mode), a single-module package (`controllers.py`, about 1,480 lines). It consists of two classes: `VehicleController`, responsible for OHT creation, initial distributed placement, assignment and route search; and `EventHandler`, which processes discrete events (LOT/TRANSFER_EXT/PROCESS_END), advances vehicles section by section and updates EQ states. It has a three-tier structure: custom strategy (.py plugin) injection → delegation to the `route` package → its own Dijkstra fallback.

## Main components

### VehicleController — assignment and routing

| Method | Role |
|---|---|
| `init()` / `_distribute_ohts()` | Creates `OHT_000...` and distributes them across sections in discrete bin order (prevents clustering in one area) |
| `assign_oht(target_section_id)` | Selects an IDLE OHT near the target section — custom assignment strategy → `Dispatcher` → default logic, in that order |
| `_select_repositioning_candidate_for_dispatch()` | Also treats REPOSITIONING vehicles as candidates and preemptively recalls them when needed |
| `get_route(from, to)` | Three-tier route search — ① custom routing strategy ② `bridge.find_section_route()` (penalty-aware) ③ its own section Dijkstra |
| `_log_*` | CSV logging of assignments, moves and state transitions via `route.logistics_logger` |

### EventHandler — event processor

| Method | Role |
|---|---|
| `process_event(event)` | Branches on LOT / TRANSFER_EXT / PROCESS_END |
| `init_lot_events()` | Registers fixed-interval (3600/rate s) LOT events based on the hourly rate in the From-To .dat |
| `_handle_lot()` | Assigns an OHT at the source EQ → retries after 5 s on failure; on success computes the route + sends `OHT_ASSIGNED` notification |
| `_handle_transfer_ext()` / `_handle_arrival()` | Section-to-section advance (checks buffer capacity); on arrival handles pickup (switch to LOADED) and delivery completion |
| `configure_simulation_mode()` | Switches between `from_to_only` (logistics only) and `production_logistics` (includes rundown/PROCESS_END) |
| `reposition_idle_ohts()` | Idle repositioning — round-robin with a limit of 10 vehicles per call, ping-pong penalty based on a recent-visit deque |

## Data flow

- `main_ui.SimulationApp.start_simulation()` creates `VehicleController` then `EventHandler`; if the route package is available, injects `Dispatcher(NearestIdleStrategy)`.
- The loop is driven by `main_ui.SimulationThread._step()`: `ds.event_queue` heappop → `process_event()` → state update → periodic `reposition_idle_ohts()`.
- All state is reflected in the `SimulatorDataSet` singleton — `gui.viewer` never references the controllers directly.
- EQ state changes happen only through the `EQ.message_receiver()` message pattern.

## External dependencies

Required: `core.data_set`, `core.components`, `common.logger`. Optional (try/except): `common.strategy_loader`, `route.RouteManager`, `route.logistics_logger`. No third-party packages — can run headless.
