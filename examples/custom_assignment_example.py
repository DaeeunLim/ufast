"""
커스텀 Assignment 전략 예시.

반환 규칙:
- select(target_section_id, idle_ohts, route_manager, bridge, vehicle_controller)를 정의한다.
- 반환값은 선택된 OHT 객체 또는 OHT 이름 문자열이다.
- None을 반환하면 기존 기본 Assignment로 fallback된다.
"""


class AssignmentStrategy:
    def select(self, target_section_id, idle_ohts, route_manager=None, bridge=None, vehicle_controller=None):
        if not idle_ohts:
            return None

        # 같은 section에 있는 OHT를 최우선 선택
        for oht in idle_ohts:
            if oht.current_section_id == target_section_id:
                return oht

        # bridge가 있으면 실제 route cost 기준 선택
        if bridge is not None:
            best_oht = None
            best_cost = float("inf")
            for oht in idle_ohts:
                _, cost, _ = bridge.estimate_section_route_cost(
                    oht.current_section_id,
                    target_section_id,
                    context="CUSTOM_ASSIGNMENT_PROBE",
                )
                if 0 <= cost < best_cost:
                    best_cost = cost
                    best_oht = oht
            if best_oht is not None:
                return best_oht

        # fallback: section id 차이가 가장 작은 OHT
        return min(idle_ohts, key=lambda o: abs(o.current_section_id - target_section_id))
