"""The one place live detections live.

Scanner, TETRA RF Check and the spectrum display all publish here, and any page
that wants to show live signals subscribes. That is what stops the Radar page
from needing its own scan - and therefore stops two components fighting over a
single RTL-SDR.

This supersedes the older ``DetectionTracker``; ``detections.py`` still exports
the old names so existing imports keep working.
"""
from __future__ import annotations

import threading
import time

from .classify import classify
from .models import LiveSignalDetection

DEFAULT_TOLERANCE_HZ = 12_500.0
DEFAULT_ACTIVE_TIMEOUT_S = 6.0
DEFAULT_STALE_TIMEOUT_S = 300.0
MIN_HITS_CONFIRMED = 2


class SignalStore:
    """Tracks live detections, merges repeat sightings, notifies subscribers."""

    MAX_PASS_HISTORY = 400

    def __init__(self, tolerance_hz: float = DEFAULT_TOLERANCE_HZ,
                 active_timeout_s: float = DEFAULT_ACTIVE_TIMEOUT_S):
        self.tolerance_hz = tolerance_hz
        self.active_timeout_s = active_timeout_s
        self._items: list[LiveSignalDetection] = []
        self._subscribers: list = []
        self._lock = threading.RLock()
        self.pass_index = 0
        self.pass_times: list[tuple[int, float]] = []

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------
    def subscribe(self, callback) -> None:
        """Register ``callback(detections)``, called after every change."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def notify(self) -> None:
        items = self.get_recent_detections()
        for callback in list(self._subscribers):
            try:
                callback(items)
            except Exception:
                # A broken listener must never take the acquisition path down.
                pass

    # ------------------------------------------------------------------
    # Sweep passes (occupancy denominator)
    # ------------------------------------------------------------------
    def begin_pass(self) -> int:
        with self._lock:
            self.pass_index += 1
            self.pass_times.append((self.pass_index, time.time()))
            if len(self.pass_times) > self.MAX_PASS_HISTORY:
                drop = len(self.pass_times) - self.MAX_PASS_HISTORY
                cutoff = self.pass_times[drop - 1][0]
                self.pass_times = self.pass_times[drop:]
                for item in self._items:
                    item.seen_passes = {k: v for k, v in item.seen_passes.items()
                                        if k > cutoff}
            return self.pass_index

    def recent_passes(self, count: int | None = None) -> list[int]:
        passes = [p for p, _ in self.pass_times]
        return passes[-count:] if count else passes

    def passes_in_window(self, window_s: float) -> list[int]:
        cutoff = time.time() - window_s
        return [p for p, t in self.pass_times if t >= cutoff]

    # ------------------------------------------------------------------
    # Detections
    # ------------------------------------------------------------------
    def _match(self, freq_hz: float, bandwidth_hz: float):
        tol = max(self.tolerance_hz, bandwidth_hz * 0.6)
        best, best_d = None, tol
        for item in self._items:
            d = abs(item.freq_hz - freq_hz)
            if d <= best_d:
                best, best_d = item, d
        return best

    def add_or_update_detection(self, freq_hz: float, level_dbfs: float,
                                noise_dbfs: float, snr_db: float,
                                bandwidth_hz: float, source: str = "",
                                gain_db=None,
                                notify: bool = False) -> tuple[LiveSignalDetection, bool]:
        """Add a detection or refresh an existing one. Returns (item, is_new)."""
        now = time.time()
        signal_class, confidence = classify(freq_hz, bandwidth_hz)
        with self._lock:
            found = self._match(freq_hz, bandwidth_hz)
            if found is None:
                det = LiveSignalDetection(
                    freq_hz=freq_hz, level_dbfs=level_dbfs, noise_dbfs=noise_dbfs,
                    snr_db=snr_db, bandwidth_hz=bandwidth_hz,
                    signal_class=signal_class, confidence=confidence,
                    source=source, first_seen=now, last_seen=now)
                if gain_db is not None:
                    det.gain_db = gain_db
                det.seen_passes[self.pass_index] = level_dbfs
                det.add_history(now, level_dbfs, snr_db)
                self._items.append(det)
                if notify:
                    self.notify()
                return det, True

            # A sweep can see the same carrier more than once in a single
            # pass, where segments overlap. That is one observation, not
            # several: counting each would inflate the hit count that gates
            # confirmation, and overwrite the pass level with whichever
            # sighting happened to come last. The strongest one wins.
            same_pass = self.pass_index in found.seen_passes
            if same_pass and level_dbfs <= found.seen_passes[self.pass_index]:
                return found, False

            if not same_pass:
                found.hits += 1
            found.seen_passes[self.pass_index] = level_dbfs

            # Track the strongest sighting's frequency, which is the best
            # estimate of the true centre.
            if level_dbfs > found.peak_level_dbfs:
                found.peak_level_dbfs = level_dbfs
                found.freq_hz = freq_hz
            found.level_dbfs = level_dbfs
            found.noise_dbfs = noise_dbfs
            found.snr_db = snr_db
            found.bandwidth_hz = 0.7 * found.bandwidth_hz + 0.3 * bandwidth_hz
            found.signal_class, found.confidence = classify(found.freq_hz,
                                                            found.bandwidth_hz)
            if source:
                found.source = source
            if gain_db is not None:
                found.gain_db = gain_db
            found.last_seen = now
            found.add_history(now, level_dbfs, snr_db)
        if notify:
            self.notify()
        return found, False

    # Backwards-compatible alias used by the existing sweep tabs.
    def update(self, freq_hz, level_dbfs, noise_dbfs, snr_db, bandwidth_hz,
               source: str = "", gain_db=None):
        return self.add_or_update_detection(freq_hz, level_dbfs, noise_dbfs,
                                            snr_db, bandwidth_hz, source, gain_db)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._items)

    def items(self) -> list[LiveSignalDetection]:
        return list(self._items)

    def get_recent_detections(self) -> list[LiveSignalDetection]:
        return list(self._items)

    def get_active_detections(self, timeout_s: float | None = None
                              ) -> list[LiveSignalDetection]:
        t = self.active_timeout_s if timeout_s is None else timeout_s
        return [d for d in self._items if d.is_active(t)]

    def confirmed_items(self, min_hits: int = MIN_HITS_CONFIRMED
                        ) -> list[LiveSignalDetection]:
        """Detections seen at least ``min_hits`` times.

        A single bin briefly poking above the threshold is nearly always a
        noise excursion. Requiring a repeat keeps the list readable without
        hiding real intermittent traffic, which returns on the same frequency.
        """
        return [d for d in self._items if d.hits >= min_hits]

    def sorted_items(self) -> list[LiveSignalDetection]:
        return sorted(self._items, key=lambda d: d.freq_hz)

    def active_count(self, min_hits: int = 1) -> int:
        return sum(1 for d in self._items
                   if d.hits >= min_hits and d.is_active(self.active_timeout_s))

    def by_class(self, signal_class: str) -> list[LiveSignalDetection]:
        return [d for d in self._items if d.signal_class == signal_class]

    def classes_present(self) -> list[str]:
        seen = []
        for d in self._items:
            if d.signal_class not in seen:
                seen.append(d.signal_class)
        return seen

    def strongest(self) -> LiveSignalDetection | None:
        active = self.get_active_detections()
        return max(active, key=lambda d: d.snr_db) if active else None

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------
    def clear_stale_detections(self, max_age_s: float = DEFAULT_STALE_TIMEOUT_S,
                               notify: bool = False) -> int:
        cutoff = time.time() - max_age_s
        with self._lock:
            before = len(self._items)
            self._items = [d for d in self._items if d.last_seen >= cutoff]
            removed = before - len(self._items)
        if removed and notify:
            self.notify()
        return removed

    # Older name kept so existing calls continue to work.
    def prune(self, max_age_s: float = DEFAULT_STALE_TIMEOUT_S) -> int:
        return self.clear_stale_detections(max_age_s)

    def clear(self, notify: bool = False) -> None:
        with self._lock:
            self._items.clear()
            self.pass_times.clear()
        if notify:
            self.notify()
