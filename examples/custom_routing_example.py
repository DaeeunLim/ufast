"""
Custom routing strategy example.

Return rules:
- Define get_route(from_sec_id, to_sec_id, vehicle_controller).
- The return value is a list of section_ids excluding the current section.
- Returning None or an empty list falls back to the built-in default routing.
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
