"""
Custom assignment strategy example.

Return rules:
- Define select(target_section_id, idle_ohts, route_manager, bridge, vehicle_controller).
- The return value is the selected OHT object or the OHT name string.
- Returning None falls back to the built-in default assignment.
"""


class AssignmentStrategy:
    def select(self, target_section_id, idle_ohts, route_manager=None, bridge=None, vehicle_controller=None):
        if not idle_ohts:
            return None

        # Prefer an OHT that is already in the same section
        for oht in idle_ohts:
            if oht.current_section_id == target_section_id:
                return oht

        # If a bridge is available, select by actual route cost
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

        # fallback: the OHT with the smallest section id difference
        return min(idle_ohts, key=lambda o: abs(o.current_section_id - target_section_id))
