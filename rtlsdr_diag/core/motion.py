"""Stationary/drive mode, and the drive survey recorder.

Mode switching uses hysteresis on purpose: a single GPS speed sample crossing a
threshold should not flip the whole page between behaviours. The receiver must
be slow for a while before it is called stationary, and moving for a while
before it is called driving; in between, whatever it was stays.

Without a GPS source the mode is Stationary. That is a statement about what is
known, not a guess.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

AUTO = "AUTO"
STATIONARY = "STATIONARY"
DRIVE = "DRIVE"
MODES = [AUTO, STATIONARY, DRIVE]

# Thresholds and dwell times from the specification.
STATIONARY_SPEED_KMH = 2.0
STATIONARY_DWELL_S = 15.0
MOVING_SPEED_KMH = 5.0
MOVING_DWELL_S = 5.0


class MotionMode:
    """Tracks whether the receiver is stationary or moving.

    ``selected`` is what the operator chose (AUTO/STATIONARY/DRIVE) and
    ``effective`` is what that resolves to right now.
    """

    def __init__(self, selected: str = AUTO):
        self.selected = selected if selected in MODES else AUTO
        self._effective = STATIONARY
        self._slow_since: float | None = None
        self._fast_since: float | None = None
        self.last_speed_kmh: float | None = None
        self.last_fix_time: float | None = None

    # ------------------------------------------------------------------
    @property
    def effective(self) -> str:
        if self.selected != AUTO:
            return self.selected
        return self._effective

    @property
    def has_gps(self) -> bool:
        return self.last_speed_kmh is not None

    def select(self, mode: str) -> None:
        if mode in MODES:
            self.selected = mode

    def update_speed(self, speed_kmh: float | None,
                     now: float | None = None) -> str:
        """Feed a GPS speed. Returns the effective mode."""
        if now is None:
            now = time.time()
        if speed_kmh is None:
            # No fix: hold the current mode rather than inventing movement.
            self._slow_since = None
            self._fast_since = None
            return self.effective

        self.last_speed_kmh = float(speed_kmh)
        self.last_fix_time = now

        if speed_kmh < STATIONARY_SPEED_KMH:
            self._fast_since = None
            if self._slow_since is None:
                self._slow_since = now
            elif now - self._slow_since >= STATIONARY_DWELL_S:
                self._effective = STATIONARY
        elif speed_kmh > MOVING_SPEED_KMH:
            self._slow_since = None
            if self._fast_since is None:
                self._fast_since = now
            elif now - self._fast_since >= MOVING_DWELL_S:
                self._effective = DRIVE
        else:
            # Between the thresholds: keep whatever we already decided.
            self._slow_since = None
            self._fast_since = None
        return self.effective

    def describe(self) -> str:
        if self.selected != AUTO:
            return self.selected
        if not self.has_gps:
            return "AUTO -> STATIONARY (no GPS)"
        return "AUTO -> %s (%.1f km/h)" % (self._effective, self.last_speed_kmh)


@dataclass
class SurveySample:
    timestamp: float
    lat: float
    lon: float
    speed_kmh: float
    freq_hz: float
    level_dbfs: float
    snr_db: float
    gain_db: object = "auto"

    def to_row(self) -> list:
        import datetime as _dt

        def num(v):
            return "" if v != v else "%.2f" % v

        return [_dt.datetime.fromtimestamp(self.timestamp).isoformat(
                    timespec="seconds"),
                "%.6f" % self.lat, "%.6f" % self.lon, "%.1f" % self.speed_kmh,
                "%.6f" % (self.freq_hz / 1e6), num(self.level_dbfs),
                num(self.snr_db),
                self.gain_db if isinstance(self.gain_db, str)
                else "%.1f" % float(self.gain_db)]


SURVEY_HEADER = ["timestamp", "latitude", "longitude", "speed_kmh",
                 "frequency_mhz", "level_dbfs", "snr_db", "gain"]


@dataclass
class DriveSurvey:
    """Signal level against position, sampled while moving.

    This records where reception was good. It does not locate a transmitter:
    a strong patch means the path was good there, which is a fact about the
    route rather than about the source.
    """

    interval_s: float = 1.0
    samples: list = field(default_factory=list)
    _last_time: float = 0.0

    def __len__(self) -> int:
        return len(self.samples)

    def clear(self) -> None:
        self.samples.clear()
        self._last_time = 0.0

    def should_record(self, now: float) -> bool:
        return (now - self._last_time) >= self.interval_s

    def record(self, lat: float, lon: float, speed_kmh: float, freq_hz: float,
               level_dbfs: float, snr_db: float, gain_db="auto",
               now: float | None = None, force: bool = False):
        if now is None:
            now = time.time()
        if not force and not self.should_record(now):
            return None
        sample = SurveySample(now, lat, lon, speed_kmh, freq_hz, level_dbfs,
                              snr_db, gain_db)
        self.samples.append(sample)
        self._last_time = now
        return sample

    # -- summaries ---------------------------------------------------------
    def strongest(self):
        valid = [s for s in self.samples if s.level_dbfs == s.level_dbfs]
        return max(valid, key=lambda s: s.level_dbfs) if valid else None

    def weakest(self):
        valid = [s for s in self.samples if s.level_dbfs == s.level_dbfs]
        return min(valid, key=lambda s: s.level_dbfs) if valid else None

    def level_range(self) -> tuple[float, float]:
        valid = [s.level_dbfs for s in self.samples if s.level_dbfs == s.level_dbfs]
        if not valid:
            return (-100.0, -20.0)
        lo, hi = min(valid), max(valid)
        return (lo, hi if hi - lo >= 1.0 else lo + 1.0)

    def route_length_km(self) -> float:
        from .geo import LatLon, distance_km
        total = 0.0
        for a, b in zip(self.samples, self.samples[1:]):
            total += distance_km(LatLon(a.lat, a.lon), LatLon(b.lat, b.lon))
        return total

    def gains_used(self) -> set:
        return {s.gain_db if isinstance(s.gain_db, str) else round(float(s.gain_db), 1)
                for s in self.samples}

    # -- CSV ---------------------------------------------------------------
    def save(self, path):
        import csv
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(SURVEY_HEADER)
            for s in self.samples:
                w.writerow(s.to_row())
        return p

    def load(self, path, replace: bool = True) -> tuple[int, list[str]]:
        import csv
        from pathlib import Path
        from .models import ValidationError, validate_latitude, validate_longitude
        warnings: list[str] = []
        loaded: list[SurveySample] = []
        with open(Path(path), newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                return 0, ["The file is empty."]
            missing = [c for c in ("latitude", "longitude", "level_dbfs")
                       if c not in reader.fieldnames]
            if missing:
                return 0, ["Missing required column(s): %s" % ", ".join(missing)]

            def num(row, key, default=float("nan")):
                try:
                    return float(str(row.get(key, "")).replace(",", ".").strip())
                except (TypeError, ValueError):
                    return default

            for n, row in enumerate(reader, start=2):
                try:
                    lat = validate_latitude(num(row, "latitude"))
                    lon = validate_longitude(num(row, "longitude"))
                except ValidationError as exc:
                    warnings.append("line %d: %s" % (n, exc))
                    continue
                loaded.append(SurveySample(
                    timestamp=time.time(), lat=lat, lon=lon,
                    speed_kmh=num(row, "speed_kmh", 0.0),
                    freq_hz=num(row, "frequency_mhz", 0.0) * 1e6,
                    level_dbfs=num(row, "level_dbfs"),
                    snr_db=num(row, "snr_db"),
                    gain_db=(row.get("gain") or "auto").strip()))
        if replace:
            self.samples = loaded
        else:
            self.samples.extend(loaded)
        return len(loaded), warnings
