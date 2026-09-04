# strategies/ — folder for user strategy files

This is where user strategy files passed to `--custom-assignment`, `--custom-routing`,
`--custom-idle` and `--custom-routing-cost` of `ufast-run` / `ufast-fromto` are kept.
If you give the flag **only a file name**, it is looked up in this folder (an absolute or relative
path is used as is).

```bash
ufast-run dataset/HVLM dataset/SMAT2022.rail --days 7 --custom-assignment my_assign.py
# == --custom-assignment strategies/my_assign.py
```

## File formats

| Format | Use |
|---|---|
| `.py` | Rule written as code. The standard workflow is to copy and modify one of the 4 examples in `examples/`. The loader looks for the entry point in the order `create_*_strategy()` / `*Strategy` class / per-kind function (`select`, `get_route`, `plan_reposition`, `compute_cost`) |
| `.pkl` / `.pickle` | **Pickled strategy object**, e.g. a trained policy. Any object with the same method conventions written with `pickle.dump` works. Used to compare a policy trained offline (e.g. RL) with the built-in rules under the same seed |

| Slot | Flag | Convention (method called) | Example |
|---|---|---|---|
| OHT assignment | `--custom-assignment` | `select(target_section_id, idle_ohts, route_manager, bridge, vehicle_controller)` → OHT or `None` | `examples/custom_assignment_example.py` |
| Routing | `--custom-routing` | `get_route(from_sec_id, to_sec_id, vehicle_controller)` → list of section ids or `None` | `examples/custom_routing_example.py` |
| Idle repositioning | `--custom-idle` | `plan_reposition(oht, current_time, current_node, ...)` → target section or `None` | `examples/custom_idle_positioning_example.py` |
| Route-search edge cost | `--custom-routing-cost` | `compute_cost(move_time, raw_penalty, effective_penalty, section, from_node, to_node, default_cost, context)` (keyword arguments) → cost (float) | `examples/custom_routing_cost_example.py` |

Returning `None` (or an empty list / a negative value / raising an exception) falls back to the
built-in rule. With `--custom-routing-cost` the C-accelerated path search is disabled and the
pure-Python path is used, so a slower run is expected.
The names of the strategy files actually applied are recorded in the result JSON under
`meta.custom_strategies`.

Files in this folder other than `README.md` are not tracked by git (personal experiments).
