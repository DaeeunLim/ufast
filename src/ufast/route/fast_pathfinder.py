"""scipy 기반 C 다익스트라 경로비용 엔진.

PathFinder.cost_search 와 동일한 비용/거리를 compiled C
(scipy.sparse.csgraph.dijkstra) 로 계산한다. 동률(같은 비용) 경로가
여럿일 때 선택이 기존 구현과 다를 수 있다는 점 외에는 결과가 같다.

- 레일 토폴로지는 불변이므로 CSR 행렬 구조는 1회만 구축한다.
- traffic_penalty 변경은 bridge → PathFinder.notify_penalty_changed 훅으로
  전달받아, 해당 노드로 들어오는 엣지 가중치만 제자리 갱신한다.
- custom_cost_function 이 설정된 실행에서는 PathFinder 가 이 엔진을
  사용하지 않는다 (엣지별 파이썬 콜백은 C 경로로 표현 불가).
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

        # ── 섹션 체인 → 방향 엣지 전개 (_cost_explore_direction 과 동일 의미) ──
        # 정방향: node[j-1] → node[j]. two_way 섹션은 역방향 엣지도 추가.
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
                    # 병렬 엣지: penalty 는 도착노드 기준이라 두 엣지에 동일하게
                    # 곱해지므로 move_time 이 작은 쪽이 항상 최소 비용이다.
                    if move_time < old[0]:
                        edge_best[key] = (move_time, distance)
        if dup_count:
            print(f"[FastRoute] 병렬 엣지 {dup_count}개 → 최소 move_time 으로 병합")

        # (src, dst) 정렬 순서로 배열 고정 → CSR data[i] == edge i
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

        # 도착노드별 유입 엣지 위치 (penalty 갱신용)
        self._in_pos: Dict[int, np.ndarray] = {}
        by_dst: Dict[int, List[int]] = {}
        for pos, v in enumerate(self._dst):
            by_dst.setdefault(int(v), []).append(pos)
        for v, positions in by_dst.items():
            self._in_pos[v] = np.array(positions, dtype=np.int64)

        self._penalty_params = (pathfinder.penalty_weight, pathfinder.penalty_cap)
        self._dirty_all = True          # 최초 1회 전체 가중치 계산
        self._dirty_nodes: set = set()

    # ── penalty 동기화 ────────────────────────────────────────────────
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

    # ── 쿼리 ──────────────────────────────────────────────────────────
    def cost_search(
        self,
        from_node_name: str,
        to_node_names: List[str],
    ) -> Dict[str, Tuple[float, float, List[str]]]:
        """PathFinder.cost_search 와 동일한 반환 형식의 1:N 최단비용."""
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
