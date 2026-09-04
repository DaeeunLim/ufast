# route package technical notes

## Overview

The `src/ufast/route/` package is responsible for **node-level route search and management** on the OHT (Overhead Hoist Transport) rail network of a semiconductor fab AMHS (Automated Material Handling System).

It re-implements, for simulation purposes, the node-level route management concepts used in commercial/in-house OHT control systems. Real-time hardware control is out of scope; instead it provides the features a simulation needs, such as deadlock detection, detour limiting, and C-accelerated cost search.

---

## Package layout

```
src/ufast/route/
├── __init__.py          # package exports
├── graph.py             # network data structures (Node, Link, NetworkSection, Network)
├── rail_parser.py       # RailData (output of the shared parser common/rail_format.py) → Network conversion
├── pathfinder.py        # Dijkstra route search + detour limiting
├── fast_pathfinder.py   # scipy C Dijkstra acceleration engine (cost_search only)
├── route_manager.py     # unified API
├── bridge.py            # co-sim integration (section route cost, traffic penalty updates)
├── dispatcher.py        # OHT assignment strategies
├── vehicle_tracker.py   # node occupancy tracking + deadlock detection (GUI mode only)
├── logistics_logger.py  # logistics event logger (GUI/legacy only)
└── ROUTE_MANAGER.md     # this document
```

---

## Main algorithms

### 1. Dijkstra shortest path (PathFinder)

A `heapq`-based priority queue gives `O(log n)` insertion/extraction.

```python
# heapq - O(log n) insertion
heapq.heappush(heap, (new_time, node_name))
# O(log n) extraction
curr_time, curr_name = heapq.heappop(heap)
```

**Search flow**:

```
1. Set the origin node's arrived_time=0 and push it onto the heap
2. Pop the lowest-cost node from the heap
3. Explore the neighbouring nodes in every section that node belongs to
4. For each neighbouring node:
   - new_cost = current_cost + move_in_time × effective_penalty
   - if new_cost < the existing arrived_time, update it and push onto the heap
5. On reaching the destination node, trace the prev_node chain back to build the route
```

**Section-based exploration**: unlike an ordinary adjacency-list Dijkstra, exploration proceeds per "section" the node belongs to. A section is an ordered array of consecutive nodes; it is scanned sequentially in one direction and only the section end node is enqueued. This structure exploits the fact that rail networks consist of "long straight runs between merge/branch points".

```
Section S1: [A, B, C, D]
From current node B (pos=1):
  forward: C → D (D is the section end, so it is enqueued)
  backward: A (only for two-way sections; A is the section end, so it is enqueued)
```

### 2. CostSearch (1:N Dijkstra)

Computes the costs from one origin node to several destination nodes in a single pass. Used for the equipment-to-equipment cost table (`build_eq_cost_table`) and the co-sim section route cost estimate (`bridge.estimate_section_route_cost`).

The same Dijkstra as PathSearch, but it keeps searching until every destination node is found and tracks the distance (length) along with the route.

### 3. C acceleration engine (fast_pathfinder.FastCostEngine)

Accelerates `cost_search`, which accounted for most of the co-sim run time, with `scipy.sparse.csgraph.dijkstra` (C implementation). For a 1-day HVLM co-sim the total run time dropped from 97 s to 15.5 s.

- The rail topology is immutable, so the section chains are expanded into a CSR sparse matrix **only once**.
- The edge weight is `move_time × effective_penalty(destination node penalty)`, identical to the existing cost formula; when a penalty changes because an OHT enters/leaves, only the edges entering the affected node are updated in place (bridge hook → `notify_penalty_changed`).
- Falls back automatically to the Python path in the following cases: a custom cost function is set (a per-edge Python callback cannot be expressed in the C path), scipy is not installed, or the environment variable `UFAST_FAST_ROUTE=0` is set.
- When several routes tie on cost, the chosen route may differ from the Python implementation. Cost equality is verified with `scripts/verify_fast_route.py`.

### 4. Detour limiting (A+B approach)

Choosing routes by the traffic penalty alone causes **repeated excessive detours**. Two mechanisms are combined to address this.

#### Approach A: Max Detour Ratio (route length cap)

If the node count of the dynamic route (penalty-aware) exceeds `max_detour_ratio` times that of the static route (penalty-free), fall back to the static route.

```
static route: 1→2→3→4 (4 nodes)
dynamic route: 1→5→6→7→8→9→4 (7 nodes)
ratio = 7/4 = 1.75

max_detour_ratio = 1.5 → 1.75 > 1.5 → use the static route
```

Static routes are cached (`_static_cache`) so the same origin-destination pair is never recomputed.

#### Approach B: Bounded Penalty (damped penalty)

The influence of `traffic_penalty` is bounded by `penalty_weight` and `penalty_cap`.

```
effective_penalty = 1 + weight × min(raw_penalty - 1, cap)
```

Effect of each parameter:

| Parameter | Meaning | Example |
|-----------|---------|---------|
| `penalty_weight` | penalty influence ratio (0~1) | 0.5 = 50% damping |
| `penalty_cap` | maximum penalty excess | 3.0 |

Worked example (`weight=0.5, cap=3.0`):

| raw_penalty | excess (raw-1) | bounded | effective |
|-------------|----------------|---------|-----------|
| 1.0 | 0 | 0 | 1.0 |
| 2.0 | 1.0 | 1.0 | 1.5 |
| 4.0 | 3.0 | 3.0 | 2.5 |
| 10.0 | 9.0 | 3.0 (capped) | 2.5 |

#### Combined A+B flow

```
path_search(from, to):
  1. search the dynamic route with the bounded penalty (_dijkstra)
  2. if max_detour_ratio > 0:
     a. search the static route (using the cache)
     b. if dynamic node count / static node count > ratio → return the static route
  3. return the dynamic route
```

#### Configuration

```python
rm = RouteManager()
rm.load_from_rail("layout.rail")
rm.initialize()

# Detour limit settings
rm.configure_detour_limit(
    max_detour_ratio=1.5,   # allow up to 1.5x the static route
    penalty_weight=0.5,     # 50% penalty influence
    penalty_cap=3.0,        # penalty cap 3.0
)

# Check statistics after the simulation
stats = rm.get_detour_stats()
# {'total_searches': 1000, 'fallback_count': 23, 'fallback_ratio': 0.023, ...}
```

### 5. Deadlock detection (VehicleTracker)

Builds a **wait-for graph** and detects cycles. (GUI mode only — the co-sim CLI uses a delay-based congestion model and therefore needs no node occupancy tracking.)

```
wait-for relation:
  OHT_A → wants to move to node X, but OHT_B occupies it
  OHT_B → wants to move to node Y, but OHT_A occupies it
  → cycle: [OHT_A, OHT_B] = deadlock
```

Cycles are detected with DFS, and all independent deadlocks are returned.

```python
deadlocks = rm.detect_deadlock()
# [['OHT_1', 'OHT_2'], ['OHT_5', 'OHT_6', 'OHT_7']]
```

---

## Data structures

### Node

Carries only the minimum fields needed for route search: position (`name, x, y`), membership (`hid, area, zone, virtual`), congestion (`traffic_penalty`), topology (`section_list, move_in_times`), and Dijkstra search state (`arrived_time, prev_node`).

### NetworkSection

A rail segment consisting of an ordered array of consecutive nodes. It is named `NetworkSection` rather than `Section` to distinguish it from `core.components.Section` (the simulation section).

### Network

The container that manages every node, link, section, and equipment mapping.

---

## .rail file format

A tab-separated text file. The first line is the `RAILDATA` header.

| Record type | Purpose | Format |
|-------------|---------|--------|
| NODE | node definition | `NODE {name} {x} {y}` |
| LINK | link definition | `LINK {name} {LINE/CURVE} {node1} {node2} [{penalty1} {penalty2}]` |
| EQTONODEMAP | equipment→node mapping | `EQTONODEMAP {eq_name} {node_name}` |
| RAILLIST | rail geometry | `RAILLIST {name} {type} {node1} {node2} {x1} {y1} {x2} {y2} {angle} [{length}]` |
| LINE | visualization line | (parsed and skipped) |
| CURVE | visualization curve | (parsed and skipped) |
| SCALE | drawing extent | `SCALE {minX} {maxX} {minY} {maxY}` |

---

## Usage examples

### Basic usage

```python
from route import RouteManager

rm = RouteManager()
rm.load_from_rail("layout.rail")
rm.initialize(line_speed=2.6667, curve_speed=0.8)

# Route between nodes
path = rm.get_route("101", "250")

# Route between equipment
path = rm.get_route_by_eq("EQ_A", "EQ_B")

# Cost table (all equipment pairs)
cost_table = rm.build_eq_cost_table()
```

### Simulation integration (GUI mode)

```python
# Register a vehicle
rm.register_vehicle("OHT_1", initial_node="101")

# Assign a route
path = rm.assign_route("OHT_1", to_node="250")

# In the event loop
if rm.can_move_vehicle("OHT_1"):
    rm.move_vehicle("OHT_1")

# Check for deadlocks
deadlocks = rm.detect_deadlock()
if deadlocks:
    handle_deadlock(deadlocks)
```

### Applying the detour limit

```python
rm.configure_detour_limit(
    max_detour_ratio=1.5,
    penalty_weight=0.5,
    penalty_cap=3.0,
)

# After the simulation run
stats = rm.get_detour_stats()
print(f"Fallback ratio: {stats['fallback_ratio']:.1%}")
```

---

## Design summary

| Item | Implementation |
|------|----------------|
| Priority queue | heapq O(log n) |
| Cost search acceleration | scipy C Dijkstra (CSR matrix, partial penalty updates) |
| Deadlock detection | wait-for graph cycle detection (GUI mode) |
| Detour limiting | A+B approach (detour ratio + bounded penalty) |
| Coding style | dataclass, type hints, snake_case |
| Module layout | search/parsing/tracking/integration split into separate modules |
