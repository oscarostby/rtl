"""Exponential moving average for level meters.

FAST/MEDIUM/SLOW are time constants, not sample counts, so the smoothing feels
the same whether frames arrive at 5/s or 25/s.
"""
from __future__ import annotations

import math

FAST = "FAST"
MEDIUM = "MEDIUM"
SLOW = "SLOW"
MODES = [FAST, MEDIUM, SLOW]

# Time constant in seconds for each mode.
TIME_CONSTANT_S = {FAST: 0.15, MEDIUM: 0.6, SLOW: 2.5}


class LevelSmoother:
    """Tracks current/average/min/max/peak-hold of a level in dB."""

    def __init__(self, mode: str = MEDIUM):
        self.mode = mode if mode in MODES else MEDIUM
        self.reset()

    def reset(self) -> None:
        self.value = float("nan")
        self.peak = float("-inf")
        self.minimum = float("inf")
        self.maximum = float("-inf")
        self._sum_lin = 0.0
        self._count = 0
        self._last_t = None

    def set_mode(self, mode: str) -> None:
        if mode in MODES:
            self.mode = mode

    def alpha(self, dt: float) -> float:
        """Weight for a new sample dt seconds after the previous one."""
        tau = TIME_CONSTANT_S[self.mode]
        if dt <= 0 or tau <= 0:
            return 1.0
        return 1.0 - math.exp(-dt / tau)

    def update(self, level_db: float, timestamp: float | None = None) -> float:
        if level_db != level_db:            # NaN in, nothing to do
            return self.value
        if timestamp is None:
            dt = TIME_CONSTANT_S[self.mode]
        else:
            dt = (timestamp - self._last_t) if self._last_t is not None else 1e9
            self._last_t = timestamp

        if self.value != self.value:        # first sample
            self.value = float(level_db)
        else:
            a = self.alpha(dt)
            self.value = (1.0 - a) * self.value + a * float(level_db)

        self.peak = max(self.peak, float(level_db))
        self.minimum = min(self.minimum, float(level_db))
        self.maximum = max(self.maximum, float(level_db))
        self._sum_lin += 10.0 ** (float(level_db) / 10.0)
        self._count += 1
        return self.value

    @property
    def average(self) -> float:
        if not self._count:
            return float("nan")
        return 10.0 * math.log10(self._sum_lin / self._count)

    @property
    def count(self) -> int:
        return self._count
