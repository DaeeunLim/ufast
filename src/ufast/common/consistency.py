"""
consistency.py — input data consistency check.

Simulation input comes in paired sources:
  fromto mode    : from/to equipment in fromto.dat ↔ EQ list of the .rail layout
  production mode: STNFAM family in tool.txt ↔ .rail eq_to_node / Equipment.csv

Entries whose names do not match are silently filtered out by existing code
(fromto: EventHandler.init_lot_events filter, production: skipped_transport).
The runner prints a summary via this module before the simulation starts,
and in --strict mode aborts the run on a mismatch.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

# Maximum number of unmatched names listed by summary()
_MAX_LISTED = 10


class ConsistencyError(ValueError):
    """Raised on a consistency violation in strict mode."""


@dataclass
class ConsistencyReport:
    context: str            # 'fromto' | 'production'
    subject: str            # unit being checked ('records' | 'families')
    total: int
    valid: int
    unknown: Counter = field(default_factory=Counter)  # unmatched name → occurrence count

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
    """Check that the from/to equipment of fromto records exist in the layout EQ list.

    known_eq: set of EQ names after loading .rail (e.g. SimulatorDataSet.eq_list).
    Uses the same criterion (exact match) as EventHandler.init_lot_events.
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
    """Check that production families (STNFAM) can be resolved to rail destinations.

    Same criterion as the resolution order in UFastInstance:
      1) the family has an entry in the Equipment.csv mapping (machine_equipment), or
      2) the family exists in eq_to_node (case-insensitive).
    Transports of unresolved families are silently skipped in the simulation
    (skipped_transport), so they are surfaced here in advance.
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
