"""A record of what was on the air, and when.

Built for a survey drive: you pass a place, signals come and go, and afterwards
you want to know what was there. Each time a channel becomes active that is one
sighting; if it falls quiet and later returns, that is another.

The one distinction this can honestly draw is between a *terminal* and *fixed
infrastructure*, and it draws it from the band plan alone. TETRA is frequency
duplex: handsets and vehicle radios transmit in the lower sub-band, base
stations in the upper one. So the frequency says which end of a link you are
hearing, and nothing else about the signal is examined.

It does not say whose radio it is. A transmitter's owner, operator, service or
purpose is not measurable from RF energy, is not recorded here, and must not be
inferred from these rows.
"""
from __future__ import annotations

import csv
import datetime
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import TETRA_CHANNEL_WIDTH_HZ
from .detector import DAB, FM, MODE_SPECS, TETRA_MAST, TETRA_MOBILE

KIND_MAST = "MAST"          # fixed infrastructure: a base station downlink
KIND_MOBILE = "MOBILE"      # a terminal transmitting near you: an uplink
KIND_FM = "FM"
KIND_DAB = "DAB"
KIND_OTHER = "OTHER"

KIND_ORDER = [KIND_MOBILE, KIND_MAST, KIND_FM, KIND_DAB, KIND_OTHER]

# What each kind means, for the panel legend and the exported file.
KIND_MEANING = {
    KIND_MOBILE: "terminal transmitting nearby (uplink)",
    KIND_MAST: "fixed infrastructure (downlink)",
    KIND_FM: "FM broadcast",
    KIND_DAB: "DAB ensemble",
    KIND_OTHER: "unclassified",
}

_BAND_KIND = {TETRA_MOBILE: KIND_MOBILE, TETRA_MAST: KIND_MAST,
              FM: KIND_FM, DAB: KIND_DAB}

# A channel that goes quiet for this long and then comes back counts as a new
# sighting rather than a continuation of the old one.
SIGHTING_GAP_S = 20.0

# Anything shorter than this was a burst: somebody keyed up and released.
BRIEF_S = 6.0


def kind_for(detection) -> str:
    """Which end of a link this is, decided by the published band plan."""
    for band, kind in _BAND_KIND.items():
        if MODE_SPECS[band].accepts(detection):
            return kind
    return KIND_OTHER


def channel_of(freq_hz: float) -> float:
    """Snap to the 12.5 kHz raster, so one carrier makes one row."""
    step = TETRA_CHANNEL_WIDTH_HZ / 2.0
    return round(freq_hz / step) * step


@dataclass
class Sighting:
    """One stretch of activity on one channel."""

    channel_hz: float
    kind: str
    first_seen: float
    last_seen: float
    hits: int = 1
    best_snr_db: float = 0.0
    last_snr_db: float = 0.0
    best_level_dbfs: float = -200.0
    bandwidth_hz: float = 0.0
    id: int = 0

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_seen - self.first_seen)

    @property
    def frequency_mhz(self) -> float:
        return self.channel_hz / 1e6

    def is_brief(self) -> bool:
        """A burst, rather than a carrier that simply sits there."""
        return self.duration_s < BRIEF_S

    def is_active(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.last_seen) <= SIGHTING_GAP_S

    def clock(self) -> str:
        return datetime.datetime.fromtimestamp(self.first_seen).strftime("%H:%M:%S")

    def describe(self) -> str:
        return "%.4f MHz  %s  %.0f dB  %d hits  %.0f s" % (
            self.frequency_mhz, self.kind, self.best_snr_db, self.hits,
            self.duration_s)


class ObservationLog:
    """Every sighting this session, newest activity first."""

    CSV_HEADER = ["first_seen", "last_seen", "kind", "meaning",
                  "channel_mhz", "best_snr_db", "last_snr_db",
                  "best_level_dbfs", "bandwidth_hz", "hits", "duration_s",
                  "brief_burst"]

    def __init__(self, capacity: int = 500, gap_s: float = SIGHTING_GAP_S):
        self.capacity = int(capacity)
        self.gap_s = float(gap_s)
        self._sightings: list[Sighting] = []
        self._open: dict = {}
        self._next_id = 1

    # ------------------------------------------------------------------
    def record(self, detections, now: float | None = None) -> list:
        """Fold one sweep's detections in. Returns the sightings that are new."""
        if now is None:
            now = time.time()
        started = []
        for det in detections:
            kind = kind_for(det)
            key = (kind, channel_of(det.freq_hz))
            open_one = self._open.get(key)
            if open_one is not None and (now - open_one.last_seen) <= self.gap_s:
                open_one.last_seen = now
                open_one.hits += 1
                open_one.last_snr_db = det.snr_db
                open_one.best_snr_db = max(open_one.best_snr_db, det.snr_db)
                open_one.best_level_dbfs = max(open_one.best_level_dbfs,
                                               det.level_dbfs)
                continue
            fresh = Sighting(
                channel_hz=key[1], kind=kind, first_seen=now, last_seen=now,
                best_snr_db=det.snr_db, last_snr_db=det.snr_db,
                best_level_dbfs=det.level_dbfs, bandwidth_hz=det.bandwidth_hz,
                id=self._next_id)
            self._next_id += 1
            self._open[key] = fresh
            self._sightings.append(fresh)
            started.append(fresh)
        self._trim()
        return started

    def _trim(self) -> None:
        if len(self._sightings) <= self.capacity:
            return
        keep = set()
        self._sightings = self._sightings[len(self._sightings) - self.capacity:]
        for s in self._sightings:
            keep.add(id(s))
        for key, s in list(self._open.items()):
            if id(s) not in keep:
                del self._open[key]

    # ------------------------------------------------------------------
    def entries(self, kind: str = "") -> list:
        """Newest activity first, so a drive reads top-down."""
        rows = [s for s in self._sightings if not kind or s.kind == kind]
        return sorted(rows, key=lambda s: s.last_seen, reverse=True)

    def counts(self) -> dict:
        out = {k: 0 for k in KIND_ORDER}
        for s in self._sightings:
            out[s.kind] = out.get(s.kind, 0) + 1
        return out

    def channels(self, kind: str) -> int:
        """How many distinct channels of a kind were seen at all."""
        return len({s.channel_hz for s in self._sightings if s.kind == kind})

    def clear(self) -> None:
        self._sightings.clear()
        self._open.clear()

    def __len__(self) -> int:
        return len(self._sightings)

    # ------------------------------------------------------------------
    def rows(self) -> list:
        def stamp(t):
            return datetime.datetime.fromtimestamp(t).isoformat(
                timespec="seconds")

        return [[stamp(s.first_seen), stamp(s.last_seen), s.kind,
                 KIND_MEANING.get(s.kind, ""), "%.4f" % s.frequency_mhz,
                 "%.1f" % s.best_snr_db, "%.1f" % s.last_snr_db,
                 "%.1f" % s.best_level_dbfs, "%.0f" % s.bandwidth_hz,
                 s.hits, "%.1f" % s.duration_s,
                 "yes" if s.is_brief() else "no"]
                for s in sorted(self._sightings, key=lambda x: x.first_seen)]

    def save(self, path) -> Path:
        target = Path(path)
        with open(target, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(self.CSV_HEADER)
            writer.writerows(self.rows())
        return target
