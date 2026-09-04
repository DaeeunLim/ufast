"""
FromTo 파일 파서
형식: 탭 구분 텍스트 — FromEQ\tToEQ\tRate(건/시간)
세 번째 열은 시간 당 발생율(hourly occurrence rate, λ [건/시간, req/hr])이다.
고정 간격(fixed interval) 시뮬레이션 시 발생 간격은 Δt = 3600.0 / rate (초)이다.
"""
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple


def load_fromto(filepath: str) -> List[Tuple[str, str, float]]:
    """
    FromTo 파일을 읽어 (from_eq, to_eq, rate) 리스트를 반환한다.
    rate는 시간 당 발생율(건/시간, req/hr)이다.
    """
    records: List[Tuple[str, str, float]] = []

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) < 3:
                continue
            from_eq = parts[0].strip()
            to_eq = parts[1].strip()
            try:
                rate = float(parts[2])
            except ValueError:
                continue
            records.append((from_eq, to_eq, rate))

    return records


def group_fromto_rates(
    fromto_data: Iterable[Tuple[str, str, float]],
) -> Dict[Tuple[str, str], List[float]]:
    """
    FromTo 레코드들을 (from_eq, to_eq) 쌍별 시간대별 발생율 리스트로 그룹화한다.
    반환: {(from_eq, to_eq): [rate_h0, rate_h1, rate_h2, ...]}
    """
    grouped: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for from_eq, to_eq, rate in fromto_data:
        grouped[(from_eq, to_eq)].append(float(rate))
    return dict(grouped)


def generate_fixed_interval_events(
    fromto_data: Iterable[Tuple[str, str, float]],
    sim_duration: float = 3600.0,
    offset_ratio: float = 0.5,
) -> List[Tuple[float, str, str]]:
    """
    시간당 발생율(rate)을 기반으로 고정 간격(fixed interval: Δt = 3600/rate 초)의
    이벤트 목록 [(timestamp_sec, from_eq, to_eq), ...] 을 생성한다.

    - sim_duration: 시뮬레이션 기간 (초)
    - offset_ratio: 첫 번째 이벤트의 오프셋 비율 (기본값 0.5: 간격의 중간 지점)
    """
    grouped = group_fromto_rates(fromto_data)
    events: List[Tuple[float, str, str]] = []

    for (from_eq, to_eq), rates in grouped.items():
        if not rates:
            continue
        r0 = rates[0]
        if r0 <= 0:
            t = 3600.0
        else:
            dt0 = 3600.0 / r0
            t = dt0 * offset_ratio

        while t < sim_duration:
            h = int(t // 3600)
            r = rates[h % len(rates)]
            if r <= 0:
                t = (h + 1) * 3600.0
                continue
            events.append((t, from_eq, to_eq))
            t += 3600.0 / r

    events.sort(key=lambda x: x[0])
    return events
