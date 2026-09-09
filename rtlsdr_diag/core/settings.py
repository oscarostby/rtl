"""Persisted user settings, backed by QSettings.

Only preferences live here - things the operator chose and would be annoyed to
set again, such as the radar range or their position. Measurements are never
stored here; they go to CSV.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings

ORG = "rtlsdr-diag"
APP = "RTL-SDR V4 Diagnostic"


def _settings() -> QSettings:
    return QSettings(ORG, APP)


def get(key: str, default=None, kind=None):
    s = _settings()
    value = s.value(key, default)
    if value is None:
        return default
    if kind is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if kind is int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
    if kind is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    return value


def put(key: str, value) -> None:
    s = _settings()
    s.setValue(key, value)
    s.sync()


# --- typed helpers used across the UI --------------------------------------
def radar_range_km(default: float = 10.0) -> float:
    return get("radar/range_km", default, float)


def set_radar_range_km(km: float) -> None:
    put("radar/range_km", float(km))


def observer_position(default=(59.9139, 10.7522)) -> tuple[float, float]:
    return (get("observer/lat", default[0], float),
            get("observer/lon", default[1], float))


def set_observer_position(lat: float, lon: float) -> None:
    put("observer/lat", float(lat))
    put("observer/lon", float(lon))


def ppm_for_device(serial: str, default: int = 0) -> int:
    """PPM correction is a property of the individual dongle's crystal."""
    return get("ppm/%s" % (serial or "default"), default, int)


def set_ppm_for_device(serial: str, ppm: int) -> None:
    put("ppm/%s" % (serial or "default"), int(ppm))


def frequency_match_tolerance_hz(default: float = 25_000.0) -> float:
    return get("radar/match_tolerance_hz", default, float)


def set_frequency_match_tolerance_hz(hz: float) -> None:
    put("radar/match_tolerance_hz", float(hz))
