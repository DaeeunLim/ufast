"""
ufast/cosim/vehicle.py — OHT 차량 제원 (VehicleSpec).

속도·가감속·차량 길이·최소 차간거리·링크 제한속도를 한 객체로 묶는다.
우선순위: CLI 플래그 > `--vehicle-spec` 파일 > 기본 파일(dataset/vehicle_spec.json)
> 내장 기본값. 기본 파일의 값은 SMAT2022 OHTV0 (VehicleType.csv) 와 같다.

내부 단위는 레일 파일과 같은 mm / mm/s / mm/s² 이다. CLI 플래그는 사람이 읽기
쉬운 단위(속도 m/s, 가감속 m/s², 길이 mm)로 받아 여기서 변환한다.

두 실행 모드(co-simulation `ufast-run`, logistics-only `ufast-fromto`)가 같은
객체를 쓰고, 값 전체가 결과 JSON 의 meta['vehicle'] 에 기록된다.
"""
from __future__ import annotations
import csv
import json
import os
from dataclasses import dataclass, asdict, replace
from typing import Any, Dict, Optional

from ufast.paths import DATASET_DIR

DEFAULT_SPEC_FILE = os.path.join(DATASET_DIR, 'vehicle_spec.json')


@dataclass(frozen=True)
class VehicleSpec:
    length_mm: float = 784.0          # 차량 본체 길이
    headway_mm: float = 125.0         # 최소 차간 거리
    max_speed_mm_s: float = 5000.0    # 최고 속도
    accel_mm_s2: float = 2000.0       # 가속도
    decel_mm_s2: float = 3500.0       # 감속도
    line_speed_mm_s: float = 5000.0   # 직선 링크 제한속도
    curve_speed_mm_s: float = 1000.0  # 곡선 링크 제한속도
    source: str = 'built-in'

    @property
    def footprint_mm(self) -> float:
        """섹션 용량 계산용 차량 점유 길이 = 본체 + 최소 차간."""
        return self.length_mm + self.headway_mm

    def kinematics(self):
        from ufast.cosim.kinematics import VehicleKinematics
        return VehicleKinematics(self.max_speed_mm_s, self.accel_mm_s2,
                                 self.decel_mm_s2)

    def as_meta(self) -> Dict[str, Any]:
        d = asdict(self)
        d['footprint_mm'] = self.footprint_mm
        return d

    def describe(self) -> str:
        return (f"Vmax {self.max_speed_mm_s / 1000:.2f} m/s, "
                f"accel {self.accel_mm_s2 / 1000:.2f} / decel "
                f"{self.decel_mm_s2 / 1000:.2f} m/s², "
                f"footprint {self.footprint_mm:.0f} mm "
                f"({self.length_mm:.0f} + {self.headway_mm:.0f}), "
                f"limits line {self.line_speed_mm_s / 1000:.1f} / curve "
                f"{self.curve_speed_mm_s / 1000:.1f} m/s [{self.source}]")


_FIELDS = ('length_mm', 'headway_mm', 'max_speed_mm_s', 'accel_mm_s2',
           'decel_mm_s2', 'line_speed_mm_s', 'curve_speed_mm_s')


def _num(s) -> float:
    return float(str(s).replace(',', '.'))


def _read_json(path: str) -> Dict[str, float]:
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    return {k: _num(raw[k]) for k in _FIELDS if k in raw}


def _read_smat2022_csv(path: str) -> Dict[str, float]:
    """SMAT2022 VehicleType.csv (탭 구분) 첫 행 → 제원. 링크 제한속도는 없음."""
    with open(path, encoding='utf-8-sig') as f:
        row = next(csv.DictReader(f, delimiter='\t'))
    out = {}
    for key, col in (('length_mm', 'SIZE'), ('headway_mm', 'MIN_DISTANCE'),
                     ('max_speed_mm_s', 'MAX_SPEED'),
                     ('accel_mm_s2', 'ACCELERATION'),
                     ('decel_mm_s2', 'DECELERATION')):
        if col in row and str(row[col]).strip():
            out[key] = _num(row[col])
    return out


def load_vehicle_spec(path: Optional[str] = None,
                      overrides: Optional[Dict[str, float]] = None) -> VehicleSpec:
    """제원 파일을 읽고(없으면 내장 기본값) overrides(mm 단위 필드명)를 덮어쓴다."""
    spec = VehicleSpec()
    src = path or (DEFAULT_SPEC_FILE if os.path.exists(DEFAULT_SPEC_FILE) else None)
    if src:
        values = (_read_json(src) if src.lower().endswith('.json')
                  else _read_smat2022_csv(src))
        spec = replace(spec, **values, source=os.path.basename(src))
    if overrides:
        clean = {k: _num(v) for k, v in overrides.items()
                 if k in _FIELDS and v is not None}
        if clean:
            spec = replace(spec, **clean, source=spec.source + ' + flags')
    return spec


# ── argparse 연동 ─────────────────────────────────────────────
def add_vehicle_args(parser) -> None:
    """두 실행기가 공유하는 차량 제원 옵션. 플래그 > 파일 > 기본 파일."""
    g = parser.add_argument_group(
        'vehicle (defaults: dataset/vehicle_spec.json; flags override)')
    g.add_argument('--vehicle-spec', metavar='PATH', default=None,
                   help='vehicle spec file (.json, or SMAT2022 VehicleType.csv)')
    g.add_argument('--oht-speed', type=float, default=None, metavar='M_S',
                   help='max speed [m/s]')
    g.add_argument('--oht-accel', type=float, default=None, metavar='M_S2',
                   help='acceleration [m/s^2]')
    g.add_argument('--oht-decel', type=float, default=None, metavar='M_S2',
                   help='deceleration [m/s^2]')
    g.add_argument('--oht-length', type=float, default=None, metavar='MM',
                   help='vehicle body length [mm]')
    g.add_argument('--oht-headway', type=float, default=None, metavar='MM',
                   help='minimum headway [mm]; section capacity = '
                        'floor(section length / (length + headway))')
    g.add_argument('--line-speed', type=float, default=None, metavar='M_S',
                   help='speed limit on straight links [m/s]')
    g.add_argument('--curve-speed', type=float, default=None, metavar='M_S',
                   help='speed limit on curved links [m/s]')


def spec_from_args(args) -> VehicleSpec:
    def m(v):        # m → mm
        return None if v is None else v * 1000.0
    overrides = {
        'max_speed_mm_s': m(getattr(args, 'oht_speed', None)),
        'accel_mm_s2': m(getattr(args, 'oht_accel', None)),
        'decel_mm_s2': m(getattr(args, 'oht_decel', None)),
        'length_mm': getattr(args, 'oht_length', None),
        'headway_mm': getattr(args, 'oht_headway', None),
        'line_speed_mm_s': m(getattr(args, 'line_speed', None)),
        'curve_speed_mm_s': m(getattr(args, 'curve_speed', None)),
    }
    return load_vehicle_spec(getattr(args, 'vehicle_spec', None), overrides)
