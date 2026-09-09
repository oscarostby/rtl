"""Backwards-compatible names for the live-detection model and store.

The real definitions now live in :mod:`models` and :mod:`signal_store`; this
module keeps the older import paths working so the tables, the activity
timeline and the sweep tabs did not all have to change at once.
"""
from __future__ import annotations

from .models import LiveSignalDetection
from .signal_store import (DEFAULT_ACTIVE_TIMEOUT_S, DEFAULT_STALE_TIMEOUT_S,
                           DEFAULT_TOLERANCE_HZ, MIN_HITS_CONFIRMED,
                           SignalStore)

# Older names.
Detection = LiveSignalDetection
DetectionTracker = SignalStore

__all__ = ["Detection", "DetectionTracker", "LiveSignalDetection", "SignalStore",
           "DEFAULT_TOLERANCE_HZ", "DEFAULT_ACTIVE_TIMEOUT_S",
           "DEFAULT_STALE_TIMEOUT_S", "MIN_HITS_CONFIRMED"]
