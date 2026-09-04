"""
커스텀 Idle Positioning 전략 예시.

반환 규칙:
- plan_reposition(oht, current_time, current_node, route_manager, bridge, event_handler, vehicle_controller)를 정의한다.
- 반환값은 다음 중 하나다.
  1) target section id: 123
  2) target node: "N_..." 또는 route_manager node 문자열
  3) section path: [123, 124, 125]
  4) dict: {"target_section_id": 123} 또는 {"path": [123, 124]}
- None을 반환하면 기존 기본 Idle Positioning으로 fallback된다.
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

        # 혼잡도가 가장 낮은 다음 section으로 1-hop 이동
        return min(candidates, key=lambda sid: bridge.get_section_congestion(sid))
