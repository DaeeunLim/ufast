# examples/ — custom strategy plugin examples

## Role

**Four reference implementations** of user-written logistics strategy plugins. Each file states in
its docstring the minimal convention per kind recognised by `common/strategy_loader.py` (method
name, arguments, return format, fallback condition) and provides a working example. Every example
falls back to the default strategy when it returns `None` (or an empty list / a negative value /
raises an exception). Copying and modifying these files is the standard workflow for trying out a
new assignment/routing algorithm.

## Files

| File | Convention (kind) | Example content |
|---|---|---|
| `custom_assignment_example.py` | `AssignmentStrategy.select(target_section_id, idle_ohts, route_manager, bridge, vehicle_controller)` — assignment | OHT in the same section first → OHT with the lowest `bridge.estimate_section_route_cost()` → smallest section-id difference |
| `custom_routing_example.py` | `RoutingStrategy.get_route(from_sec_id, to_sec_id, vehicle_controller)` — routing | BFS section route search over `next_sections` |
| `custom_idle_positioning_example.py` | `IdlePositioningStrategy.plan_reposition(oht, current_time, current_node, ...)` — idle repositioning | 1-hop move to the least congested adjacent section that has a free buffer |
| `custom_routing_cost_example.py` | `compute_cost(move_time, raw_penalty, effective_penalty, section, from_node, to_node, default_cost, context)` — edge cost (called with keyword arguments) | Default formula with a 1.15x weight on CURVE plus an extra penalty for highly congested nodes |

## Usage context

- Put your own strategy in the `strategies/` folder and pass only the file name
  (`strategies/README.md`). When a strategy file is specified in the GUI settings or on the CLI
  (`--custom-assignment` / `--custom-routing` / `--custom-idle` / `--custom-routing-cost`),
  `strategy_loader.load_strategy()` loads it and `control/controllers.py`·`cosim/amhs.py` call it
  during the run.
- Note: when a custom routing_cost function is set, the C-accelerated path-search engine
  (`route/fast_pathfinder`) is disabled and the pure-Python path is used — a slower run during
  strategy experiments is expected.
