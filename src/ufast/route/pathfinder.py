"""
pathfinder.py - Dijkstra route search

Ports PathSearch and CostSearch from the Java RouteManager.
Uses a heapq-based priority queue for O(log n) insertion (an improvement over Java's O(n)).
"""

from __future__ import annotations
import heapq
from typing import Dict, List, Optional, Tuple

from .graph import Network, Node, NetworkSection


class PathFinder:
    """
    Dijkstra-based route finder.

    Ports PathSearch/CostSearch from the Java RouteManager,
    with heapq for better performance.

    Detour limiting (A+B approach):
      A) max_detour_ratio: if the dynamic route exceeds this ratio relative to the
         static shortest route, fall back to the static route that ignores penalties.
      B) penalty_weight / penalty_cap: bound the influence of traffic_penalty.
         effective_penalty = 1 + weight * min(raw_penalty - 1, cap)

    Usage:
        finder = PathFinder(network)
        finder.initialize(line_speed=2.6667, curve_speed=0.8)
        path, cost = finder.path_search("node_1", "node_100")

        # Detour limit settings
        finder.penalty_weight = 0.5   # damp the penalty influence to 50%
        finder.penalty_cap = 3.0      # cap the penalty at 3.0x
        finder.max_detour_ratio = 1.5  # allow up to 1.5x the static route
    """

    MAX_COST = 1e15

    def __init__(self, network: Network):
        self.network = network
        self.line_speed: float = 2.6667   # m/s (Java default)
        self.curve_speed: float = 0.8     # m/s
        self.vehicle_length: float = 1.0
        self.vehicle_width: float = 1.0

        # ─── Detour limit parameters (B: Bounded Penalty) ───
        # effective_penalty = 1 + penalty_weight * min(raw_penalty - 1, penalty_cap)
        # raw_penalty=1.0 (default) gives effective=1.0 (no effect)
        self.penalty_weight: float = 1.0  # 0.0~1.0, penalty influence ratio
        self.penalty_cap: float = 5.0     # penalty upper bound (in terms of raw_penalty - 1)

        # ─── Detour limit parameters (A: Max Detour Ratio) ───
        self.max_detour_ratio: float = 0.0  # 0 disables. 1.5 = allow up to 1.5x the static route

        # ─── Static route cache ───
        # {(from_node, to_node): (path, cost)}
        self._static_cache: Dict[Tuple[str, str], Tuple[List[str], float]] = {}

        # ─── Dynamic route cache (penalty-aware, TTL based) ───
        # {(from_node, to_node): (sim_time_cached, path, cost)}
        # If the same pair is requested again within the TTL, Dijkstra is skipped.
        self._dynamic_cache: Dict[Tuple[str, str], Tuple[float, List[str], float]] = {}
        self._dynamic_cache_ttl: float = 3.0    # sim-seconds
        self._dynamic_cache_max: int = 4096     # memory limit (number of entries)
        self._sim_time: float = 0.0             # synchronized by route_manager

        # ─── Detour limit statistics ───
        self.detour_fallback_count: int = 0
        self.total_search_count: int = 0
        self.cache_hit_count: int = 0

        # External routing cost function. None means the default cost formula is used.
        self.custom_cost_function = None

        # ─── scipy C Dijkstra engine (fast_pathfinder) ───
        # Built lazily on the first cost_search call. Falls back to the pure-Python
        # path when scipy is missing or the UFAST_FAST_ROUTE=0 environment variable is set.
        self._fast_engine = None
        self._fast_engine_disabled = False

    def initialize(
        self,
        line_speed: float = 2.6667,
        curve_speed: float = 0.8,
        vehicle_length: float = 1.0,
        vehicle_width: float = 1.0,
    ):
        """
        Initialize the network: compute travel times between nodes.
        Java: RouteManager.Initialize()

        For each adjacent node pair in every section, move_in_time = distance / speed.
        LINE sections use line_speed, CURVE sections use curve_speed.
        """
        self.line_speed = line_speed
        self.curve_speed = curve_speed
        self.vehicle_length = vehicle_length
        self.vehicle_width = vehicle_width

        # Speeds/travel times change, so the C engine is rebuilt on the next query
        self._fast_engine = None

        # Compute travel times for the node pairs of each section
        for section in self.network.sections.values():
            section.make_hash_table()

            speed = (self.line_speed
                     if section.section_type == "LINE"
                     else self.curve_speed)

            if speed <= 0:
                continue

            # Consecutive node pairs within the section
            for j in range(1, section.node_count):
                prev_name = section.get_node(j - 1)
                curr_name = section.get_node(j)
                if prev_name is None or curr_name is None:
                    continue

                prev_node = self.network.nodes.get(prev_name)
                curr_node = self.network.nodes.get(curr_name)
                if prev_node is None or curr_node is None:
                    continue

                distance = prev_node.get_length(curr_node)
                move_in_time = distance / speed

                # Forward: prev → curr
                curr_node.set_move_in_time(move_in_time, prev_name, forward=True)
                # Backward: curr → prev
                prev_node.set_move_in_time(move_in_time, curr_name, forward=False)

    def _get_fast_engine(self):
        """Return the C Dijkstra engine, or None if it cannot be used (pure-Python path)."""
        if self.custom_cost_function is not None or self._fast_engine_disabled:
            return None
        if self._fast_engine is None:
            import os
            if os.environ.get("UFAST_FAST_ROUTE", "1") == "0":
                self._fast_engine_disabled = True
                return None
            try:
                from .fast_pathfinder import FastCostEngine
                self._fast_engine = FastCostEngine(self)
            except ImportError:
                print("[FastRoute] scipy not installed → using pure-Python cost_search")
                self._fast_engine_disabled = True
                return None
        return self._fast_engine

    def notify_penalty_changed(self, node_names) -> None:
        """traffic_penalty change notification (bridge hook). Invalidates engine weights."""
        if self._fast_engine is not None:
            self._fast_engine.mark_dirty(node_names)

    def notify_all_penalties_changed(self) -> None:
        if self._fast_engine is not None:
            self._fast_engine.mark_all_dirty()

    def _effective_penalty(self, raw_penalty: float) -> float:
        """
        Bounded penalty computation (approach B).

        If raw_penalty is 1.0 (default), the result is also 1.0.
        A higher raw_penalty raises the cost, but is bounded by cap and weight.

        Formula: 1 + weight * min(raw - 1, cap)
        e.g. raw=4.0, weight=0.5, cap=3.0 → 1 + 0.5 * min(3.0, 3.0) = 2.5
        e.g. raw=10.0, weight=0.5, cap=3.0 → 1 + 0.5 * min(9.0, 3.0) = 2.5 (capped)
        e.g. raw=1.0 → 1.0 (no penalty)
        """
        if raw_penalty <= 1.0:
            return 1.0
        excess = raw_penalty - 1.0
        bounded = min(excess, self.penalty_cap)
        return 1.0 + self.penalty_weight * bounded

    def clear_static_cache(self):
        """Clear the static route cache. Call when the network structure changes."""
        self._static_cache.clear()
        self._dynamic_cache.clear()   # clearing the static cache also clears the dynamic cache

    def clear_dynamic_cache(self):
        """Clear only the dynamic route cache. Call after large network penalty changes."""
        self._dynamic_cache.clear()

    def set_custom_cost_function(self, cost_function):
        """Inject an external routing cost function. None reverts to the default cost formula."""
        self.custom_cost_function = cost_function
        self.clear_static_cache()

    def _calculate_edge_cost(self, move_time: float, raw_penalty: float, section: NetworkSection, from_node: Node, to_node: Node, context: str = "") -> float:
        """Compute the edge cost. Without a custom function, use move_time * effective_penalty as before."""
        default_cost = move_time * self._effective_penalty(raw_penalty)
        fn = self.custom_cost_function
        if fn is None:
            return default_cost

        func = getattr(fn, "calculate_cost", None)
        if not callable(func):
            func = getattr(fn, "cost", None)
        if not callable(func) and callable(fn):
            func = fn
        if not callable(func):
            return default_cost

        call_specs = [
            ((), {
                "move_time": move_time,
                "raw_penalty": raw_penalty,
                "effective_penalty": self._effective_penalty(raw_penalty),
                "section": section,
                "from_node": from_node,
                "to_node": to_node,
                "default_cost": default_cost,
                "context": context,
            }),
            ((move_time, raw_penalty, section, from_node, to_node, default_cost), {}),
            ((move_time, raw_penalty, default_cost), {}),
            ((move_time, raw_penalty), {}),
        ]
        last_error = None
        for args, kwargs in call_specs:
            try:
                value = func(*args, **kwargs)
                return float(value)
            except TypeError as e:
                last_error = e
                continue
            except Exception as e:
                print(f"[RoutingCost] ⚠️ custom cost function error → using default cost: {e}")
                return default_cost
        if last_error is not None:
            print(f"[RoutingCost] ⚠️ custom cost function signature mismatch → using default cost: {last_error}")
        return default_cost

    def path_search_static(
        self,
        from_node_name: str,
        to_node_name: str,
    ) -> Tuple[List[str], float]:
        """
        Static shortest-route search (ignores penalties).

        Finds the route using pure travel time only, ignoring traffic_penalty.
        The result is cached so the same pair is not recomputed.
        Serves as the baseline for the detour ratio comparison (approach A).
        """
        cache_key = (from_node_name, to_node_name)
        if cache_key in self._static_cache:
            return self._static_cache[cache_key]

        # Temporarily disable the penalty parameters and search
        saved_weight = self.penalty_weight
        saved_cap = self.penalty_cap
        self.penalty_weight = 0.0  # ignore penalties entirely → effective=1.0

        path, cost = self._dijkstra(from_node_name, to_node_name)

        self.penalty_weight = saved_weight
        self.penalty_cap = saved_cap

        self._static_cache[cache_key] = (path, cost)
        return path, cost

    def _reset_search(self):
        """Reset the Dijkstra state of every node. Java: ResetRouteSearch()"""
        for node in self.network.nodes.values():
            node.reset_search(self.MAX_COST)

    def path_search(
        self,
        from_node_name: str,
        to_node_name: str,
    ) -> Tuple[List[str], float]:
        """
        1:1 shortest-route search (with detour limiting).

        Flow:
          1. Search the dynamic route with bounded penalty (B) applied.
          2. If max_detour_ratio (A) is set:
             - Obtain the static shortest route (penalties ignored), using the cache.
             - If the dynamic route's node count exceeds ratio times the static route's,
               fall back to the static route.

        Returns:
            (list of node names on the route, total travel time)
            ([], -1.0) if no route is found
        """
        self.total_search_count += 1

        # ── Dynamic cache lookup ────────────────────────────
        # If the same (from, to) pair is requested again within the TTL, skip Dijkstra.
        # Even if penalties change often, reusing the previous result within the TTL saves CPU.
        _cache_key = (from_node_name, to_node_name)
        _cached = self._dynamic_cache.get(_cache_key)
        if _cached is not None:
            _ct, _cp, _cc = _cached
            if self._sim_time - _ct <= self._dynamic_cache_ttl:
                self.cache_hit_count += 1
                return _cp, _cc

        # Dynamic route search (bounded penalty applied)
        dynamic_path, dynamic_cost = self._dijkstra(from_node_name, to_node_name)

        if not dynamic_path:
            return [], -1.0

        # Approach A: detour ratio check
        if self.max_detour_ratio > 0:
            static_path, static_cost = self.path_search_static(
                from_node_name, to_node_name,
            )

            if static_path and len(static_path) > 1:
                # Judge the detour ratio by node count
                ratio = len(dynamic_path) / len(static_path)

                if ratio > self.max_detour_ratio:
                    # Excessive detour → fall back to the static route
                    self.detour_fallback_count += 1
                    dynamic_path, dynamic_cost = static_path, static_cost

        # ── Store in the dynamic cache ──────────────────────
        if len(self._dynamic_cache) >= self._dynamic_cache_max:
            # Simple size limit: evict the stale entries
            cutoff = self._sim_time - self._dynamic_cache_ttl
            stale = [k for k, v in self._dynamic_cache.items() if v[0] <= cutoff]
            for k in stale:
                del self._dynamic_cache[k]
            # If still too large, clear everything
            if len(self._dynamic_cache) >= self._dynamic_cache_max:
                self._dynamic_cache.clear()
        self._dynamic_cache[_cache_key] = (self._sim_time, dynamic_path, dynamic_cost)

        return dynamic_path, dynamic_cost

    def _dijkstra(
        self,
        from_node_name: str,
        to_node_name: str,
    ) -> Tuple[List[str], float]:
        """
        Core Dijkstra algorithm.
        Java: RouteManager.PathSearch()

        Searches with costs that include the bounded penalty (_effective_penalty).
        """
        from_node = self.network.nodes.get(from_node_name)
        to_node = self.network.nodes.get(to_node_name)

        if from_node is None or to_node is None:
            return [], -1.0

        # Same node
        if from_node_name == to_node_name:
            return [from_node_name], 0.0

        self._reset_search()

        # heapq: (arrived_time, node_name)
        heap: List[Tuple[float, str]] = []

        from_node.arrived_time = 0.0
        from_node.arrived_length = 0.0
        heapq.heappush(heap, (0.0, from_node_name))

        arrived = False

        while heap:
            curr_time, curr_name = heapq.heappop(heap)
            curr_node = self.network.nodes[curr_name]

            # A better route has already been recorded
            if curr_time > curr_node.arrived_time:
                continue

            # Arrival check
            if curr_name == to_node_name:
                arrived = True
                break

            # Explore every section the current node belongs to
            for section_name in curr_node.section_list:
                section = self.network.sections.get(section_name)
                if section is None:
                    continue

                pos = section.find_node(curr_name)
                if pos < 0:
                    continue

                # Forward exploration (pos+1, pos+2, ...)
                self._explore_direction(
                    section, pos, 1, curr_node, heap,
                    from_node_name, to_node_name,
                )

                # For two-way sections also explore backward
                if section.two_way:
                    self._explore_direction(
                        section, pos, -1, curr_node, heap,
                        from_node_name, to_node_name,
                    )

        if not arrived:
            return [], -1.0

        # Trace the route back
        path = []
        node_name = to_node_name
        while node_name is not None:
            path.append(node_name)
            node_name = self.network.nodes[node_name].prev_node
        path.reverse()

        return path, to_node.arrived_time

    def _explore_direction(
        self,
        section: NetworkSection,
        start_pos: int,
        direction: int,  # +1 = forward, -1 = backward
        search_node: Node,
        heap: List[Tuple[float, str]],
        from_node_name: str,
        to_node_name: str,
    ):
        """
        Explore neighbouring nodes in one direction within a section.
        Ports the inner for loop of Java PathSearch.
        """
        d_time = search_node.arrived_time

        # ── perf(#2): inner-loop localization — replace the node_count property /
        # get_node method calls (hundreds of millions each) with direct list indexing,
        # carry prev_node forward instead of re-fetching it, and hoist the speed
        # computation out of the loop. Identical results (pure refactor).
        node_list = section.node_list
        n = len(node_list)
        gnodes = self.network.nodes
        speed = self.line_speed if section.section_type == "LINE" else self.curve_speed
        push = heapq.heappush

        prev_name = node_list[start_pos]
        prev_node = gnodes.get(prev_name)
        if prev_node is None:
            return
        j = start_pos + direction

        while 0 <= j < n:
            next_name = node_list[j]

            next_node = gnodes.get(next_name)
            if next_node is None:
                break

            # Prevent returning to the origin node (Java: if(Node == FromNode) break)
            if next_name == from_node_name:
                break

            # Compute the travel time
            move_time = prev_node.move_in_times.get(next_name)
            if move_time is None:
                # If move_in_time was not set, compute from distance
                distance = prev_node.get_length(next_node)
                move_time = distance / speed if speed > 0 else self.MAX_COST

            # Apply the traffic penalty (bounded) or the custom routing cost function
            edge_cost = self._calculate_edge_cost(
                move_time, next_node.traffic_penalty, section, prev_node, next_node, context="path_search"
            )
            new_time = d_time + edge_cost

            if next_node.set_arrived_time(new_time, prev_name):
                d_time = new_time
                prev_name = next_name
                prev_node = next_node  # carry-forward (was gnodes.get(prev_name))

                # NOTE:
                # The former implementation pushed only the section end node onto the heap,
                # so it could not expand into other sections from a junction / branching
                # node in the middle of a section, and reachable routes came back as FAIL.
                #
                # For Dijkstra correctness, every updated node is pushed onto the heap.
                # Stale entries are filtered naturally by the outer loop's
                #   if curr_time > curr_node.arrived_time: continue
                push(heap, (new_time, next_name))

                if next_name == to_node_name:
                    return
            else:
                break  # a better route already exists

            j += direction

    def cost_search(
        self,
        from_node_name: str,
        to_node_names: List[str],
        context: str = "cost_search",
    ) -> Dict[str, Tuple[float, float, List[str]]]:
        """
        1:N Dijkstra - compute the costs from one origin node to several destination nodes.
        Java: RouteManager.CostSearch()

        Args:
            from_node_name: origin node name
            to_node_names: list of destination node names

        Returns:
            {to_node_name: (time, distance, path)} dictionary.
            Unreachable nodes return (MAX_COST, MAX_COST, []).
        """
        engine = self._get_fast_engine()
        if engine is not None:
            return engine.cost_search(from_node_name, to_node_names)

        from_node = self.network.nodes.get(from_node_name)
        if from_node is None:
            return {}

        # Set of nodes still to be found
        remaining = set()
        for name in to_node_names:
            if name != from_node_name and name in self.network.nodes:
                remaining.add(name)

        self._reset_search()

        heap: List[Tuple[float, str]] = []
        from_node.arrived_time = 0.0
        from_node.arrived_length = 0.0
        heapq.heappush(heap, (0.0, from_node_name))

        while heap and remaining:
            curr_time, curr_name = heapq.heappop(heap)
            curr_node = self.network.nodes[curr_name]

            if curr_time > curr_node.arrived_time:
                continue

            # Destination node found
            remaining.discard(curr_name)

            # Explore all adjacent sections
            for section_name in curr_node.section_list:
                section = self.network.sections.get(section_name)
                if section is None:
                    continue

                pos = section.find_node(curr_name)
                if pos < 0:
                    continue

                # Forward
                self._cost_explore_direction(
                    section, pos, 1, curr_node, heap, remaining, context,
                )

                # Backward as well for two-way sections
                if section.two_way:
                    self._cost_explore_direction(
                        section, pos, -1, curr_node, heap, remaining, context,
                    )

        # Collect results
        results: Dict[str, Tuple[float, float, List[str]]] = {}
        for name in to_node_names:
            if name == from_node_name:
                results[name] = (0.0, 0.0, [from_node_name])
                continue

            node = self.network.nodes.get(name)
            if node is None or node.arrived_time >= self.MAX_COST:
                results[name] = (self.MAX_COST, self.MAX_COST, [])
                continue

            # Trace the route back
            path = []
            trace_name = name
            while trace_name is not None:
                path.append(trace_name)
                trace_name = self.network.nodes[trace_name].prev_node
            path.reverse()

            results[name] = (node.arrived_time, node.arrived_length, path)

        return results

    def _cost_explore_direction(
        self,
        section: NetworkSection,
        start_pos: int,
        direction: int,
        search_node: Node,
        heap: List[Tuple[float, str]],
        remaining: set,
        context: str = "cost_search",
    ):
        """
        Directional exploration for CostSearch.
        Similar to PathSearch, but there are several destination nodes
        and the distance (length) is tracked as well.
        """
        d_time = search_node.arrived_time
        d_length = search_node.arrived_length

        prev_name = section.get_node(start_pos)
        j = start_pos + direction

        while 0 <= j < section.node_count:
            next_name = section.get_node(j)
            if next_name is None:
                break

            next_node = self.network.nodes.get(next_name)
            prev_node = self.network.nodes.get(prev_name)
            if next_node is None or prev_node is None:
                break

            # Compute travel time and distance
            move_time = prev_node.move_in_times.get(next_name)
            distance = prev_node.get_length(next_node)

            if move_time is None:
                speed = (self.line_speed
                         if section.section_type == "LINE"
                         else self.curve_speed)
                move_time = distance / speed if speed > 0 else self.MAX_COST

            edge_cost = self._calculate_edge_cost(
                move_time, next_node.traffic_penalty, section, prev_node, next_node, context=context
            )
            new_time = d_time + edge_cost
            new_length = d_length + distance

            if next_node.set_cost_arrived_time(new_time, new_length, prev_name):
                d_time = new_time
                d_length = new_length
                prev_name = next_name

                # Dijkstra correctness: a target's shortest distance is final only when it is
                # popped from the heap. Doing remaining.discard / early-return at discovery
                # (relaxation) time could terminate on a non-optimal (longer) route → only
                # the discard(curr_name) at pop time in the outer cost_search loop is trusted.
                heapq.heappush(heap, (new_time, next_name))
            else:
                break

            j += direction

    def build_eq_cost_table(
        self,
        eq_names: Optional[List[str]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        """
        Build the equipment-to-equipment cost table.
        Java: Rail.makeFromToCostTable()

        Args:
            eq_names: list of equipment names. None means every mapped equipment.

        Returns:
            {"from_node_to_node": (time, distance)} dictionary
        """
        if eq_names is None:
            eq_names = list(self.network.eq_to_node.keys())

        # Equipment → node conversion
        eq_node_pairs = []
        for eq in eq_names:
            node_name = self.network.eq_to_node.get(eq)
            if node_name and node_name in self.network.nodes:
                eq_node_pairs.append((eq, node_name))

        to_node_names = [node_name for _, node_name in eq_node_pairs]

        cost_table: Dict[str, Tuple[float, float]] = {}

        for from_eq, from_node in eq_node_pairs:
            results = self.cost_search(from_node, to_node_names)

            for to_eq, to_node in eq_node_pairs:
                if to_node in results:
                    time, length, _ = results[to_node]
                    key = f"{from_node}_{to_node}"
                    cost_table[key] = (time, length)

        return cost_table
