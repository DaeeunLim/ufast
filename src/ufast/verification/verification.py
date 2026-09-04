"""
Verification utilities for AMHS simulation logs/state.

이 모듈은 시뮬레이션 동작에는 개입하지 않고, 저장 시점의 상태와 logger 기록을
기반으로 rule violation 리포트만 생성한다.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any, List, Dict


def _add(rows: List[Dict[str, Any]], rule_id: str, severity: str, entity_type: str, entity_id: str, message: str):
    rows.append({
        "rule_id": rule_id,
        "severity": severity,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "message": message,
    })


def run_verification(output_dir: str, data_set: Any, logger: Any, route_manager: Any = None) -> List[str]:
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rows: List[Dict[str, Any]] = []

    valid_status = {"IDLE", "ASSIGNED", "LOADED", "REPOSITIONING"}

    # V0xx: OHT 상태 및 section 유효성
    for name, oht in getattr(data_set, "oht_list", {}).items():
        if getattr(oht, "status", None) not in valid_status:
            _add(rows, "V001", "ERROR", "OHT", name, f"invalid OHT status: {getattr(oht, 'status', None)}")
        sec_id = getattr(oht, "current_section_id", None)
        if sec_id not in getattr(data_set, "section_id_to_index", {}):
            _add(rows, "V002", "ERROR", "OHT", name, f"current_section_id not found: {sec_id}")
        if getattr(oht, "status", None) == "LOADED" and not getattr(oht, "loaded_lot_info", None):
            _add(rows, "V003", "WARN", "OHT", name, "LOADED status but loaded_lot_info is empty")

    # V1xx: buffer 중복 점유 및 capacity 초과
    seen = {}
    for sec in getattr(data_set, "sections", []):
        for b_idx, buf in enumerate(getattr(sec, "oht_buffers", [])):
            items = [x for x in getattr(buf, "buffer", []) if x is not None]
            if len(items) > getattr(buf, "capacity", len(items)):
                _add(rows, "V101", "ERROR", "BUFFER", f"sec{sec.section_id}/buf{b_idx}", "buffer occupancy exceeds capacity")
            for item in items:
                key = str(item)
                loc = f"sec{sec.section_id}/buf{b_idx}"
                if key in seen:
                    _add(rows, "V102", "ERROR", "OHT", key, f"duplicated in buffers: {seen[key]} and {loc}")
                else:
                    seen[key] = loc

    # V2xx: Lot timestamp 순서
    for lot_id, rec in getattr(logger, "lot_records", {}).items():
        if rec.pickup_time >= 0 and rec.pickup_time < rec.created_time:
            _add(rows, "V201", "ERROR", "LOT", lot_id, "pickup_time earlier than created_time")
        if rec.delivered_time >= 0 and rec.pickup_time >= 0 and rec.delivered_time < rec.pickup_time:
            _add(rows, "V202", "ERROR", "LOT", lot_id, "delivered_time earlier than pickup_time")

    # V3xx: Rundown timestamp 순서
    for rec in getattr(logger, "eq_rundown_records", []):
        if rec.process_start_time >= 0 and rec.wait_start_time >= 0 and rec.process_start_time < rec.wait_start_time:
            _add(rows, "V301", "ERROR", "EQ_RUNDOWN", str(rec.seq), "process_start_time earlier than wait_start_time")

    # V4xx: RouteManager tracker consistency
    if route_manager is not None:
        vehicles = getattr(getattr(route_manager, "tracker", None), "vehicles", {}) or {}
        for name in getattr(data_set, "oht_list", {}).keys():
            if name not in vehicles:
                _add(rows, "V401", "WARN", "OHT", name, "OHT not registered in RouteManager VehicleTracker")

    report_path = os.path.join(output_dir, f"verification_report_{ts}.csv")
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["rule_id", "severity", "entity_type", "entity_id", "message"])
        writer.writeheader()
        writer.writerows(rows)

    summary_path = os.path.join(output_dir, f"verification_summary_{ts}.txt")
    error_count = sum(1 for r in rows if r["severity"] == "ERROR")
    warn_count = sum(1 for r in rows if r["severity"] == "WARN")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("Verification Summary\n")
        f.write("=" * 60 + "\n")
        f.write(f"total_violations: {len(rows)}\n")
        f.write(f"errors: {error_count}\n")
        f.write(f"warnings: {warn_count}\n")
        f.write("\nRules checked: V001-V003, V101-V102, V201-V202, V301, V401\n")
        f.write("This verification report is generated after simulation and does not alter simulation behavior.\n")

    return [report_path, summary_path]
