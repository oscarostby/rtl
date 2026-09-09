"""Relative reception strength and trend.

Both are deliberately *relative*. Received level depends on transmit power,
antenna gain and orientation, terrain, buildings, multipath and the receiver's
own AGC, so it cannot be turned into a distance. What it can honestly say is
"reception here is strong" and "reception is getting weaker", which is what a
person actually uses when walking around with a receiver.

A distance is only ever offered for a transmitter the operator has calibrated
themselves, through :class:`CalibrationProfile`, and even then it is labelled as
an estimate for that one transmitter.
"""
from __future__ import annotations

from dataclasses import dataclass

# Buckets, weakest first.
VERY_WEAK = "Very Weak"
WEAK = "Weak"
MEDIUM = "Medium"
STRONG = "Strong"
VERY_STRONG = "Very Strong"

PROXIMITY_LEVELS = [VERY_WEAK, WEAK, MEDIUM, STRONG, VERY_STRONG]

# SNR thresholds in dB. SNR is used rather than raw level because it survives a
# gain change: turning the gain up lifts signal and noise together.
_SNR_THRESHOLDS = [
    (30.0, VERY_STRONG),
    (20.0, STRONG),
    (12.0, MEDIUM),
    (6.0, WEAK),
]

RISING = "Rising"
STABLE = "Stable"
FALLING = "Falling"

# How much the level must move before it counts as a trend rather than noise.
TREND_THRESHOLD_DB = 2.0


def proximity_label(snr_db: float) -> str:
    """Relative reception bucket from SNR. Never a distance."""
    if snr_db != snr_db:                      # NaN
        return VERY_WEAK
    for threshold, label in _SNR_THRESHOLDS:
        if snr_db >= threshold:
            return label
    return VERY_WEAK


def proximity_fraction(snr_db: float) -> float:
    """0..1 position within the bucket scale, for drawing a meter."""
    if snr_db != snr_db:
        return 0.0
    return max(0.0, min(1.0, snr_db / 40.0))


PROXIMITY_HELP = (
    "Relative reception only - not a distance. A strong reading means the "
    "signal arrives strongly here, which depends on the transmitter's power "
    "and antenna, the terrain and buildings between you, multipath, your own "
    "antenna's orientation, and the receiver's gain. A low-power transmitter "
    "in the next room and a high-power mast on a hill can read the same."
)


@dataclass
class TrendResult:
    direction: str = STABLE
    change_db: float = 0.0
    window_s: float = 0.0
    samples: int = 0

    def arrow(self) -> str:
        return {RISING: "^", FALLING: "v"}.get(self.direction, "-")

    def text(self) -> str:
        if self.samples < 4:
            return "Collecting..."
        return "%s %s  %+.1f dB / %.0f s" % (
            self.arrow(), self.direction, self.change_db, self.window_s)


def trend(history, window_s: float = 15.0, now: float | None = None) -> TrendResult:
    """Compare the newest half of a time window against the older half.

    ``history`` is a sequence of ``(timestamp, level_db, snr_db)``. This is a
    change in *reception*, not evidence of movement toward or away from
    anything - reception can change while both ends stand still.
    """
    if not history:
        return TrendResult(STABLE, 0.0, window_s, 0)
    if now is None:
        now = history[-1][0]
    cutoff = now - window_s
    recent = [(t, lv) for t, lv, _ in history if t >= cutoff]
    if len(recent) < 4:
        return TrendResult(STABLE, 0.0, window_s, len(recent))

    midpoint = cutoff + window_s / 2.0
    older = [lv for t, lv in recent if t < midpoint]
    newer = [lv for t, lv in recent if t >= midpoint]
    if not older or not newer:
        return TrendResult(STABLE, 0.0, window_s, len(recent))

    change = (sum(newer) / len(newer)) - (sum(older) / len(older))
    if change >= TREND_THRESHOLD_DB:
        direction = RISING
    elif change <= -TREND_THRESHOLD_DB:
        direction = FALLING
    else:
        direction = STABLE
    return TrendResult(direction, change, window_s, len(recent))


@dataclass
class CalibrationProfile:
    """A distance estimate for one transmitter the operator controls.

    Two reference readings at known distances give a log-distance path-loss
    exponent. This is only meaningful for the transmitter it was measured on,
    in conditions like those it was measured in, and it is the only place in
    the application where a level becomes a distance.
    """

    name: str
    freq_hz: float
    ref_distance_m: float
    ref_level_db: float
    path_loss_exponent: float = 2.0

    @classmethod
    def from_two_points(cls, name: str, freq_hz: float,
                        d1_m: float, level1_db: float,
                        d2_m: float, level2_db: float) -> "CalibrationProfile":
        import math
        if d1_m <= 0 or d2_m <= 0 or abs(d2_m - d1_m) < 1e-9:
            raise ValueError("the two reference distances must differ and be > 0")
        ratio = math.log10(d2_m / d1_m)
        if abs(ratio) < 1e-9:
            raise ValueError("the two reference distances are too close together")
        n = (level1_db - level2_db) / (10.0 * ratio)
        return cls(name, freq_hz, d1_m, level1_db, max(1.2, min(6.0, n)))

    def estimate_distance_m(self, level_db: float) -> float:
        """Distance in metres for this calibrated transmitter only."""
        import math
        exponent = (self.ref_level_db - level_db) / (10.0 * self.path_loss_exponent)
        return self.ref_distance_m * (10.0 ** exponent)

    def describe(self) -> str:
        return ("%s at %.4f MHz, reference %.0f m at %.1f dB, path-loss "
                "exponent %.2f" % (self.name, self.freq_hz / 1e6,
                                   self.ref_distance_m, self.ref_level_db,
                                   self.path_loss_exponent))
