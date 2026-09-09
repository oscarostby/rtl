"""RTL-SDR device discovery and the real hardware source implementation.

Two backends are supported:

* ``native``  - this project's own ctypes binding (:mod:`native`). Preferred,
  because it tolerates librtlsdr builds that export slightly different sets of
  functions.
* ``pyrtlsdr`` - used only if the native library could not be loaded but the
  pyrtlsdr package imports successfully.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import dll_loader, native

dll_loader.prepare()

# --- native backend --------------------------------------------------------
_NATIVE_LIB = native.load()
NATIVE_OK = _NATIVE_LIB is not None

# --- pyrtlsdr backend (fallback / informational) ----------------------------
RTLSDR_IMPORT_ERROR: str | None = None
RtlSdr = None
PYRTLSDR_VERSION = "not installed"

try:
    import rtlsdr as _rtlsdr_pkg
    from rtlsdr import RtlSdr as _RtlSdr

    RtlSdr = _RtlSdr
    PYRTLSDR_VERSION = getattr(_rtlsdr_pkg, "__version__", "unknown")
except Exception as exc:  # pragma: no cover - depends on the machine
    RTLSDR_IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

PYRTLSDR_OK = RtlSdr is not None
BACKEND = "native" if NATIVE_OK else ("pyrtlsdr" if PYRTLSDR_OK else "none")


TUNER_NAMES = {
    0: "Unknown",
    1: "Elonics E4000",
    2: "Fitipower FC0012",
    3: "Fitipower FC0013",
    4: "FCI FC2580",
    5: "Rafael Micro R820T/R820T2",
    6: "Rafael Micro R828D (RTL-SDR Blog V4)",
}


@dataclass
class DeviceInfo:
    index: int
    name: str = "Unknown"
    manufacturer: str = ""
    product: str = ""
    serial: str = ""
    tuner: str = ""
    opened_ok: bool = False
    error: str = ""

    def label(self) -> str:
        bits = ["#%d" % self.index, self.name]
        if self.serial:
            bits.append("SN " + self.serial)
        return "   ".join(b for b in bits if b)


def library_available() -> bool:
    return NATIVE_OK or PYRTLSDR_OK


def library_status() -> str:
    if NATIVE_OK:
        dll = dll_loader.found_dll()
        text = "librtlsdr via built-in ctypes binding"
        if dll:
            text += " (%s)" % dll
        if _NATIVE_LIB is not None and _NATIVE_LIB.missing:
            text += "; optional functions absent: %s" % ", ".join(_NATIVE_LIB.missing)
        return text
    if PYRTLSDR_OK:
        return "pyrtlsdr %s" % PYRTLSDR_VERSION
    parts = []
    if native.load_error():
        parts.append("native: %s" % native.load_error())
    if RTLSDR_IMPORT_ERROR:
        parts.append("pyrtlsdr: %s" % RTLSDR_IMPORT_ERROR)
    return "; ".join(parts) or "librtlsdr not available"


def device_count() -> int:
    if NATIVE_OK:
        return native.device_count()
    if PYRTLSDR_OK:
        try:
            from rtlsdr import librtlsdr
            return int(librtlsdr.rtlsdr_get_device_count())
        except Exception:
            return 0
    return 0


def enumerate_devices() -> list[DeviceInfo]:
    """List every RTL-SDR the driver can see, without keeping them open."""
    if not library_available():
        return []
    devices: list[DeviceInfo] = []
    for idx in range(device_count()):
        info = DeviceInfo(index=idx)
        if NATIVE_OK:
            info.name = native.device_name(idx) or "Unknown"
            info.manufacturer, info.product, info.serial = native.usb_strings(idx)
        else:  # pragma: no cover - only when running on the pyrtlsdr fallback
            try:
                import ctypes

                from rtlsdr import librtlsdr
                raw = librtlsdr.rtlsdr_get_device_name(idx)
                info.name = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                man = ctypes.create_string_buffer(256)
                prod = ctypes.create_string_buffer(256)
                ser = ctypes.create_string_buffer(256)
                librtlsdr.rtlsdr_get_device_usb_strings(idx, man, prod, ser)
                info.manufacturer = man.value.decode("utf-8", "replace").strip()
                info.product = prod.value.decode("utf-8", "replace").strip()
                info.serial = ser.value.decode("utf-8", "replace").strip()
            except Exception as exc:
                info.error = str(exc)
        devices.append(info)
    return devices


def tuner_name(code: int) -> str:
    return TUNER_NAMES.get(int(code), "type %d" % int(code))


def probe_device(index: int = 0) -> DeviceInfo:
    """Open a device briefly to confirm the driver works and read the tuner."""
    infos = enumerate_devices()
    info = next((d for d in infos if d.index == index), DeviceInfo(index=index))
    if not library_available():
        info.error = library_status()
        return info
    if not infos:
        info.error = "No RTL-SDR devices reported by the driver."
        return info
    src = RtlSdrSource(device_index=index)
    try:
        src.open()
        info.opened_ok = True
        info.tuner = src.describe().get("tuner", "")
    except Exception as exc:
        info.error = "%s: %s" % (type(exc).__name__, exc)
    finally:
        src.close()
    return info


class SdrError(RuntimeError):
    pass


class SdrSource:
    """Common interface implemented by the real device and the simulator."""

    is_simulated = False

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def read(self, num_samples: int) -> np.ndarray:
        raise NotImplementedError

    @property
    def is_open(self) -> bool:
        return False

    def set_center_freq(self, hz: float) -> None:
        pass

    def set_sample_rate(self, hz: float) -> None:
        pass

    def set_gain(self, gain) -> None:
        pass

    def set_ppm(self, ppm: int) -> None:
        pass

    def valid_gains_db(self) -> list[float]:
        return []

    def describe(self) -> dict:
        return {}


class RtlSdrSource(SdrSource):
    """Real hardware, through the native binding (or pyrtlsdr as a fallback)."""

    def __init__(self, device_index: int = 0):
        self.device_index = device_index
        self._dev = None            # native.NativeDevice
        self._sdr = None            # pyrtlsdr RtlSdr
        self._info = DeviceInfo(index=device_index)
        self.last_error = ""
        self.backend = BACKEND
        self._center = 100.0e6
        self._rate = 2.048e6
        self._gain = "auto"
        self._ppm = 0

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> None:
        if self.is_open:
            return
        if not library_available():
            raise SdrError(library_status())
        if device_count() == 0:
            raise SdrError("No RTL-SDR device found on USB.")
        try:
            if NATIVE_OK:
                self._dev = native.NativeDevice(self.device_index)
                self._dev.open()
            else:  # pragma: no cover - fallback path
                self._sdr = RtlSdr(device_index=self.device_index)
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self._dev = None
            self._sdr = None
            raise SdrError(
                "Could not open RTL-SDR #%d: %s\n"
                "On Windows this usually means the WinUSB driver has not been "
                "installed with Zadig, or another program is already using the "
                "device." % (self.device_index, exc)) from exc

        infos = enumerate_devices()
        self._info = next((d for d in infos if d.index == self.device_index),
                          DeviceInfo(index=self.device_index))
        self._info.opened_ok = True
        self._info.tuner = self._read_tuner()
        # Re-apply cached settings so reopening restores state.
        self.set_sample_rate(self._rate)
        self.set_ppm(self._ppm)
        self.set_center_freq(self._center)
        self.set_gain(self._gain)

    def _read_tuner(self) -> str:
        try:
            if self._dev is not None:
                return tuner_name(self._dev.get_tuner_type())
            if self._sdr is not None:
                fn = getattr(self._sdr, "get_tuner_type", None)
                if callable(fn):
                    return tuner_name(int(fn()))
        except Exception:
            pass
        return "unavailable"

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception as exc:
                self.last_error = "close failed: %s" % exc
            self._dev = None
        if self._sdr is not None:
            try:
                self._sdr.close()
            except Exception as exc:
                self.last_error = "close failed: %s" % exc
            self._sdr = None

    @property
    def is_open(self) -> bool:
        return (self._dev is not None and self._dev.is_open) or self._sdr is not None

    # -- settings ----------------------------------------------------------
    def set_center_freq(self, hz: float) -> None:
        self._center = float(hz)
        if not self.is_open:
            return
        try:
            if self._dev is not None:
                self._dev.set_center_freq(hz)
            else:
                self._sdr.center_freq = float(hz)
        except Exception as exc:
            self.last_error = "set center_freq %.4f MHz failed: %s" % (hz / 1e6, exc)
            raise SdrError(self.last_error) from exc

    def set_sample_rate(self, hz: float) -> None:
        self._rate = float(hz)
        if not self.is_open:
            return
        try:
            if self._dev is not None:
                self._dev.set_sample_rate(hz)
            else:
                self._sdr.sample_rate = float(hz)
        except Exception as exc:
            self.last_error = "set sample_rate failed: %s" % exc
            raise SdrError(self.last_error) from exc

    def set_gain(self, gain) -> None:
        self._gain = gain
        if not self.is_open:
            return
        auto = isinstance(gain, str) and gain.lower() == "auto"
        try:
            if self._dev is not None:
                self._dev.set_manual_gain(not auto)
                self._dev.set_agc_mode(auto)
                if not auto:
                    self._dev.set_gain_db(float(gain))
            else:
                self._sdr.set_manual_gain_enabled(not auto)
                if not auto:
                    self._sdr.gain = float(gain)
        except Exception as exc:
            self.last_error = "set gain failed: %s" % exc

    def set_ppm(self, ppm: int) -> None:
        self._ppm = int(ppm)
        if not self.is_open:
            return
        try:
            if self._dev is not None:
                self._dev.set_ppm(int(ppm))
            else:
                self._sdr.freq_correction = int(ppm)
        except Exception as exc:
            if "same" not in str(exc).lower():
                self.last_error = "set ppm failed: %s" % exc

    def valid_gains_db(self) -> list[float]:
        try:
            if self._dev is not None:
                return self._dev.valid_gains_db()
            if self._sdr is not None:
                return [float(g) for g in self._sdr.valid_gains_db]
        except Exception:
            pass
        return []

    def set_bias_tee(self, enabled: bool) -> bool:
        if self._dev is not None:
            return self._dev.set_bias_tee(enabled)
        return False

    # -- data --------------------------------------------------------------
    def read(self, num_samples: int) -> np.ndarray:
        if not self.is_open:
            raise SdrError("Device is not open.")
        try:
            if self._dev is not None:
                return self._dev.read_samples(num_samples)
            n = ((max(512, int(num_samples)) + 511) // 512) * 512
            return np.asarray(self._sdr.read_samples(n), dtype=np.complex64)
        except Exception as exc:
            self.last_error = "read failed: %s" % exc
            raise SdrError(
                "Reading samples failed (%s). The device may have been unplugged "
                "or reset." % exc) from exc

    # -- info --------------------------------------------------------------
    def describe(self) -> dict:
        d = {
            "device_index": self.device_index,
            "name": self._info.name,
            "manufacturer": self._info.manufacturer,
            "product": self._info.product,
            "serial": self._info.serial,
            "tuner": self._info.tuner,
            "opened": self.is_open,
            "last_error": self.last_error,
            "simulated": False,
            "backend": self.backend,
        }
        if self._dev is not None and self._dev.is_open:
            d.update(center_freq_hz=self._dev.get_center_freq(),
                     sample_rate_hz=self._dev.get_sample_rate(),
                     gain_db=self._dev.get_gain_db(),
                     ppm=self._dev.get_ppm())
        if self.is_open:
            # The gain table differs between tuners, so a wanted gain has to be
            # snapped to what this one actually offers.
            try:
                d["gains_db"] = self.valid_gains_db()
            except Exception:
                pass
        elif self._sdr is not None:
            for key, attr in (("center_freq_hz", "center_freq"),
                              ("sample_rate_hz", "sample_rate"),
                              ("gain_db", "gain"),
                              ("ppm", "freq_correction")):
                try:
                    d[key] = getattr(self._sdr, attr)
                except Exception:
                    d[key] = None
        else:
            d.update(center_freq_hz=self._center, sample_rate_hz=self._rate,
                     gain_db=self._gain, ppm=self._ppm)
        return d
