"""Known transmitter sites and manually taken bearings.

Transmitter sites are *reference data you supply*, not something this program
can discover. Broadcast transmitter locations are public - in Norway the Nkom
frequency register publishes them - and once you enter one, the radar can show
its true bearing and distance from you and light it up with the signal level
actually being measured on its frequency.

Bearings are what you record by hand when you swing a directional antenna.
"""
from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path

from .geo import LatLon, bearing_deg, distance_km, radio_horizon_km
from .models import (ValidationError, validate_frequency_mhz,
                     validate_latitude, validate_longitude)

SITE_KINDS = ["FM", "DAB", "TV", "TETRA site", "Amateur", "Test", "Other"]

SITE_HEADER = ["name", "kind", "frequency_mhz", "latitude", "longitude",
               "height_m", "power_kw", "notes"]

BEARING_HEADER = ["timestamp", "label", "frequency_mhz", "latitude", "longitude",
                  "bearing_deg", "level_dbfs", "notes"]

COVERAGE_HEADER = ["timestamp", "latitude", "longitude", "frequency_mhz",
                   "level_dbfs", "noise_dbfs", "snr_db", "notes"]


@dataclass
class Site:
    """A transmitter whose position is known from published data.

    Exported as ``KnownTransmitter`` too - reference data, never something the
    receiver discovered.
    """

    name: str
    kind: str = "Other"
    freq_hz: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    height_m: float = 0.0
    power_kw: float = 0.0
    notes: str = ""

    # Filled in live while measuring
    level_dbfs: float = float("nan")
    snr_db: float = float("nan")
    last_heard: float = 0.0

    def position(self) -> LatLon:
        return LatLon(self.lat, self.lon)

    def bearing_from(self, here: LatLon) -> float:
        return bearing_deg(here, self.position())

    def distance_from(self, here: LatLon) -> float:
        return distance_km(here, self.position())

    def horizon_km(self, rx_height_m: float = 2.0) -> float:
        return radio_horizon_km(self.height_m, rx_height_m)

    def is_heard(self, timeout_s: float = 30.0) -> bool:
        return self.last_heard > 0.0 and (time.time() - self.last_heard) <= timeout_s

    @property
    def frequency_mhz(self) -> float:
        return self.freq_hz / 1e6

    def validate(self) -> None:
        """Raise ValidationError with a usable message if the row is bad."""
        if not (self.name or "").strip():
            raise ValidationError("name is empty")
        validate_latitude(self.lat)
        validate_longitude(self.lon)
        # A site with no frequency cannot be matched against anything, so it is
        # rejected rather than quietly loaded as a marker that never lights up.
        validate_frequency_mhz(self.freq_hz / 1e6)

    def matches_frequency(self, freq_hz: float, tolerance_hz: float) -> bool:
        """Frequency proximity only - never proof the signal came from here."""
        if self.freq_hz <= 0 or freq_hz <= 0:
            return False
        return abs(self.freq_hz - freq_hz) <= tolerance_hz

    def to_row(self) -> list:
        return [self.name, self.kind, "%.6f" % (self.freq_hz / 1e6),
                "%.6f" % self.lat, "%.6f" % self.lon,
                "%.1f" % self.height_m, "%.3f" % self.power_kw, self.notes]

    @classmethod
    def from_row(cls, row: dict) -> "Site":
        def num(key, default=0.0):
            try:
                return float(str(row.get(key, "")).replace(",", ".").strip())
            except (TypeError, ValueError):
                return default

        return cls(
            name=(row.get("name") or "unnamed").strip(),
            kind=(row.get("kind") or "Other").strip(),
            freq_hz=num("frequency_mhz") * 1e6,
            lat=num("latitude"),
            lon=num("longitude"),
            height_m=num("height_m"),
            power_kw=num("power_kw"),
            notes=(row.get("notes") or "").strip(),
        )


@dataclass
class BearingRecord:
    """One bearing taken by hand from a known position."""

    label: str
    freq_hz: float
    lat: float
    lon: float
    bearing_deg: float
    level_dbfs: float = float("nan")
    notes: str = ""
    timestamp: float = field(default_factory=time.time)

    def position(self) -> LatLon:
        return LatLon(self.lat, self.lon)

    def to_row(self) -> list:
        import datetime as _dt
        return [_dt.datetime.fromtimestamp(self.timestamp).isoformat(timespec="seconds"),
                self.label, "%.6f" % (self.freq_hz / 1e6),
                "%.6f" % self.lat, "%.6f" % self.lon,
                "%.2f" % self.bearing_deg,
                "" if self.level_dbfs != self.level_dbfs else "%.2f" % self.level_dbfs,
                self.notes]


@dataclass
class CoveragePoint:
    """Signal strength measured at a known position.

    This is what a coverage survey actually produces: not a guess at where a
    transmitter is, but a record of how well you received it from each place
    you stood. Enough of these and the map of good and bad reception draws
    itself.
    """

    lat: float
    lon: float
    freq_hz: float
    level_dbfs: float
    noise_dbfs: float = float("nan")
    snr_db: float = float("nan")
    notes: str = ""
    timestamp: float = field(default_factory=time.time)

    def position(self) -> LatLon:
        return LatLon(self.lat, self.lon)

    def to_row(self) -> list:
        import datetime as _dt

        def num(v):
            return "" if v != v else "%.2f" % v

        return [_dt.datetime.fromtimestamp(self.timestamp).isoformat(timespec="seconds"),
                "%.6f" % self.lat, "%.6f" % self.lon,
                "%.6f" % (self.freq_hz / 1e6),
                num(self.level_dbfs), num(self.noise_dbfs), num(self.snr_db),
                self.notes]


class CoverageLog:
    """A list of coverage measurements, with CSV import and export."""

    def __init__(self) -> None:
        self.points: list[CoveragePoint] = []

    def __len__(self) -> int:
        return len(self.points)

    def add(self, point: CoveragePoint) -> CoveragePoint:
        self.points.append(point)
        return point

    def clear(self) -> None:
        self.points.clear()

    def for_frequency(self, freq_hz: float, tolerance_hz: float = 50_000.0):
        return [p for p in self.points if abs(p.freq_hz - freq_hz) <= tolerance_hz]

    def level_range(self) -> tuple[float, float]:
        levels = [p.level_dbfs for p in self.points if p.level_dbfs == p.level_dbfs]
        if not levels:
            return (-100.0, -20.0)
        lo, hi = min(levels), max(levels)
        if hi - lo < 1.0:
            hi = lo + 1.0
        return lo, hi

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(COVERAGE_HEADER)
            for point in self.points:
                w.writerow(point.to_row())
        return p

    def load(self, path: str | Path, replace: bool = True) -> tuple[int, list[str]]:
        p = Path(path)
        warnings: list[str] = []
        loaded: list[CoveragePoint] = []
        with open(p, newline="", encoding="utf-8-sig") as fh:
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
                lat, lon = num(row, "latitude"), num(row, "longitude")
                if lat != lat or lon != lon:
                    warnings.append("line %d: unreadable coordinates" % n)
                    continue
                point = CoveragePoint(
                    lat=lat, lon=lon,
                    freq_hz=num(row, "frequency_mhz", 0.0) * 1e6,
                    level_dbfs=num(row, "level_dbfs"),
                    noise_dbfs=num(row, "noise_dbfs"),
                    snr_db=num(row, "snr_db"),
                    notes=(row.get("notes") or "").strip())
                if not point.position().is_valid():
                    warnings.append("line %d: coordinates out of range" % n)
                    continue
                loaded.append(point)
        if replace:
            self.points = loaded
        else:
            self.points.extend(loaded)
        return len(loaded), warnings


class SiteStore:
    """Holds the site list and the recorded bearings, and reads/writes CSV."""

    def __init__(self) -> None:
        self.sites: list[Site] = []
        self.bearings: list[BearingRecord] = []

    # -- sites -------------------------------------------------------------
    def add_site(self, site: Site) -> Site:
        self.sites.append(site)
        return site

    def remove_site(self, index: int) -> None:
        if 0 <= index < len(self.sites):
            del self.sites[index]

    def clear_sites(self) -> None:
        self.sites.clear()

    def load_sites(self, path: str | Path, replace: bool = True) -> tuple[int, list[str]]:
        """Read sites from CSV. Returns (loaded, warnings)."""
        p = Path(path)
        warnings: list[str] = []
        loaded: list[Site] = []
        with open(p, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                return 0, ["The file is empty."]
            missing = [c for c in ("name", "latitude", "longitude")
                       if c not in reader.fieldnames]
            if missing:
                return 0, ["Missing required column(s): %s. Expected header: %s"
                           % (", ".join(missing), ", ".join(SITE_HEADER))]
            for n, row in enumerate(reader, start=2):
                name = (row.get("name") or "").strip()
                if name.startswith("#"):
                    continue          # commented-out template row
                if not name:
                    # A genuinely blank line is nothing; a row with data but no
                    # name is malformed, and saying so beats dropping it.
                    if any((v or "").strip() for v in row.values()):
                        warnings.append("line %d: row has data but no name" % n)
                    continue
                site = Site.from_row(row)
                try:
                    site.validate()
                except ValidationError as exc:
                    warnings.append("line %d (%s): %s" % (n, name, exc))
                    continue
                loaded.append(site)
        if replace:
            self.sites = loaded
        else:
            self.sites.extend(loaded)
        return len(loaded), warnings

    def save_sites(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(SITE_HEADER)
            for site in self.sites:
                w.writerow(site.to_row())
        return p

    # -- bearings ----------------------------------------------------------
    def add_bearing(self, record: BearingRecord) -> BearingRecord:
        self.bearings.append(record)
        return record

    def remove_bearing(self, index: int) -> None:
        if 0 <= index < len(self.bearings):
            del self.bearings[index]

    def clear_bearings(self) -> None:
        self.bearings.clear()

    def save_bearings(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(BEARING_HEADER)
            for rec in self.bearings:
                w.writerow(rec.to_row())
        return p

    # -- live correlation --------------------------------------------------
    def update_levels(self, freqs_hz, power_db, noise_db: float,
                      bandwidth_hz: float = 200_000.0) -> list[Site]:
        """Mark sites whose frequency falls inside the measured span.

        Returns the sites that were updated.
        """
        import numpy as np

        if freqs_hz is None or len(freqs_hz) == 0:
            return []
        lo, hi = float(freqs_hz[0]), float(freqs_hz[-1])
        touched: list[Site] = []
        now = time.time()
        for site in self.sites:
            if site.freq_hz <= 0 or not (lo <= site.freq_hz <= hi):
                continue
            mask = np.abs(freqs_hz - site.freq_hz) <= bandwidth_hz / 2.0
            if not mask.any():
                idx = int(np.argmin(np.abs(freqs_hz - site.freq_hz)))
                level = float(power_db[idx])
            else:
                level = float(np.max(power_db[mask]))
            site.level_dbfs = level
            site.snr_db = level - noise_db
            site.last_heard = now
            touched.append(site)
        return touched


def write_template(path: str | Path) -> Path:
    """Write a CSV template with the expected columns and worked examples."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(SITE_HEADER)
        # Every template row is commented out with a leading "#", so loading
        # this file as-is adds nothing. Delete the "#" once a row holds real
        # data from a published register.
        w.writerow(["# Fill in transmitter data from a public register - in",
                    "", "", "", "", "", "",
                    "Norway that is the Nkom frequency register."])
        w.writerow(["# Remove the leading # to activate a row.",
                    "", "", "", "", "", "", ""])
        w.writerow(["# Example DAB block", "DAB", "222.064",
                    "59.00000", "10.00000", "300", "10",
                    "PLACEHOLDER coordinates - look up the real site"])
        w.writerow(["# Example FM station", "FM", "99.300",
                    "59.00000", "10.00000", "300", "5",
                    "PLACEHOLDER coordinates - look up the real site"])
    return p


# Spec name for the same thing: reference data about a fixed transmitter.
KnownTransmitter = Site
