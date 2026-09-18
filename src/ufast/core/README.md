# src/ufast/core — Simulation core data model for the GUI

## Role

The lowest layer, holding only the **domain data model** shared by the logistics simulator (GUI mode). It provides the simulation entities (OHT, EQ, Section, OHTBuffer) and a global singleton store containing the event queue. It has no PyQt dependency, so it is reused as is on the headless execution path (`ufast/cosim/run_legacy.py`).

The `Section`/`OHTBuffer` here form the basis of the GUI mode's **capacity-constrained queue-based logistics model** (finite-slot buffers, entry blocking, FIFO). The co-sim CLI (`ufast/cosim/amhs.py`) implements its own section queue model on the same idea (finite FIFO slots per section) with deadlock resolution, plus the delay-based alternatives.

> The CAD shape dataclasses (`CLine`, `CCircle`, `CText`, etc.) live in `src/ufast/drawing/geometry.py`; only simulation entities are kept in `core`.

## Files

| File | Key classes | Role |
|---|---|---|
| `data_set.py` | `Event` | Simulation event — only `time_scheduled` is the comparison key (heapq ordering); provides `clone()` |
| | `SimulatorDataSet` (singleton) | Global store — `sections`/`layers`/`eq_list`/`oht_list`, node/section mappings, `event_queue` (heap), `main_clock`, processing counters, completion log. `get_instance()`, `clear()`, `add_event()`, `remove_event_from_queue()` |
| `components.py` | `OHT` | Vehicle entity — status (IDLE/ASSIGNED/LOADED/RUN/UNLOADING/REPOSITIONING), current section/buffer, destination, loaded lot |
| | `EQ` | Equipment entity — port/internal buffers, 5 statuses; `message_receiver()` performs state transitions on LOT_CREATED/OHT_ASSIGNED/LOT_TRANSPORTED/LOT_INBOUND/LOT_DELIVERED messages |
| | `Section` | Rail segment — length/speed, `next_sections`/`prev_sections`, `oht_buffers` (length/1.0 = capacity), `merge_figures()`, `message_receiver()` allows only the head vehicle to move |
| | `OHTBuffer` | Array of vehicle slots within a section — capacity check, head determination, compaction |

## Data flow

- The `SimulatorDataSet.get_instance()` singleton is shared by `main_ui`, `gui.viewer`, `control.controllers` and `layout.rail_manager`.
- Writes: `layout.rail_manager` fills sections/eq_list → `control.controllers` updates oht_list/event_queue/main_clock → `gui.viewer` reads every frame and renders.
- `data_set.py` does not import other core modules (avoids cycles).

## External dependencies

- `src/ufast/drawing` — `components.py` uses `Figure`/`CLine`/`CQuadCurve` (Section visualization shapes)
- Otherwise only the standard library (`dataclasses`, `heapq`, etc.). No third-party or PyQt dependency.
