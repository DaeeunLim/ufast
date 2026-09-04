"""
커스텀 Routing 전략 예시.

반환 규칙:
- get_route(from_sec_id, to_sec_id, vehicle_controller)를 정의한다.
- 반환값은 현재 섹션을 제외한 section_id 리스트다.
- None 또는 빈 리스트를 반환하면 기존 기본 Routing으로 fallback된다.
"""
from collections import deque


class RoutingStrategy:
    def get_route(self, from_sec_id, to_sec_id, vehicle_controller=None):
        if vehicle_controller is None or not hasattr(vehicle_controller, "data_set"):
            return None
        ds = vehicle_controller.data_set
        if from_sec_id == to_sec_id:
            return []
        if from_sec_id not in ds.section_id_to_index or to_sec_id not in ds.section_id_to_index:
            return None

        prev = {from_sec_id: None}
        q = deque([from_sec_id])

        while q:
            sid = q.popleft()
            if sid == to_sec_id:
                break
            sec = ds.sections[ds.section_id_to_index[sid]]
            for nxt in sec.next_sections:
                if nxt not in ds.section_id_to_index or nxt in prev:
                    continue
                prev[nxt] = sid
                q.append(nxt)

        if to_sec_id not in prev:
            return None

        path = []
        cur = to_sec_id
        while cur != from_sec_id:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        return path
