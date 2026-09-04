"""
kinematics.py — OHT 가감속 운동학 이동시간 모델.

LogiFabSim(Rank & Betker, 2025 IFAC)의 엣지 이송시간 모델을 따라 재구현한다
(reimplemented following Rank & Betker, 2025). 각 엣지를 가속→순항→감속의
트래피저이드 속도 프로파일로 모델링하고, 엣지 경계에서 속도가 다음 엣지
속도제한으로 이어진다(LogiFabSim 단순화). 경로 출발/도착은 정지(속도 0).
결과값은 LogiFabSim 의 calculate_edge_travel_time 과 일치하도록 맞췄다.

목적: U-FAST 의 free-flow 이송시간 baseline 을 LogiFabSim 의 t_ij 와 동일하게
맞춰, 두 시뮬레이터의 차이를 '혼잡 모델' 하나로 고정하기 위함.

LogiFabSim 원본 대비 유일한 deviation: 삼각형 프로파일의 sqrt 인자가 음수가
될 때 0 으로 clamp(런타임 crash 방지). 정상 데이터에서는 발생하지 않는다.
"""
from __future__ import annotations
import math
from typing import List, Tuple


class VehicleKinematics:
    """차량 제원(최고속도/가속도/감속도) 기반 운동학 이동시간 계산기."""

    def __init__(self, max_speed: float, acceleration: float, deceleration: float):
        self.max_speed = max_speed
        self.acceleration = acceleration
        self.deceleration = deceleration

    def edge_time(self, distance: float, speed_limit: float,
                  initial_speed: float, speed_limit_next_edge: float) -> float:
        """
        엣지 1개 통과 시간 — LogiFabSim calculate_edge_travel_time 과 동일 semantics.
        """
        a = self.acceleration
        d = self.deceleration
        achievable_speed = min(self.max_speed, speed_limit)

        # 도달 가능 속도까지 가속
        acceleration_time = (achievable_speed - initial_speed) / a
        acceleration_distance = (initial_speed * acceleration_time
                                 + 0.5 * a * acceleration_time ** 2)
        # 다음 엣지 속도제한까지 감속
        deceleration_time = (achievable_speed - speed_limit_next_edge) / d
        deceleration_distance = (achievable_speed * deceleration_time
                                 - 0.5 * d * deceleration_time ** 2)

        distance_cruise = distance - acceleration_distance - deceleration_distance
        if distance_cruise < 0:
            # 엣지가 짧아 순항 불가 — 삼각형 프로파일
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
        경로 전체 이송시간. edges: [(거리, 속도제한), ...] 순서대로.
        엣지 경계 진입속도 = 직전 엣지의 다음-속도제한(=해당 엣지 속도제한),
        출발/도착은 정지 — LogiFabSim calc_point_to_point_travel_time 과 동일 semantics.
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
