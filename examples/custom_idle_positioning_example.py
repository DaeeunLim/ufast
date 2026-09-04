"""
Custom idle positioning strategy example.

Return rules:
- Define plan_reposition(oht, current_time, current_node, route_manager, bridge, event_handler, vehicle_controller).
- The return value is one of the following.
  1) target section id: 123
  2) target node: "N_..." or a route_manager node string
  3) section path: [123, 124, 125]
  4) dict: {"target_section_id": 123} or {"path": [123, 124]}
- Returning None falls back to the built-in default idle positioning.
"""


class IdlePositioningStrategy:
    def plan_reposition(
        self,
        oht,
        current_time,
        current_node,
        route_manager=None,
        bridge=None,
        event_handler=None,
        vehicle_controller=None,
    ):
        if vehicle_controller is None or bridge is None:
            return None

        ds = vehicle_controller.data_set
        cur_idx = ds.section_id_to_index.get(oht.current_section_id)
        if cur_idx is None:
            return None

        cur_sec = ds.sections[cur_idx]
        candidates = []
        for nxt in cur_sec.next_sections:
            nxt_idx = ds.section_id_to_index.get(nxt)
            if nxt_idx is None:
                continue
            nxt_sec = ds.sections[nxt_idx]
            if not nxt_sec.oht_buffers or not nxt_sec.oht_buffers[0].vacant_buffer_exist():
                continue
            candidates.append(nxt)

        if not candidates:
            return None

        # Move 1 hop to the least congested next section
        return min(candidates, key=lambda sid: bridge.get_section_congestion(sid))
