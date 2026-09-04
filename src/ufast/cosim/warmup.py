from __future__ import annotations

from dataclasses import dataclass


SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class WarmupPolicy:
    """Timing policy for static warm-up and AMHS settling windows."""

    static_warmup_days: float = 0.0
    amhs_settling_days: float = 0.0

    def __post_init__(self):
        if self.static_warmup_days < 0:
            raise ValueError("static_warmup_days must be non-negative")
        if self.amhs_settling_days < 0:
            raise ValueError("amhs_settling_days must be non-negative")

    @property
    def static_warmup_s(self) -> float:
        return self.static_warmup_days * SECONDS_PER_DAY

    @property
    def amhs_settling_s(self) -> float:
        return self.amhs_settling_days * SECONDS_PER_DAY

    @property
    def measurement_start_s(self) -> float:
        return self.static_warmup_s + self.amhs_settling_s

    @property
    def measurement_start_days(self) -> float:
        return self.static_warmup_days + self.amhs_settling_days

    def use_static_transport(self, sim_time_s: float) -> bool:
        return sim_time_s < self.static_warmup_s

    def as_meta(self) -> dict[str, float]:
        return {
            "static_warmup_days": self.static_warmup_days,
            "amhs_settling_days": self.amhs_settling_days,
            "measurement_start_days": self.measurement_start_days,
        }
