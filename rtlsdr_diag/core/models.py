"""Shared data models.

``KnownTransmitter`` lives in :mod:`sites` (as ``Site``) because it also needs
the bearing/range helpers; the validators for it are here.

The central rule here: a single RTL-SDR measures **frequency, level, SNR and
bandwidth**. It does not measure direction or distance, so a live detection
carries no latitude, longitude, bearing or range. Those fields exist only on
the types that genuinely have an independent source for them - a published
transmitter register (:class:`KnownTransmitter`), a bearing the operator read
off a directional antenna (:class:`BearingMeasurement`), or the operator's own
position when a level was recorded (:class:`CoverageMeasurement`).
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

from ..config import TETRA_CHANNEL_WIDTH_HZ

# ---------------------------------------------------------------------------
# Signal classes
# ---------------------------------------------------------------------------
CLASS_FM = "FM Broadcast"
CLASS_AIRBAND = "Airband"
CLASS_DAB = "DAB"
CLASS_AMATEUR = "Amateur"
CLASS_MARINE = "Marine VHF"
CLASS_ISM = "ISM"
CLASS_TETRA = "TETRA-like"
CLASS_UNKNOWN = "Unknown"

SIGNAL_CLASSES = [CLASS_FM, CLASS_AIRBAND, CLASS_DAB, CLASS_AMATEUR,
                  CLASS_MARINE, CLASS_ISM, CLASS_TETRA, CLASS_UNKNOWN]

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"

_ids = itertools.count(1)


@dataclass
class LiveSignalDetection:
    """RF energy measured by the receiver. Position is deliberately absent."""

    freq_hz: float
    level_dbfs: float
    noise_dbfs: float
    snr_db: float
    bandwidth_hz: float
    signal_class: str = CLASS_UNKNOWN
    confidence: str = CONFIDENCE_LOW
    source: str = ""
    gain_db: object = "auto"
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    hits: int = 1
    peak_level_dbfs: float = -200.0
    id: int = field(default_factory=lambda: next(_ids))
    # Which sweep passes this carrier appeared in, and how strong it was, so
    # occupancy can be worked out without assuming a fixed sweep rate.
    seen_passes: dict = field(default_factory=dict)
    # Rolling (timestamp, level_dbfs, snr_db) for trend and the history chart.
    history: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.peak_level_dbfs < -199.0:
            self.peak_level_dbfs = self.level_dbfs

    # -- convenience views -------------------------------------------------
    @property
    def frequency_hz(self) -> float:
        return self.freq_hz

    @property
    def frequency_mhz(self) -> float:
        return self.freq_hz / 1e6

    @property
    def level_db(self) -> float:
        """Relative to the receiver's full scale. Not calibrated to dBm."""
        return self.level_dbfs

    @property
    def noise_floor_db(self) -> float:
        return self.noise_dbfs

    # -- lifetime ----------------------------------------------------------
    def age_s(self) -> float:
        return time.time() - self.last_seen

    def is_active(self, timeout_s: float = 6.0) -> bool:
        return self.age_s() <= timeout_s

    @property
    def active(self) -> bool:
        return self.is_active()

    def status(self, timeout_s: float = 6.0) -> str:
        return "Active" if self.is_active(timeout_s) else "Idle"

    # -- occupancy ---------------------------------------------------------
    def occupancy(self, passes: list[int]) -> float:
        """Fraction of the given sweep passes in which this carrier was up."""
        if not passes:
            return 0.0
        seen = sum(1 for p in passes if p in self.seen_passes)
        return seen / float(len(passes))

    def bursts(self, passes: list[int]) -> int:
        """How many separate times it came on air across those passes."""
        count = 0
        was_on = False
        for p in passes:
            on = p in self.seen_passes
            if on and not was_on:
                count += 1
            was_on = on
        return count

    def channel_estimate_hz(self, raster_hz: float = TETRA_CHANNEL_WIDTH_HZ) -> float:
        """Nearest raster frequency - a display hint only."""
        if raster_hz <= 0:
            return self.freq_hz
        return round(self.freq_hz / raster_hz) * raster_hz

    MAX_HISTORY = 1800          # ~30 min at one sample a second

    def add_history(self, timestamp: float, level_db: float, snr_db: float) -> None:
        self.history.append((timestamp, level_db, snr_db))
        if len(self.history) > self.MAX_HISTORY:
            del self.history[:len(self.history) - self.MAX_HISTORY]

    def history_since(self, seconds: float, now: float | None = None) -> list:
        if not self.history:
            return []
        if now is None:
            now = self.history[-1][0]
        cutoff = now - seconds
        return [h for h in self.history if h[0] >= cutoff]

    def average_level_db(self, seconds: float = 60.0) -> float:
        rows = self.history_since(seconds)
        if not rows:
            return self.level_dbfs
        return sum(r[1] for r in rows) / len(rows)

    def peak_level_db(self) -> float:
        return self.peak_level_dbfs

    def min_level_db(self) -> float:
        if not self.history:
            return self.level_dbfs
        return min(r[1] for r in self.history)

    def describe(self) -> str:
        return "%.4f MHz  %s  %.1f dBFS  SNR %.1f dB" % (
            self.frequency_mhz, self.signal_class, self.level_dbfs, self.snr_db)


# ---------------------------------------------------------------------------
# Reference and operator-supplied data (these legitimately have position)
# ---------------------------------------------------------------------------
class ValidationError(ValueError):
    """A row of user-supplied data that cannot be accepted, with the reason."""


def validate_latitude(value: float) -> float:
    if value != value:
        raise ValidationError("latitude is not a number")
    if not -90.0 <= value <= 90.0:
        raise ValidationError("latitude %.5f is outside -90..90" % value)
    return float(value)


def validate_longitude(value: float) -> float:
    if value != value:
        raise ValidationError("longitude is not a number")
    if not -180.0 <= value <= 180.0:
        raise ValidationError("longitude %.5f is outside -180..180" % value)
    return float(value)


def validate_frequency_mhz(value: float) -> float:
    if value != value:
        raise ValidationError("frequency is not a number")
    if value <= 0:
        raise ValidationError("frequency must be greater than 0 (got %.4f)" % value)
    if value > 100_000.0:
        raise ValidationError("frequency %.4f MHz is implausible" % value)
    return float(value)


@dataclass
class BearingMeasurement:
    """A bearing read off a directional antenna at a known position."""

    observer_lat: float
    observer_lon: float
    bearing_deg: float
    freq_hz: float
    level_dbfs: float = float("nan")
    snr_db: float = float("nan")
    gain_db: object = "auto"
    label: str = ""
    notes: str = ""
    timestamp: float = field(default_factory=time.time)
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def frequency_mhz(self) -> float:
        return self.freq_hz / 1e6

    def validate(self) -> None:
        validate_latitude(self.observer_lat)
        validate_longitude(self.observer_lon)
        if not 0.0 <= self.bearing_deg < 360.0:
            raise ValidationError(
                "bearing %.1f is outside 0..360" % self.bearing_deg)


@dataclass
class CoverageMeasurement:
    """How well a frequency was received at one place."""

    observer_lat: float
    observer_lon: float
    freq_hz: float
    level_dbfs: float
    noise_dbfs: float = float("nan")
    snr_db: float = float("nan")
    gain_db: object = "auto"
    notes: str = ""
    timestamp: float = field(default_factory=time.time)
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def frequency_mhz(self) -> float:
        return self.freq_hz / 1e6

    def validate(self) -> None:
        validate_latitude(self.observer_lat)
        validate_longitude(self.observer_lon)
