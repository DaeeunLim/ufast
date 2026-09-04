"""
viz/replay_recorder.py — GUI '재생 시뮬레이션' 모드용 기록기.

실시간 모드(기존 Qt 애니메이션)와 달리, 시뮬레이션을 전속력으로 돌리며
집계 지표를 기록했다가 종료 시 Parquet 저장 + Rerun 뷰어로 재생한다.

2계층 기록 설계:
  - 집계 계층 (전 구간): 섹션별 혼잡도(점유 OHT 수 시간평균), KPI 시계열.
    규모 = 섹션 수 × 프레임 수. 프레임 수는 frame_budget 으로 상한 고정
    (기본 10,000) → 실행 기간과 무관하게 용량이 제어된다.
  - 궤적 계층: OHT 위치·상태를 프레임 스텝으로 다운샘플 기록.
    빠른 재생에서 개체 추적이 안 되는 한계는 설계상 수용 (개체 검증은
    실시간 모드 담당).

SimulationThread(백그라운드)에서 on_step() 이 호출되고, 종료 시 같은
스레드에서 finalize() 가 호출된다. Qt 객체는 일절 사용하지 않는다.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from ufast.drawing.geometry import CQuadCurve

_OHT_SPEED_MM_S = 1000.0          # viewer.OHTItem 과 동일한 보간 속도
_CURVE_SAMPLES = 8                # 곡선 폴리라인 근사 분할 수

# OHT 상태 색상 (viewer 범례와 동일한 의미 체계)
_OHT_COLORS: Dict[str, List[int]] = {
    "IDLE":          [70, 110, 240, 220],
    "REPOSITIONING": [70, 110, 240, 220],
    "ASSIGNED":      [60, 200, 90, 255],
    "LOADED":        [230, 60, 50, 255],
}
_OHT_COLOR_MOVING = [255, 165, 0, 255]

_RAIL_COLOR_STATIC = [110, 110, 110, 160]

# 혼잡도 팔레트 — 논문 fig_a(section heatmap)와 동일한 RdYlGn 계열:
# 0 = 진초록(한산) → 0.5 = 연노랑 → 1 = 빨강(혼잡)
_CONG_STOPS = [
    (0.0, (26, 152, 80)),     # green
    (0.5, (255, 255, 191)),   # pale yellow
    (1.0, (215, 48, 39)),     # red
]

# EQ 상태 → 코드 → 색 (viewer.update_animation 의 색 체계와 동일)
_EQ_STATUS_CODES = {
    "IDLE": 0,
    "WAITING": 1,
    "PROCESS_WAITING": 2, "LOT_INBOUND": 2,
    "OHT_COMING": 3, "PROCESSING": 3,
}
_EQ_CODE_COLORS = {
    0: [160, 160, 160, 90],     # IDLE 회색
    1: [255, 165, 0, 220],      # WAITING 주황
    2: [135, 206, 250, 220],    # 공정 대기 하늘색
    3: [50, 205, 50, 230],      # OHT 접근/가공 중 녹색
}
_EQ_HALF_SIZE_MM = 400.0


def _congestion_color(norm: float) -> List[int]:
    """0..1 정규화 혼잡도 → RdYlGn(초록→노랑→빨강) 그라디언트."""
    norm = max(0.0, min(norm, 1.0))
    for (t0, c0), (t1, c1) in zip(_CONG_STOPS, _CONG_STOPS[1:]):
        if norm <= t1:
            f = (norm - t0) / (t1 - t0) if t1 > t0 else 0.0
            return [int(a + (b - a) * f) for a, b in zip(c0, c1)] + [230]
    return list(_CONG_STOPS[-1][1]) + [230]


def _figure_polyline(fig) -> List[List[float]]:
    """섹션 figure 하나를 폴리라인 점 목록으로 변환."""
    if isinstance(fig, CQuadCurve):
        pts = []
        for i in range(_CURVE_SAMPLES + 1):
            t = i / _CURVE_SAMPLES
            mt = 1.0 - t
            x = mt * mt * fig.start_x + 2 * mt * t * fig.ctrl_x + t * t * fig.end_x
            y = mt * mt * fig.start_y + 2 * mt * t * fig.ctrl_y + t * t * fig.end_y
            pts.append([x, y])
        return pts
    return [[fig.start_x, fig.start_y], [fig.end_x, fig.end_y]]


def _oht_xy(oht, clock: float, ds) -> Optional[List[float]]:
    """viewer.OHTItem.update_position 과 동일한 섹션 내 위치 보간."""
    idx = ds.section_id_to_index.get(oht.current_section_id)
    if idx is None:
        return None
    section = ds.sections[idx]
    if not section.figures:
        return None
    fig = section.figures[0]
    elapsed = clock - oht.time_enter_current_section
    dist = elapsed * _OHT_SPEED_MM_S
    ratio = dist / section.length if section.length > 0 else 0.0
    ratio = max(0.0, min(1.0, ratio))
    if isinstance(fig, CQuadCurve):
        t = ratio
        mt = 1.0 - t
        return [
            mt * mt * fig.start_x + 2 * mt * t * fig.ctrl_x + t * t * fig.end_x,
            mt * mt * fig.start_y + 2 * mt * t * fig.ctrl_y + t * t * fig.end_y,
        ]
    return [
        fig.start_x + (fig.end_x - fig.start_x) * ratio,
        fig.start_y + (fig.end_y - fig.start_y) * ratio,
    ]


class ReplayRecorder:
    """재생 모드 기록기 — 시뮬레이션 스레드에서 on_step/finalize 호출."""

    def __init__(
        self,
        ds,
        duration: float,
        *,
        frame_budget: int = 10_000,
        out_base: str = os.path.join("logs", "replay"),
        spawn_viewer: bool = True,
        app_id: str = "UFAST_replay_gui",
    ):
        self.ds = ds
        self.duration = float(duration)
        self.frame_step = max(1.0, self.duration / frame_budget)
        self.sample_interval = min(1.0, self.frame_step)
        self.out_base = out_base
        self.spawn_viewer = spawn_viewer
        self.app_id = app_id
        self.out_dir: Optional[str] = None

        # 섹션 목록은 시작 시점에 고정 (id 순서 유지)
        self._section_ids = [s.section_id for s in ds.sections]
        self._sec_index = {sid: i for i, sid in enumerate(self._section_ids)}

        # EQ 목록 고정 (이름 순) — 상태는 프레임별 코드(bytearray)로 압축 저장
        self._eq_names = sorted(ds.eq_list.keys())
        self._eq_centers = [
            [ds.eq_list[n].left, ds.eq_list[n].top] for n in self._eq_names
        ]
        self._eq_status_frames: List[bytearray] = []

        self._next_sample = 0.0
        self._next_frame = 0.0
        # 버킷 누적: 섹션별 점유 합 / 샘플 수
        self._occ_sum = [0.0] * len(self._section_ids)
        self._n_samples = 0

        # 프레임 저장소 (finalize 에서 일괄 저장/로깅)
        self.frame_times: List[float] = []
        self._congestion_rows: List[List[float]] = []   # frames × sections 평균 점유
        self._oht_names: List[str] = []
        self._oht_positions: List[List[List[float]]] = []  # frames × ohts × [x,y]
        self._oht_colors: List[List[List[int]]] = []
        self._kpi_rows: List[dict] = []

    @property
    def frame_count(self) -> int:
        return len(self.frame_times)

    # ── 시뮬레이션 스레드 훅 ─────────────────────────────────
    def on_step(self, clock: float):
        if clock >= self._next_sample:
            self._sample_occupancy()
            self._next_sample += self.sample_interval
        if clock >= self._next_frame:
            self._flush_frame(clock)
            self._next_frame += self.frame_step

    def _sample_occupancy(self):
        for oht in self.ds.oht_list.values():
            i = self._sec_index.get(oht.current_section_id)
            if i is not None:
                self._occ_sum[i] += 1.0
        self._n_samples += 1

    def _flush_frame(self, clock: float):
        n = self._n_samples or 1
        self._congestion_rows.append([s / n for s in self._occ_sum])
        self._occ_sum = [0.0] * len(self._section_ids)
        self._n_samples = 0

        if not self._oht_names:
            self._oht_names = sorted(self.ds.oht_list.keys())
        positions, colors = [], []
        for name in self._oht_names:
            oht = self.ds.oht_list.get(name)
            xy = _oht_xy(oht, clock, self.ds) if oht is not None else None
            if xy is None:
                xy = [0.0, 0.0]
                colors.append([0, 0, 0, 0])       # 위치 불명 → 투명
            else:
                colors.append(_OHT_COLORS.get(oht.status, _OHT_COLOR_MOVING))
            positions.append(xy)
        self._oht_positions.append(positions)
        self._oht_colors.append(colors)

        eq_codes = bytearray(len(self._eq_names))
        for i, name in enumerate(self._eq_names):
            eq = self.ds.eq_list.get(name)
            status = getattr(eq, "eq_status", "IDLE") if eq is not None else "IDLE"
            eq_codes[i] = _EQ_STATUS_CODES.get(status, 0)
        self._eq_status_frames.append(eq_codes)

        self.frame_times.append(clock)
        self._kpi_rows.append(self._collect_kpis(clock))

    def _collect_kpis(self, clock: float) -> dict:
        ds = self.ds
        idle = repo = assigned = loaded = 0
        for o in ds.oht_list.values():
            s = o.status
            if s == "IDLE":
                idle += 1
            elif s == "REPOSITIONING":
                repo += 1
            elif s == "ASSIGNED":
                assigned += 1
            elif s == "LOADED":
                loaded += 1
        row = {
            "t": clock,
            "processed": ds.num_of_processed_lot,
            "total_lots": ds.lot_count,
            "oht_count": len(ds.oht_list),
            "idle": idle,
            "repositioning": repo,
            "assigned": assigned,
            "loaded": loaded,
            "throughput_lots_h": (ds.num_of_processed_lot / clock * 3600.0)
                                 if clock > 0 else 0.0,
        }
        try:
            from ufast.common.logger import get_logger
            k = get_logger().get_live_kpis()
            row.update({
                "avg_transport_time": k.get("avg_transport_time", 0.0),
                "avg_delivery_time":  k.get("avg_delivery_time", 0.0),
                "avg_call_wait":      k.get("avg_call_wait", 0.0),
                "wip":                k.get("wip", 0),
            })
        except Exception:
            pass
        return row

    # ── 종료 처리: Parquet 저장 + Rerun 로깅 ──────────────────
    def finalize(self) -> Optional[str]:
        if not self.frame_times:
            return None
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.out_dir = os.path.join(self.out_base, ts)
        os.makedirs(self.out_dir, exist_ok=True)

        self._save_parquet()
        try:
            self._save_geometry()
        except Exception as e:  # noqa: BLE001
            print(f"[replay_recorder] ⚠️ 지오메트리 저장 실패: {e}")
        try:
            self._log_rerun()
        except Exception as e:  # noqa: BLE001 — 뷰어 실패가 결과 저장을 막으면 안 됨
            print(f"[replay_recorder] ⚠️ Rerun 로깅 실패: {e}")
        try:
            # 종료 후 정적 리포트 (KPI 시계열 차트 + 혼잡도 히트맵 + HTML)
            from ufast.viz.report import generate_report
            generate_report(self.out_dir)
        except Exception as e:  # noqa: BLE001
            print(f"[replay_recorder] ⚠️ 리포트 생성 실패: {e}")
        return self.out_dir

    def _save_parquet(self):
        import pandas as pd
        kpi = pd.DataFrame(self._kpi_rows)
        kpi_path = os.path.join(self.out_dir, "kpi_timeseries.parquet")
        kpi.to_parquet(kpi_path, index=False)

        cong = pd.DataFrame(
            self._congestion_rows,
            columns=[str(s) for s in self._section_ids],
            dtype="float32",
        )
        cong.insert(0, "t", self.frame_times)
        cong_path = os.path.join(self.out_dir, "section_congestion.parquet")
        cong.to_parquet(cong_path, index=False)
        print(f"[replay_recorder] Parquet 저장: {kpi_path}, {cong_path} "
              f"({self.frame_count} frames × {len(self._section_ids)} sections)")

    def _section_strips(self):
        """섹션 figure → 폴리라인 목록 + strip 별 소속 섹션 index."""
        strips: List[List[List[float]]] = []
        strip_section: List[int] = []
        for si, section in enumerate(self.ds.sections):
            for fig in section.figures:
                strips.append(_figure_polyline(fig))
                strip_section.append(si)
        return strips, strip_section

    def _save_geometry(self):
        """섹션 지오메트리를 저장 — 리포트(viz.report)가 레일 위에
        혼잡도를 직접 그릴 때 사용한다."""
        import json
        strips, strip_section = self._section_strips()
        geo: Dict[str, list] = {}
        for strip, si in zip(strips, strip_section):
            geo.setdefault(str(self._section_ids[si]), []).append(strip)
        path = os.path.join(self.out_dir, "section_geometry.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(geo, f)
        print(f"[replay_recorder] 지오메트리 저장: {path}")

    def _log_rerun(self):
        import rerun as rr

        rr.init(self.app_id, spawn=self.spawn_viewer)
        try:
            rr.disable_timeline("log_time")
        except Exception:
            pass

        # ── 정적 레일 ──
        strips, strip_section = self._section_strips()
        rr.log("layout/rails",
               rr.LineStrips2D(strips, colors=_RAIL_COLOR_STATIC, radii=60.0),
               static=True)

        # ── 정적 EQ 마커 (이름 라벨 포함) — 상태색은 프레임별로 갱신 ──
        can_partial_boxes = hasattr(rr.Boxes2D, "from_fields")
        if self._eq_names:
            rr.log("layout/eqs",
                   rr.Boxes2D(centers=self._eq_centers,
                              half_sizes=[[_EQ_HALF_SIZE_MM, _EQ_HALF_SIZE_MM]]
                                         * len(self._eq_names),
                              colors=_EQ_CODE_COLORS[0],
                              labels=self._eq_names,
                              show_labels=False),
                   static=True)

        # 혼잡도 정규화 기준: 전 프레임 양수 점유의 95 percentile
        flat = [v for row in self._congestion_rows for v in row if v > 0]
        if flat:
            flat.sort()
            p95 = flat[int(len(flat) * 0.95) - 1] if len(flat) > 1 else flat[0]
            p95 = p95 or 1.0
        else:
            p95 = 1.0

        can_partial = hasattr(rr.LineStrips2D, "from_fields")
        for f, t in enumerate(self.frame_times):
            rr.set_time("sim_time", duration=t)

            row = self._congestion_rows[f]
            colors = [_congestion_color(row[si] / p95) for si in strip_section]
            if can_partial:
                rr.log("layout/congestion", rr.LineStrips2D.from_fields(colors=colors))
            else:
                rr.log("layout/congestion",
                       rr.LineStrips2D(strips, colors=colors, radii=90.0))

            rr.log("ohts", rr.Points2D(self._oht_positions[f],
                                       colors=self._oht_colors[f],
                                       radii=600.0))

            if self._eq_names:
                eq_colors = [_EQ_CODE_COLORS[c] for c in self._eq_status_frames[f]]
                if can_partial_boxes:
                    rr.log("layout/eqs", rr.Boxes2D.from_fields(colors=eq_colors))
                else:
                    rr.log("layout/eqs",
                           rr.Boxes2D(centers=self._eq_centers,
                                      half_sizes=[[_EQ_HALF_SIZE_MM, _EQ_HALF_SIZE_MM]]
                                                 * len(self._eq_names),
                                      colors=eq_colors,
                                      labels=self._eq_names,
                                      show_labels=False))

            k = self._kpi_rows[f]
            rr.log("kpi/throughput_lots_h", rr.Scalars(k["throughput_lots_h"]))
            rr.log("kpi/loaded", rr.Scalars(float(k["loaded"])))
            rr.log("kpi/idle", rr.Scalars(float(k["idle"])))
            if "wip" in k:
                rr.log("kpi/wip", rr.Scalars(float(k["wip"])))
            if "avg_transport_time" in k:
                rr.log("kpi/avg_transport_time", rr.Scalars(k["avg_transport_time"]))

        # 최초 프레임에 congestion 지오메트리가 없으면 partial update 가 안 보이므로
        # partial 방식일 때는 지오메트리를 static 으로 한 번 깔아준다.
        if can_partial:
            rr.set_time("sim_time", duration=self.frame_times[0])
            rr.log("layout/congestion",
                   rr.LineStrips2D(strips,
                                   colors=[_congestion_color(0.0)] * len(strips),
                                   radii=90.0),
                   static=True)

        rrd_path = os.path.join(self.out_dir, "replay.rrd")
        try:
            rr.save(rrd_path)
            print(f"[replay_recorder] .rrd 저장: {rrd_path}")
        except Exception as e:  # noqa: BLE001
            print(f"[replay_recorder] ⚠️ .rrd 저장 실패(뷰어는 정상): {e}")
        print(f"[replay_recorder] Rerun 로깅 완료 — {self.frame_count} frames")
