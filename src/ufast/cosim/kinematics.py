"""
kinematics.py — OHT acceleration/deceleration kinematics travel-time model.

Reimplements the edge transport-time model of LogiFabSim (Rank & Betker, 2025 IFAC)
(reimplemented following Rank & Betker, 2025). Each edge is modelled as a
trapezoidal speed profile of accelerate → cruise → decelerate, and at an edge
boundary the speed carries over into the next edge's speed limit (LogiFabSim
simplification). A path starts and ends at rest (speed 0).
The results are matched to LogiFabSim's calculate_edge_travel_time.

Purpose: make U-FAST's free-flow transport-time baseline identical to LogiFabSim's
t_ij, so that the only difference between the two simulators is the congestion
model.

The only deviation from the LogiFabSim original: the sqrt argument of the
triangular profile is clamped to 0 when it would be negative (prevents a runtime
crash). This does not occur with well-formed data.
"""
from __future__ import annotations
import math
from typing import List, Tuple


class VehicleKinematics:
    """Kinematic travel-time calculator based on vehicle specs (max speed / acceleration / deceleration)."""

    def __init__(self, max_speed: float, acceleration: float, deceleration: float):
        self.max_speed = max_speed
        self.acceleration = acceleration
        self.deceleration = deceleration

    def edge_time(self, distance: float, speed_limit: float,
                  initial_speed: float, speed_limit_next_edge: float) -> float:
        """
        Traversal time of a single edge — same semantics as LogiFabSim calculate_edge_travel_time.
        """
        a = self.acceleration
        d = self.deceleration
        achievable_speed = min(self.max_speed, speed_limit)

        # accelerate to the achievable speed
        acceleration_time = (achievable_speed - initial_speed) / a
        acceleration_distance = (initial_speed * acceleration_time
                                 + 0.5 * a * acceleration_time ** 2)
        # decelerate to the next edge's speed limit
        deceleration_time = (achievable_speed - speed_limit_next_edge) / d
        deceleration_distance = (achievable_speed * deceleration_time
                                 - 0.5 * d * deceleration_time ** 2)

        distance_cruise = distance - acceleration_distance - deceleration_distance
        if distance_cruise < 0:
            # edge too short to cruise — triangular profile
            time_cruise = 0.0
            v_peak = self._v_max_triangular(a, d, distance,
                                            initial_speed, speed_limit_next_edge)
            acceleration_time = (v_peak - initial_speed) / a
            deceleration_time = (speed_limit_next_edge - v_peak) / d
        time_cruise = distance_cruise / achievable_speed if distance_cruise > 0 else 0.0

        return acceleration_time + time_cruise + deceleration_time

    @staticmethod
    def _v_max_triangular(a: float, d: float, s: float,
                          v_0: float, v_end: float) -> float:
        s_eff = (s - (v_end ** 2 - v_0 ** 2) / (2 * a)
                 - (v_end ** 2 - v_0 ** 2) / (2 * d))
        inner = v_0 ** 2 + 2 * a * s_eff * d / (a + d)
        return math.sqrt(inner if inner > 0 else 0.0)

    def path_time(self, edges: List[Tuple[float, float]]) -> float:
        """
        Transport time over a whole path. edges: [(distance, speed_limit), ...] in order.
        Entry speed at an edge boundary = previous edge's next-speed-limit (= this edge's
        speed limit); start/end at rest — same semantics as LogiFabSim
        calc_point_to_point_travel_time.
        """
        if not edges:
            return 0.0
        total = 0.0
        n = len(edges)
        current_speed = 0.0
        for i in range(n):
            dist, slim = edges[i]
            next_slim = 0.0 if i == n - 1 else edges[i + 1][1]
            total += self.edge_time(dist, slim, current_speed, next_slim)
            current_speed = next_slim
        return max(0.0, total)
