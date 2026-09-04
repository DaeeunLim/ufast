"""scipy-based C Dijkstra route-cost engine.

Computes the same cost/distance as PathFinder.cost_search using compiled C
(scipy.sparse.csgraph.dijkstra). The results are identical except that,
when several paths tie on cost, the chosen path may differ from the
original implementation.

- The rail topology is immutable, so the CSR matrix structure is built once.
- traffic_penalty changes arrive via the bridge → PathFinder.notify_penalty_changed
  hook; only the weights of edges entering the affected node are updated in place.
- In runs where custom_cost_function is set, PathFinder does not use this
  engine (a per-edge Python callback cannot be expressed in the C path).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra


class FastCostEngine:

    def __init__(self, pathfinder):
        self.pf = pathfinder
        network = pathfinder.network

        self.node_names: List[str] = list(network.nodes.keys())
        self.idx: Dict[str, int] = {n: i for i, n in enumerate(self.node_names)}
        n_nodes = len(self.node_names)

        # ── Expand section chains into directed edges (same semantics as _cost_explore_direction) ──
        # Forward: node[j-1] → node[j]. two_way sections also add the reverse edge.
        edge_best: Dict[Tuple[int, int], Tuple[float, float]] = {}  # (u,v) → (move_time, dist)
        dup_count = 0
        for section in network.sections.values():
            speed = (pathfinder.line_speed
                     if section.section_type == "LINE"
                     else pathfinder.curve_speed)
            pairs = []
            for j in range(1, section.node_count):
                a, b = section.get_node(j - 1), section.get_node(j)
                if a is None or b is None:
                    continue
                pairs.append((a, b))
                if section.two_way:
                    pairs.append((b, a))
            for prev_name, next_name in pairs:
                prev_node = network.nodes.get(prev_name)
                next_node = network.nodes.get(next_name)
                if prev_node is None or next_node is None:
                    continue
                distance = prev_node.get_length(next_node)
                move_time = prev_node.move_in_times.get(next_name)
                if move_time is None:
                    move_time = distance / speed if speed > 0 else pathfinder.MAX_COST
                key = (self.idx[prev_name], self.idx[next_name])
                old = edge_best.get(key)
                if old is None:
                    edge_best[key] = (move_time, distance)
                else:
                    dup_count += 1
                    # Parallel edges: the penalty is keyed on the destination node, so it
                    # multiplies both edges equally; the smaller move_time is always cheapest.
                    if move_time < old[0]:
                        edge_best[key] = (move_time, distance)
        if dup_count:
            print(f"[FastRoute] merged {dup_count} parallel edges → keeping the minimum move_time")

        # Freeze arrays in sorted (src, dst) order → CSR data[i] == edge i
        keys = sorted(edge_best.keys())
        self._src = np.array([k[0] for k in keys], dtype=np.int32)
        self._dst = np.array([k[1] for k in keys], dtype=np.int32)
        self._move_time = np.array([edge_best[k][0] for k in keys], dtype=np.float64)
        self._edge_dist = np.array([edge_best[k][1] for k in keys], dtype=np.float64)
        self._pair_dist = {k: edge_best[k][1] for k in keys}

        indptr = np.zeros(n_nodes + 1, dtype=np.int64)
        np.add.at(indptr, self._src + 1, 1)
        np.cumsum(indptr, out=indptr)
        self._csr = csr_matrix(
            (self._move_time.copy(), self._dst.copy(), indptr),
            shape=(n_nodes, n_nodes),
        )

        # Positions of incoming edges per destination node (for penalty updates)
        self._in_pos: Dict[int, np.ndarray] = {}
        by_dst: Dict[int, List[int]] = {}
        for pos, v in enumerate(self._dst):
            by_dst.setdefault(int(v), []).append(pos)
        for v, positions in by_dst.items():
            self._in_pos[v] = np.array(positions, dtype=np.int64)

        self._penalty_params = (pathfinder.penalty_weight, pathfinder.penalty_cap)
        self._dirty_all = True          # compute all weights once on first use
        self._dirty_nodes: set = set()

    # ── penalty synchronization ───────────────────────────────────────
    def mark_dirty(self, node_names) -> None:
        for name in node_names:
            self._dirty_nodes.add(name)

    def mark_all_dirty(self) -> None:
        self._dirty_all = True
        self._dirty_nodes.clear()

    def _effective_penalty_scalar(self, raw: float) -> float:
        if raw <= 1.0:
            return 1.0
        weight, cap = self._penalty_params
        return 1.0 + weight * min(raw - 1.0, cap)

    def _sync(self) -> None:
        params = (self.pf.penalty_weight, self.pf.penalty_cap)
        if params != self._penalty_params:
            self._penalty_params = params
            self._dirty_all = True

        nodes = self.pf.network.nodes
        data = self._csr.data
        if self._dirty_all:
            raw = np.array(
                [nodes[name].traffic_penalty for name in self.node_names],
                dtype=np.float64,
            )
            weight, cap = self._penalty_params
            eff = np.where(raw <= 1.0, 1.0,
                           1.0 + weight * np.minimum(raw - 1.0, cap))
            data[:] = self._move_time * eff[self._dst]
            self._dirty_all = False
            self._dirty_nodes.clear()
            return

        if self._dirty_nodes:
            for name in self._dirty_nodes:
                v = self.idx.get(name)
                if v is None:
                    continue
                positions = self._in_pos.get(v)
                if positions is None:
                    continue
                eff = self._effective_penalty_scalar(nodes[name].traffic_penalty)
                data[positions] = self._move_time[positions] * eff
            self._dirty_nodes.clear()

    # ── queries ───────────────────────────────────────────────────────
    def cost_search(
        self,
        from_node_name: str,
        to_node_names: List[str],
    ) -> Dict[str, Tuple[float, float, List[str]]]:
        """1:N minimum cost with the same return format as PathFinder.cost_search."""
        i = self.idx.get(from_node_name)
        if i is None:
            return {}

        self._sync()
        times, predecessors = dijkstra(
            self._csr, directed=True, indices=i, return_predecessors=True,
        )

        max_cost = self.pf.MAX_COST
        results: Dict[str, Tuple[float, float, List[str]]] = {}
        for name in to_node_names:
            if name == from_node_name:
                results[name] = (0.0, 0.0, [from_node_name])
                continue
            j = self.idx.get(name)
            if j is None:
                results[name] = (max_cost, max_cost, [])
                continue
            t = times[j]
            if not np.isfinite(t):
                results[name] = (max_cost, max_cost, [])
                continue

            path_idx = [j]
            k = j
            while k != i:
                k = int(predecessors[k])
                if k < 0:
                    break
                path_idx.append(k)
            if path_idx[-1] != i:
                results[name] = (max_cost, max_cost, [])
                continue
            path_idx.reverse()

            length = 0.0
            for a, b in zip(path_idx, path_idx[1:]):
                length += self._pair_dist[(a, b)]
            results[name] = (
                float(t), length,
                [self.node_names[k] for k in path_idx],
            )
        return results
