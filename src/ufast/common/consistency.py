"""
consistency.py — 입력 데이터 정합성 검사.

시뮬레이션 입력은 두 소스가 짝을 이룬다:
  fromto 모드    : fromto.dat 의 from/to 설비 ↔ .rail 레이아웃의 EQ 목록
  production 모드: tool.txt 의 STNFAM family ↔ .rail eq_to_node / Equipment.csv

이름이 일치하지 않는 항목은 기존 코드가 조용히 걸러낸다
(fromto: EventHandler.init_lot_events 필터, production: skipped_transport).
러너가 시뮬레이션 시작 전에 이 모듈로 요약을 출력하고,
--strict 모드에서는 불일치 시 실행을 중단한다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

# summary() 에서 나열하는 미매칭 이름 최대 개수
_MAX_LISTED = 10


class ConsistencyError(ValueError):
    """strict 모드에서 정합성 위반 시 발생."""


@dataclass
class ConsistencyReport:
    context: str            # 'fromto' | 'production'
    subject: str            # 검사 대상 단위 ('records' | 'families')
    total: int
    valid: int
    unknown: Counter = field(default_factory=Counter)  # 미매칭 이름 → 등장 횟수

    @property
    def ok(self) -> bool:
        return not self.unknown

    def summary(self) -> str:
        head = (f"consistency[{self.context}]: "
                f"{self.valid:,}/{self.total:,} {self.subject} matched")
        if self.ok:
            return head + " — OK"
        names = self.unknown.most_common()
        listed = ", ".join(f"{n}(x{c})" for n, c in names[:_MAX_LISTED])
        more = f" … +{len(names) - _MAX_LISTED} more" if len(names) > _MAX_LISTED else ""
        return (f"{head} — {len(names)} unknown name(s) not in layout: "
                f"{listed}{more}")

    def raise_if_invalid(self) -> None:
        if not self.ok:
            raise ConsistencyError(self.summary())


def check_fromto(fromto_data: Iterable[Tuple[str, str, float]],
                 known_eq: Iterable[str]) -> ConsistencyReport:
    """fromto 레코드의 from/to 설비가 레이아웃 EQ 목록에 있는지 검사한다.

    known_eq: .rail 로드 후의 EQ 이름 집합 (예: SimulatorDataSet.eq_list).
    EventHandler.init_lot_events 와 동일한 기준(정확 일치)을 쓴다.
    """
    known = set(known_eq)
    unknown: Counter = Counter()
    total = valid = 0
    for from_eq, to_eq, _rate in fromto_data:
        total += 1
        missing = [eq for eq in (from_eq, to_eq) if eq not in known]
        if missing:
            unknown.update(missing)
        else:
            valid += 1
    return ConsistencyReport('fromto', 'records', total, valid, unknown)


def check_production_families(
        families: Iterable[str],
        eq_to_node: Dict[str, Any],
        machine_equipment: Dict[str, List[Dict[str, Any]]] | None = None,
) -> ConsistencyReport:
    """production family(STNFAM)가 레일 목적지로 해석 가능한지 검사한다.

    UFastInstance 의 해석 순서와 동일한 기준:
      1) Equipment.csv 매핑(machine_equipment)에 family 항목이 있거나
      2) eq_to_node 에 family 가 있으면 (대소문자 무시) 해석 가능.
    미해석 family 의 이송은 시뮬레이션에서 조용히 스킵되므로
    (skipped_transport), 사전에 여기서 드러낸다.
    """
    me = machine_equipment or {}
    node_keys_ci = {str(k).lower() for k in eq_to_node}
    unknown: Counter = Counter()
    fam_list = list(families)
    valid = 0
    for fam in fam_list:
        if me.get(fam) or str(fam).lower() in node_keys_ci:
            valid += 1
        else:
            unknown[fam] += 1
    return ConsistencyReport('production', 'families', len(fam_list), valid, unknown)
