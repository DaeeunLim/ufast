"""
SimulationLogger
시뮬레이션 결과를 CSV + Summary 텍스트 형식으로 저장
"""

import os
import csv
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ── Lot 레코드 ──────────────────────────────────────────────────────────────
@dataclass
class LotRecord:
    lot_id: str
    from_eq: str
    to_eq: str
    created_time: float      # LOT 이벤트 발생 시각
    pickup_time: float = -1  # OHT가 from_eq 도착하여 픽업한 시각
    delivered_time: float = -1  # to_eq 배달 완료 시각

    @property
    def waiting_time(self) -> float:
        """픽업 대기시간 (created → pickup)"""
        if self.pickup_time < 0:
            return -1
        return self.pickup_time - self.created_time

    @property
    def transport_time(self) -> float:
        """운반 시간 (pickup → delivered)"""
        if self.pickup_time < 0 or self.delivered_time < 0:
            return -1
        return self.delivered_time - self.pickup_time

    @property
    def total_time(self) -> float:
        """총 소요시간 (created → delivered)"""
        if self.delivered_time < 0:
            return -1
        return self.delivered_time - self.created_time


# ── OHT 레코드 ──────────────────────────────────────────────────────────────
@dataclass
class OHTRecord:
    oht_id: str
    total_trips: int = 0
    total_distance: float = 0.0   # mm
    idle_time: float = 0.0        # 초
    assigned_time: float = 0.0    # 픽업 이동 시간 (초)
    loaded_time: float = 0.0      # 배달 이동 시간 (초)

    # 내부 추적용
    _last_status: str = field(default="IDLE", repr=False)
    _last_status_time: float = field(default=0.0, repr=False)
    _current_section_length: float = field(default=0.0, repr=False)

    @property
    def utilization(self) -> float:
        """가동률 (LOADED 비율)"""
        total = self.idle_time + self.assigned_time + self.loaded_time
        return self.loaded_time / total if total > 0 else 0.0

    @property
    def total_distance_m(self) -> float:
        return self.total_distance / 1000.0


# ── EQ 레코드 ───────────────────────────────────────────────────────────────
@dataclass
class EQRecord:
    eq_name: str
    lots_dispatched: int = 0   # 이 EQ에서 출발한 lot 수
    lots_received: int = 0     # 이 EQ로 도착한 lot 수


# ── EQ Rundown 레코드 ───────────────────────────────────────────────────────
@dataclass
class EQRundownRecord:
    seq: int
    eq_name: str
    lot_id: str
    from_eq: str = ""
    to_eq: str = ""
    oht_id: str = ""
    wait_start_time: float = -1.0     # 가공 대기 시작 시각(하늘색)
    process_start_time: float = -1.0  # 가공 시작 시각(초록색)
    rundown_time: float = -1.0        # wait_start → process_start
    status: str = "WAITING_FOR_LOT"
    wait_origin: str = ""             # rundown 측정 시작 기준 (REQUEST/ARRIVAL 등)


# ── 메인 로거 ────────────────────────────────────────────────────────────────
class SimulationLogger:
    def __init__(self):
        self.lot_records: Dict[str, LotRecord] = {}
        self.oht_records: Dict[str, OHTRecord] = {}
        self.eq_records: Dict[str, EQRecord] = {}
        self.eq_rundown_records: List[EQRundownRecord] = []
        self._open_eq_rundown: Dict[Tuple[str, str], EQRundownRecord] = {}
        self._eq_rundown_seq: int = 0

        self.sim_start_wall: str = ""
        self.sim_duration: float = 0.0
        self.num_oht: int = 0

    def reset(self, num_oht: int, sim_duration: float):
        self.lot_records.clear()
        self.oht_records.clear()
        self.eq_records.clear()
        self.eq_rundown_records.clear()
        self._open_eq_rundown.clear()
        self._eq_rundown_seq = 0
        self.sim_start_wall = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.sim_duration = sim_duration
        self.num_oht = num_oht

    # ── 실행 중 KPI 요약 ─────────────────────────────────────────────────
    def get_live_kpis(self) -> Dict[str, float]:
        """누적 레코드 기반 라이브 KPI — GUI Statistics 패널과
        Replay 기록기(viz.replay_recorder)가 주기적으로 호출한다.

        - avg_transport_time: 픽업→배달 평균 (완료 lot)
        - avg_delivery_time:  생성→배달 평균 (완료 lot)
        - avg_call_wait:      생성→픽업 평균 (픽업된 lot)
        - avg_rundown_time / rundown_count: EQ 가공대기 rundown
        - wip: 생성됐지만 아직 배달되지 않은 lot 수
        """
        tt: List[float] = []
        dt: List[float] = []
        cw: List[float] = []
        wip = 0
        # GUI 스레드에서 호출될 수 있으므로 스냅샷 리스트로 순회
        for r in list(self.lot_records.values()):
            if r.delivered_time >= 0:
                dt.append(r.total_time)
                tt.append(r.transport_time)
            else:
                wip += 1
            if r.pickup_time >= 0:
                cw.append(r.waiting_time)
        rundowns = [
            r.rundown_time for r in list(self.eq_rundown_records)
            if r.rundown_time >= 0
        ]

        def _mean(seq: List[float]) -> float:
            return sum(seq) / len(seq) if seq else 0.0

        return {
            "avg_transport_time": _mean(tt),
            "avg_delivery_time":  _mean(dt),
            "avg_call_wait":      _mean(cw),
            "avg_rundown_time":   _mean(rundowns),
            "rundown_count":      len(rundowns),
            "wip":                wip,
        }

    # ── 이벤트 기록 메서드 ────────────────────────────────────────────────
    def on_lot_created(self, lot_id: str, from_eq: str, to_eq: str, created_time: float):
        self.lot_records[lot_id] = LotRecord(
            lot_id=lot_id, from_eq=from_eq, to_eq=to_eq, created_time=created_time
        )
        eq = self.eq_records.setdefault(from_eq, EQRecord(from_eq))
        eq.lots_dispatched += 1

    def on_lot_picked_up(self, lot_id: str, pickup_time: float):
        rec = self.lot_records.get(lot_id)
        if rec:
            rec.pickup_time = pickup_time

    def on_lot_delivered(self, lot_id: str, to_eq: str, delivered_time: float):
        rec = self.lot_records.get(lot_id)
        if rec:
            rec.delivered_time = delivered_time
        eq = self.eq_records.setdefault(to_eq, EQRecord(to_eq))
        eq.lots_received += 1

    def on_eq_rundown_wait_start(self, eq_name: str, lot_id: str,
                                  wait_start_time: float, oht_id: str = "",
                                  wait_origin: str = ""):
        """설비가 새 Lot 도착을 기다리기 시작한 시각을 기록한다.

        wait_origin: rundown 측정 시작 기준 ("REQUEST"=호출 시점, ""=도착 시점 등).
        """
        if not eq_name or not lot_id:
            return

        key = (eq_name, lot_id)
        if key in self._open_eq_rundown:
            return

        self._eq_rundown_seq += 1
        lot_rec = self.lot_records.get(lot_id)
        rec = EQRundownRecord(
            seq=self._eq_rundown_seq,
            eq_name=eq_name,
            lot_id=lot_id,
            from_eq=lot_rec.from_eq if lot_rec else "",
            to_eq=lot_rec.to_eq if lot_rec else eq_name,
            oht_id=oht_id,
            wait_start_time=wait_start_time,
            wait_origin=wait_origin,
        )
        self.eq_rundown_records.append(rec)
        self._open_eq_rundown[key] = rec

    def on_eq_process_start(self, eq_name: str, lot_id: str,
                            process_start_time: float, oht_id: str = ""):
        """설비에 Lot이 도착해 가공이 시작된 시각과 rundown time을 기록한다."""
        if not eq_name or not lot_id:
            return

        key = (eq_name, lot_id)
        rec = self._open_eq_rundown.pop(key, None)
        if rec is None:
            self._eq_rundown_seq += 1
            lot_rec = self.lot_records.get(lot_id)
            rec = EQRundownRecord(
                seq=self._eq_rundown_seq,
                eq_name=eq_name,
                lot_id=lot_id,
                from_eq=lot_rec.from_eq if lot_rec else "",
                to_eq=lot_rec.to_eq if lot_rec else eq_name,
                oht_id=oht_id,
            )
            self.eq_rundown_records.append(rec)

        if oht_id and not rec.oht_id:
            rec.oht_id = oht_id
        rec.process_start_time = process_start_time
        if rec.wait_start_time >= 0:
            rec.rundown_time = process_start_time - rec.wait_start_time
        rec.status = "PROCESS_STARTED"

    def on_eq_process_end(self, eq_name: str, lot_id: str,
                          process_start_time: float, process_end_time: float):
        """Accept the GUI processing-end event.

        Scientific starvation is measured by the production engine, not by
        this request-to-arrival compatibility logger.
        """
        return None

    def on_oht_init(self, oht_id: str, init_time: float = 0.0):
        self.oht_records[oht_id] = OHTRecord(oht_id=oht_id)
        self.oht_records[oht_id]._last_status_time = init_time

    def on_oht_status_change(self, oht_id: str, new_status: str,
                              current_time: float, section_length: float = 0.0):
        rec = self.oht_records.get(oht_id)
        if rec is None:
            return
        elapsed = current_time - rec._last_status_time
        if rec._last_status == "IDLE":
            rec.idle_time += elapsed
        elif rec._last_status == "ASSIGNED":
            rec.assigned_time += elapsed
            rec.total_distance += section_length
        elif rec._last_status == "LOADED":
            rec.loaded_time += elapsed
            rec.total_distance += section_length
            if new_status == "IDLE":
                rec.total_trips += 1

        rec._last_status = new_status
        rec._last_status_time = current_time

    def on_oht_moved(self, oht_id: str, section_length: float):
        """섹션 이동 시 거리 누적"""
        rec = self.oht_records.get(oht_id)
        if rec:
            rec.total_distance += section_length

    def finalize_oht_times(self, end_time: float):
        """Flush each OHT's last status interval through the run boundary."""
        for rec in self.oht_records.values():
            elapsed = max(0.0, float(end_time) - rec._last_status_time)
            if rec._last_status == "IDLE":
                rec.idle_time += elapsed
            elif rec._last_status == "ASSIGNED":
                rec.assigned_time += elapsed
            elif rec._last_status == "LOADED":
                rec.loaded_time += elapsed
            rec._last_status_time = max(rec._last_status_time, float(end_time))

    # ── 저장 ─────────────────────────────────────────────────────────────
    def save(self, output_dir: str, kpi_mode: str = "both", simulator_mode: str = "production_logistics") -> List[str]:
        """로그 저장. 저장된 파일 경로 목록 반환.

        kpi_mode:
          - logistics  : 반송/배차 중심 로그 저장
          - production : 설비/rundown 중심 로그 저장
          - both       : 기존 로그 전체 저장

        simulator_mode:
          - from_to_only          : processing/rundown 개념 없는 물류 전용 모드
          - production_logistics  : 생산 상태 표시 및 rundown 로그 포함
        """
        os.makedirs(output_dir, exist_ok=True)
        self.finalize_oht_times(self.sim_duration)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved = []

        mode = kpi_mode if kpi_mode in {"logistics", "production", "both"} else "both"

        saved.append(self._save_summary(output_dir, ts))
        saved.append(self._save_oht_log(output_dir, ts))
        saved.append(self._save_kpi_relation_report(output_dir, ts, mode, simulator_mode))

        if mode in ("logistics", "both"):
            saved.append(self._save_lot_log(output_dir, ts))

        if mode in ("production", "both") and simulator_mode != "from_to_only":
            saved.append(self._save_eq_log(output_dir, ts))
            saved.append(self._save_eq_supply_delay_log(output_dir, ts))
            saved.append(self._save_processing_time_measurement_note(output_dir, ts, simulator_mode))

        return saved


    def _avg(self, values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def _save_kpi_relation_report(self, output_dir: str, ts: str, kpi_mode: str, simulator_mode: str) -> str:
        completed = [r for r in self.lot_records.values() if r.delivered_time >= 0]
        waiting_times = [r.waiting_time for r in completed if r.waiting_time >= 0]
        transport_times = [r.transport_time for r in completed if r.transport_time >= 0]
        total_times = [r.total_time for r in completed if r.total_time >= 0]
        supply_delay_times = [
            r.rundown_time for r in self.eq_rundown_records
            if r.rundown_time >= 0
        ]
        oht_utils = [r.utilization for r in self.oht_records.values()]
        throughput = len(completed) / self.sim_duration * 3600 if self.sim_duration > 0 else 0.0

        path = os.path.join(output_dir, f"kpi_relation_report_{ts}.csv")
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["group", "metric", "value", "unit", "kpi_mode", "simulator_mode", "note"])
            writer.writerow(["logistics", "completed_lots", len(completed), "lots", kpi_mode, simulator_mode, "delivered lots"])
            writer.writerow(["logistics", "avg_lot_waiting_time", f"{self._avg(waiting_times):.4f}", "s", kpi_mode, simulator_mode, "created to pickup"])
            writer.writerow(["logistics", "avg_transport_time", f"{self._avg(transport_times):.4f}", "s", kpi_mode, simulator_mode, "pickup to delivery"])
            writer.writerow(["logistics", "transportation_time_TT", f"{self._avg(transport_times):.4f}", "s", kpi_mode, simulator_mode, "TT: pickup(배차/도착)->delivery(목적지 도착)"])
            writer.writerow(["logistics", "avg_total_tat", f"{self._avg(total_times):.4f}", "s", kpi_mode, simulator_mode, "created to delivery"])
            writer.writerow(["logistics", "delivery_time_DT", f"{self._avg(total_times):.4f}", "s", kpi_mode, simulator_mode, "DT: 호출(created)->목적지 도착(delivery)"])
            writer.writerow(["logistics", "avg_call_wait", f"{self._avg(waiting_times):.4f}", "s", kpi_mode, simulator_mode, "call wait: 호출->픽업"])
            writer.writerow(["logistics", "avg_oht_utilization", f"{self._avg(oht_utils) * 100:.4f}", "%", kpi_mode, simulator_mode, "loaded_time / total_tracked_time"])
            writer.writerow(["production", "throughput", f"{throughput:.4f}", "lots/hr", kpi_mode, simulator_mode, "completed lots per simulated hour"])
            writer.writerow(["logistics", "completed_inbound_supply_delay_count", len(supply_delay_times), "events", kpi_mode, simulator_mode, "request to equipment arrival; not starvation"])
            writer.writerow(["logistics", "avg_inbound_supply_delay_s", f"{self._avg(supply_delay_times):.4f}", "s", kpi_mode, simulator_mode, "request to equipment arrival; not starvation"])
        return path

    def _save_processing_time_measurement_note(self, output_dir: str, ts: str, simulator_mode: str) -> str:
        path = os.path.join(output_dir, f"processing_time_measurement_{ts}.txt")
        lines = [
            "Processing / Inbound Supply Delay Measurement Note",
            "=" * 60,
            f"simulator_mode: {simulator_mode}",
            "",
            "This legacy GUI metric is request-to-arrival inbound supply delay.",
            "It is not equipment starvation (previous process end to next lot arrival).",
            "Scientific co-simulation starvation is stored in the equipment KPI output.",
            "From-To only mode does not generate processing or supply-delay events.",
        ]
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return path

    def _save_summary(self, output_dir: str, ts: str) -> str:
        completed = [r for r in self.lot_records.values() if r.delivered_time >= 0]
        waiting_times  = [r.waiting_time  for r in completed if r.waiting_time  >= 0]
        transport_times= [r.transport_time for r in completed if r.transport_time >= 0]
        total_times    = [r.total_time     for r in completed if r.total_time    >= 0]
        supply_delay_times = [
            r.rundown_time for r in self.eq_rundown_records
            if r.rundown_time >= 0
        ]

        def avg(lst):  return sum(lst)/len(lst) if lst else 0.0
        def mn(lst):   return min(lst)          if lst else 0.0
        def mx(lst):   return max(lst)          if lst else 0.0

        throughput = len(completed) / self.sim_duration * 3600 if self.sim_duration > 0 else 0

        oht_util = [r.utilization for r in self.oht_records.values()]
        avg_util = avg(oht_util) * 100

        lines = [
            "=" * 60,
            "  AMHS Simulation Result Summary",
            "=" * 60,
            f"  Run time (wall)   : {self.sim_start_wall}",
            f"  Sim duration      : {self.sim_duration:.0f} s  "
            f"({self.sim_duration/3600:.2f} hr)",
            f"  Num OHTs          : {self.num_oht}",
            "",
            "── Lot Statistics ─────────────────────────────────────",
            f"  Total lots created: {len(self.lot_records)}",
            f"  Completed lots    : {len(completed)}",
            f"  Throughput        : {throughput:.2f} lots/hr",
            "",
            f"  Waiting time (s)  : avg={avg(waiting_times):.1f}  "
            f"min={mn(waiting_times):.1f}  max={mx(waiting_times):.1f}",
            f"  Transport time TT : avg={avg(transport_times):.1f}  "
            f"min={mn(transport_times):.1f}  max={mx(transport_times):.1f}  "
            f"(픽업→목적지 도착)",
            f"  Delivery time  DT : avg={avg(total_times):.1f}  "
            f"min={mn(total_times):.1f}  max={mx(total_times):.1f}  "
            f"(호출→목적지 도착)",
            f"  WIP (undelivered) : {sum(1 for r in self.lot_records.values() if r.delivered_time < 0)} lots",
            "",
            "── EQ Inbound Supply Delay ────────────────────────────",
            f"  Completed supply delays: {len(supply_delay_times)}",
            f"  Inbound supply delay (s): avg={avg(supply_delay_times):.1f}  "
            f"min={mn(supply_delay_times):.1f}  max={mx(supply_delay_times):.1f}",
            "",
            "── OHT Statistics ─────────────────────────────────────",
            f"  Avg utilization   : {avg_util:.1f} %",
        ]
        for rec in sorted(self.oht_records.values(), key=lambda r: r.oht_id):
            lines.append(
                f"  {rec.oht_id:12s}  trips={rec.total_trips:4d}  "
                f"dist={rec.total_distance_m:8.1f} m  "
                f"util={rec.utilization*100:5.1f} %  "
                f"IDLE={rec.idle_time:7.1f}s  "
                f"ASGN={rec.assigned_time:7.1f}s  "
                f"LOAD={rec.loaded_time:7.1f}s"
            )

        lines += [
            "",
            "── EQ Statistics ──────────────────────────────────────",
            f"  {'EQ':15s}  {'Dispatched':>10s}  {'Received':>8s}",
        ]
        for rec in sorted(self.eq_records.values(), key=lambda r: r.eq_name):
            lines.append(
                f"  {rec.eq_name:15s}  {rec.lots_dispatched:10d}  {rec.lots_received:8d}"
            )

        lines.append("=" * 60)

        path = os.path.join(output_dir, f"summary_{ts}.txt")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return path

    def _save_lot_log(self, output_dir: str, ts: str) -> str:
        path = os.path.join(output_dir, f"lot_log_{ts}.csv")
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "lot_id", "from_eq", "to_eq",
                "created_time", "pickup_time", "delivered_time",
                "waiting_time", "transport_time", "total_time", "status"
            ])
            for rec in sorted(self.lot_records.values(), key=lambda r: r.created_time):
                status = "DELIVERED" if rec.delivered_time >= 0 else (
                    "IN_TRANSIT" if rec.pickup_time >= 0 else "WAITING"
                )
                writer.writerow([
                    rec.lot_id, rec.from_eq, rec.to_eq,
                    f"{rec.created_time:.2f}",
                    f"{rec.pickup_time:.2f}" if rec.pickup_time >= 0 else "",
                    f"{rec.delivered_time:.2f}" if rec.delivered_time >= 0 else "",
                    f"{rec.waiting_time:.2f}" if rec.waiting_time >= 0 else "",
                    f"{rec.transport_time:.2f}" if rec.transport_time >= 0 else "",
                    f"{rec.total_time:.2f}" if rec.total_time >= 0 else "",
                    status
                ])
        return path

    def _save_oht_log(self, output_dir: str, ts: str) -> str:
        path = os.path.join(output_dir, f"oht_log_{ts}.csv")
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "oht_id", "total_trips", "total_distance_m",
                "idle_time_s", "assigned_time_s", "loaded_time_s", "utilization_pct"
            ])
            for rec in sorted(self.oht_records.values(), key=lambda r: r.oht_id):
                writer.writerow([
                    rec.oht_id, rec.total_trips,
                    f"{rec.total_distance_m:.1f}",
                    f"{rec.idle_time:.1f}",
                    f"{rec.assigned_time:.1f}",
                    f"{rec.loaded_time:.1f}",
                    f"{rec.utilization*100:.1f}"
                ])
        return path

    def _save_eq_log(self, output_dir: str, ts: str) -> str:
        path = os.path.join(output_dir, f"eq_log_{ts}.csv")
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["eq_name", "lots_dispatched", "lots_received"])
            for rec in sorted(self.eq_records.values(), key=lambda r: r.eq_name):
                writer.writerow([rec.eq_name, rec.lots_dispatched, rec.lots_received])
        return path

    def _save_eq_supply_delay_log(self, output_dir: str, ts: str) -> str:
        path = os.path.join(output_dir, f"eq_supply_delay_log_{ts}.csv")
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "seq", "eq_name", "lot_id", "from_eq", "to_eq", "oht_id",
                "request_time", "arrival_time", "inbound_supply_delay_s", "status"
            ])
            for rec in sorted(self.eq_rundown_records, key=lambda r: (r.wait_start_time, r.seq)):
                writer.writerow([
                    rec.seq, rec.eq_name, rec.lot_id, rec.from_eq, rec.to_eq, rec.oht_id,
                    f"{rec.wait_start_time:.2f}" if rec.wait_start_time >= 0 else "",
                    f"{rec.process_start_time:.2f}" if rec.process_start_time >= 0 else "",
                    f"{rec.rundown_time:.2f}" if rec.rundown_time >= 0 else "",
                    rec.status,
                ])
        return path


# 싱글턴
_instance: Optional[SimulationLogger] = None

def get_logger() -> SimulationLogger:
    global _instance
    if _instance is None:
        _instance = SimulationLogger()
    return _instance
