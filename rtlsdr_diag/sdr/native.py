"""Direct ctypes binding to librtlsdr.

Why this exists
---------------
pyrtlsdr binds every function it knows about at import time, unconditionally.
If the installed ``rtlsdr.dll`` is missing even one of them the whole package
fails to import - and different librtlsdr builds genuinely do export different
sets. The RTL-SDR Blog V1.4.0 Windows release, for example, does not export
``rtlsdr_set_dithering``, which pyrtlsdr 0.5.0 requires, so a perfectly working
driver and dongle look "not found".

This module binds each function independently and tolerates missing ones, so
the application works with any reasonable librtlsdr build. pyrtlsdr remains
supported as a fallback but is no longer required.
"""
from __future__ import annotations

import ctypes
from ctypes import (POINTER, byref, c_char_p, c_int, c_uint32, c_void_p,
                    create_string_buffer)

import numpy as np

from . import dll_loader

p_dev = c_void_p

# librtlsdr takes the frequency as a uint32, so anything at or above 2^32 Hz
# wraps silently and the receiver ends up somewhere else entirely - asking for
# 5200 MHz lands on 905 MHz. Refuse instead of quietly lying.
UINT32_LIMIT_HZ = 4_294_967_295

# R820T / R820T2 / R828D tuning range. Below this the V4 has a direct-sampling
# HF path; above it there is nothing - the tuner PLL cannot lock.
TUNER_MIN_HZ = 24_000_000
TUNER_MAX_HZ = 1_766_000_000

# (name, restype, argtypes, required)
_SIGNATURES = [
    ("rtlsdr_get_device_count", c_uint32, [], True),
    ("rtlsdr_get_device_name", c_char_p, [c_uint32], True),
    ("rtlsdr_get_device_usb_strings", c_int,
     [c_uint32, c_char_p, c_char_p, c_char_p], True),
    ("rtlsdr_get_index_by_serial", c_int, [c_char_p], False),
    ("rtlsdr_open", c_int, [POINTER(p_dev), c_uint32], True),
    ("rtlsdr_close", c_int, [p_dev], True),
    ("rtlsdr_set_center_freq", c_int, [p_dev, c_uint32], True),
    ("rtlsdr_get_center_freq", c_uint32, [p_dev], True),
    ("rtlsdr_set_sample_rate", c_int, [p_dev, c_uint32], True),
    ("rtlsdr_get_sample_rate", c_uint32, [p_dev], True),
    ("rtlsdr_set_tuner_gain_mode", c_int, [p_dev, c_int], True),
    ("rtlsdr_set_tuner_gain", c_int, [p_dev, c_int], True),
    ("rtlsdr_get_tuner_gain", c_int, [p_dev], True),
    ("rtlsdr_get_tuner_gains", c_int, [p_dev, POINTER(c_int)], True),
    ("rtlsdr_set_freq_correction", c_int, [p_dev, c_int], True),
    ("rtlsdr_get_freq_correction", c_int, [p_dev], True),
    ("rtlsdr_get_tuner_type", c_int, [p_dev], True),
    ("rtlsdr_reset_buffer", c_int, [p_dev], True),
    ("rtlsdr_read_sync", c_int, [p_dev, c_void_p, c_int, POINTER(c_int)], True),
    ("rtlsdr_set_agc_mode", c_int, [p_dev, c_int], False),
    ("rtlsdr_set_bias_tee", c_int, [p_dev, c_int], False),
    ("rtlsdr_set_direct_sampling", c_int, [p_dev, c_int], False),
    ("rtlsdr_set_offset_tuning", c_int, [p_dev, c_int], False),
    ("rtlsdr_set_dithering", c_int, [p_dev, c_int], False),
]


class NativeError(RuntimeError):
    pass


class LibRtlSdr:
    """A loaded librtlsdr, with per-function availability recorded."""

    def __init__(self, path):
        self.path = path
        self.lib = ctypes.CDLL(str(path))
        self.available: set[str] = set()
        self.missing: list[str] = []
        for name, restype, argtypes, required in _SIGNATURES:
            try:
                fn = getattr(self.lib, name)
            except AttributeError:
                self.missing.append(name)
                if required:
                    raise NativeError(
                        "%s does not export %s, which this application needs."
                        % (path, name))
                continue
            fn.restype = restype
            fn.argtypes = argtypes
            self.available.add(name)

    def has(self, name: str) -> bool:
        return name in self.available

    def __getattr__(self, item):
        # Delegate rtlsdr_* lookups to the CDLL.
        if item.startswith("rtlsdr_"):
            return getattr(self.lib, item)
        raise AttributeError(item)


_LIB: LibRtlSdr | None = None
_LOAD_ERROR: str | None = None
_LOADED = False


def load() -> LibRtlSdr | None:
    """Load librtlsdr once. Returns None if it is unavailable."""
    global _LIB, _LOAD_ERROR, _LOADED
    if _LOADED:
        return _LIB
    _LOADED = True
    path = dll_loader.prepare()
    if path is None:
        # Fall back to the plain library name; the OS loader may still find it.
        for candidate in ("rtlsdr", "librtlsdr", "librtlsdr.so.0"):
            try:
                _LIB = LibRtlSdr(candidate)
                return _LIB
            except (OSError, NativeError):
                continue
        _LOAD_ERROR = "rtlsdr.dll / librtlsdr was not found."
        return None
    try:
        _LIB = LibRtlSdr(path)
    except (OSError, NativeError) as exc:
        _LOAD_ERROR = "%s: %s" % (type(exc).__name__, exc)
        _LIB = None
    return _LIB


def load_error() -> str | None:
    return _LOAD_ERROR


def _cstr(buf) -> str:
    return buf.value.decode("utf-8", "replace").strip("\x00").strip()


# ---------------------------------------------------------------------------
# Module-level queries (no open device required)
# ---------------------------------------------------------------------------
def device_count() -> int:
    lib = load()
    if lib is None:
        return 0
    try:
        return int(lib.rtlsdr_get_device_count())
    except Exception:
        return 0


def device_name(index: int) -> str:
    lib = load()
    if lib is None:
        return ""
    try:
        raw = lib.rtlsdr_get_device_name(index)
        return raw.decode("utf-8", "replace") if raw else ""
    except Exception:
        return ""


def usb_strings(index: int) -> tuple[str, str, str]:
    lib = load()
    if lib is None:
        return ("", "", "")
    man = create_string_buffer(256)
    prod = create_string_buffer(256)
    ser = create_string_buffer(256)
    try:
        if lib.rtlsdr_get_device_usb_strings(index, man, prod, ser) != 0:
            return ("", "", "")
    except Exception:
        return ("", "", "")
    return (_cstr(man), _cstr(prod), _cstr(ser))


class NativeDevice:
    """An open RTL-SDR, driven through librtlsdr directly."""

    def __init__(self, index: int = 0):
        self.index = int(index)
        self._dev = p_dev()
        self._open = False
        self._lib = load()
        if self._lib is None:
            raise NativeError(load_error() or "librtlsdr unavailable")

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> None:
        if self._open:
            return
        rc = self._lib.rtlsdr_open(byref(self._dev), c_uint32(self.index))
        if rc != 0 or not self._dev:
            raise NativeError(
                "rtlsdr_open(%d) failed with code %d. The device is either in "
                "use by another program, or the WinUSB driver is not installed "
                "for it (use Zadig)." % (self.index, rc))
        self._open = True
        # Direct sampling (the HF path) is sticky in the RTL2832's registers, so
        # another program - rtl_test -t, for instance - can leave it enabled and
        # the tuner then refuses to lock on VHF/UHF. Always start from off.
        self.set_direct_sampling(0)
        try:
            self._lib.rtlsdr_reset_buffer(self._dev)
        except Exception:
            pass

    def set_direct_sampling(self, mode: int) -> bool:
        """0 = off (normal tuner path), 1 = I input, 2 = Q input."""
        if not self._open or not self._lib.has("rtlsdr_set_direct_sampling"):
            return False
        try:
            return self._lib.rtlsdr_set_direct_sampling(self._dev, c_int(int(mode))) == 0
        except Exception:
            return False

    def close(self) -> None:
        if self._open:
            try:
                self._lib.rtlsdr_close(self._dev)
            except Exception:
                pass
            self._open = False
            self._dev = p_dev()

    @property
    def is_open(self) -> bool:
        return self._open

    def _require(self) -> None:
        if not self._open:
            raise NativeError("Device is not open.")

    # -- settings ----------------------------------------------------------
    def set_center_freq(self, hz: float) -> None:
        self._require()
        hz = float(hz)
        if hz <= 0 or hz > UINT32_LIMIT_HZ:
            raise NativeError(
                "%.1f MHz cannot even be expressed to the driver, which takes a "
                "32-bit frequency in Hz (max %.0f MHz)."
                % (hz / 1e6, UINT32_LIMIT_HZ / 1e6))
        if hz > TUNER_MAX_HZ:
            extra = ""
            if 2_400e6 <= hz <= 2_500e6 or 5_150e6 <= hz <= 5_900e6:
                extra = (" That is a Wi-Fi band; no RTL-SDR can reach it, "
                         "whatever the software. You would need a wideband SDR "
                         "such as a HackRF (1 MHz - 6 GHz).")
            raise NativeError(
                "%.1f MHz is above this tuner's ceiling of about %.0f MHz.%s"
                % (hz / 1e6, TUNER_MAX_HZ / 1e6, extra))
        rc = self._lib.rtlsdr_set_center_freq(self._dev, c_uint32(int(hz)))
        if rc != 0:
            raise NativeError("Setting centre frequency to %.4f MHz failed "
                              "(code %d). The tuner could not lock there."
                              % (hz / 1e6, rc))

    def get_center_freq(self) -> float:
        if not self._open:
            return 0.0
        return float(self._lib.rtlsdr_get_center_freq(self._dev))

    def set_sample_rate(self, hz: float) -> None:
        self._require()
        rc = self._lib.rtlsdr_set_sample_rate(self._dev, c_uint32(int(hz)))
        if rc != 0:
            raise NativeError("Setting sample rate to %.4f MS/s failed "
                              "(code %d)." % (hz / 1e6, rc))

    def get_sample_rate(self) -> float:
        if not self._open:
            return 0.0
        return float(self._lib.rtlsdr_get_sample_rate(self._dev))

    def set_manual_gain(self, enabled: bool) -> None:
        self._require()
        self._lib.rtlsdr_set_tuner_gain_mode(self._dev, c_int(1 if enabled else 0))

    def set_gain_db(self, db: float) -> None:
        self._require()
        self._lib.rtlsdr_set_tuner_gain(self._dev, c_int(int(round(db * 10))))

    def get_gain_db(self) -> float:
        if not self._open:
            return 0.0
        return self._lib.rtlsdr_get_tuner_gain(self._dev) / 10.0

    def set_agc_mode(self, enabled: bool) -> None:
        """RTL2832 digital AGC - separate from the tuner's own gain mode."""
        if not self._open or not self._lib.has("rtlsdr_set_agc_mode"):
            return
        try:
            self._lib.rtlsdr_set_agc_mode(self._dev, c_int(1 if enabled else 0))
        except Exception:
            pass

    def valid_gains_db(self) -> list[float]:
        if not self._open:
            return []
        try:
            count = self._lib.rtlsdr_get_tuner_gains(self._dev, None)
            if count <= 0:
                return []
            buf = (c_int * count)()
            self._lib.rtlsdr_get_tuner_gains(self._dev, buf)
            return [g / 10.0 for g in buf]
        except Exception:
            return []

    def set_ppm(self, ppm: int) -> None:
        self._require()
        rc = self._lib.rtlsdr_set_freq_correction(self._dev, c_int(int(ppm)))
        # -2 means "already set to this value", which is not an error.
        if rc not in (0, -2):
            raise NativeError("Setting PPM correction failed (code %d)." % rc)

    def get_ppm(self) -> int:
        if not self._open:
            return 0
        return int(self._lib.rtlsdr_get_freq_correction(self._dev))

    def get_tuner_type(self) -> int:
        if not self._open:
            return 0
        try:
            return int(self._lib.rtlsdr_get_tuner_type(self._dev))
        except Exception:
            return 0

    def set_bias_tee(self, enabled: bool) -> bool:
        if not self._open or not self._lib.has("rtlsdr_set_bias_tee"):
            return False
        try:
            return self._lib.rtlsdr_set_bias_tee(
                self._dev, c_int(1 if enabled else 0)) == 0
        except Exception:
            return False

    def reset_buffer(self) -> None:
        if self._open:
            try:
                self._lib.rtlsdr_reset_buffer(self._dev)
            except Exception:
                pass

    # -- data --------------------------------------------------------------
    def read_samples(self, num_samples: int) -> np.ndarray:
        """Read complex samples, scaled to roughly [-1, 1] like pyrtlsdr does."""
        self._require()
        n = max(512, int(num_samples))
        n = ((n + 255) // 256) * 256          # keep the byte count a multiple of 512
        n_bytes = n * 2                       # interleaved 8-bit I and Q
        buf = (ctypes.c_ubyte * n_bytes)()
        got = c_int(0)
        rc = self._lib.rtlsdr_read_sync(self._dev, buf, c_int(n_bytes), byref(got))
        if rc != 0:
            raise NativeError(
                "rtlsdr_read_sync failed (code %d). The device may have been "
                "unplugged or reset." % rc)
        count = int(got.value)
        if count <= 0:
            raise NativeError("The device returned no samples.")
        raw = np.frombuffer(buf, dtype=np.uint8, count=count)
        if raw.size % 2:
            raw = raw[:-1]
        scaled = (raw.astype(np.float32) - 127.5) / 127.5
        return (scaled[0::2] + 1j * scaled[1::2]).astype(np.complex64)
