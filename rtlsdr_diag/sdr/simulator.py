"""Simulated RTL-SDR source so the whole GUI can be exercised without hardware.

The simulator synthesises IQ in the frequency domain: a noise floor plus a
catalogue of band-limited carriers whose levels roll off with distance from the
tuned centre. It is a *plausibility* model for UI testing, not an RF propagation
model, and it never claims to be real hardware.
"""
from __future__ import annotations

import time

import numpy as np

from .device import SdrSource


class Carrier:
    __slots__ = ("freq", "level_db", "bw", "duty", "phase")

    def __init__(self, freq: float, level_db: float, bw: float, duty: float = 1.0):
        self.freq = freq
        self.level_db = level_db
        self.bw = bw
        self.duty = duty          # fraction of time the carrier is on air
        self.phase = np.random.rand() * 100.0


def _build_catalogue() -> list[Carrier]:
    rng = np.random.default_rng(20240908)
    car: list[Carrier] = []

    # FM broadcast - strong, wide, always on
    for f in (87.9, 89.3, 91.1, 93.5, 96.7, 99.3, 100.1, 102.5, 104.3, 106.9):
        car.append(Carrier(f * 1e6, rng.uniform(-32, -18), 180e3, 1.0))

    # Airband AM - narrow and bursty
    for f in (118.1, 119.7, 121.5, 124.3, 127.85, 132.2, 135.6):
        car.append(Carrier(f * 1e6, rng.uniform(-58, -42), 8e3, 0.12))

    # DAB+ Band III - wide OFDM blocks
    for f in (176.640, 185.360, 195.936, 209.936, 222.064):
        car.append(Carrier(f * 1e6, rng.uniform(-38, -26), 1.5e6, 1.0))

    # 2 m amateur - narrow FM, intermittent
    for f in (144.8, 145.325, 145.6, 145.725):
        car.append(Carrier(f * 1e6, rng.uniform(-55, -38), 12.5e3, 0.35))

    # 380-400 MHz band: a 25 kHz raster of continuous downlink-style carriers
    # plus a few intermittent ones. Presence only - no content is modelled.
    for f_mhz, lvl in ((390.0125, -31), (390.4375, -37), (391.2625, -34),
                       (392.0875, -44), (392.9125, -40), (393.5375, -49),
                       (394.7625, -46), (396.1125, -52)):
        car.append(Carrier(f_mhz * 1e6, lvl, 25e3, 1.0))
    for f_mhz in (385.2125, 386.7375, 388.4625):
        car.append(Carrier(f_mhz * 1e6, rng.uniform(-58, -45), 25e3, 0.4))

    # Uplink half of the duplex plan: handsets and vehicle radios. These are
    # bursty by nature - a mobile only transmits while someone is holding the
    # PTT - and weaker, since it is a couple of watts into a small antenna
    # rather than a mast. Modelled as presence only; no content is simulated.
    for f_mhz, lvl in ((380.0125, -52), (381.2625, -58), (383.5375, -55)):
        car.append(Carrier(f_mhz * 1e6, lvl, 25e3, 0.15))

    # 70 cm amateur
    for f in (433.075, 433.5, 434.6, 439.0):
        car.append(Carrier(f * 1e6, rng.uniform(-56, -40), 12.5e3, 0.3))

    return car


# Total in-band noise power of the simulated receiver, in dBFS. The *displayed*
# per-bin noise floor follows from this and the FFT size, as on real hardware.
NOISE_TOTAL_DBFS = -45.0

CATALOGUE = _build_catalogue()


class SimulatedSource(SdrSource):
    """Drop-in replacement for :class:`RtlSdrSource`."""

    is_simulated = True

    # Generate no faster than a real receiver would deliver. Without this the
    # simulator runs as fast as the CPU allows, which makes audio overflow the
    # sound card and makes every rate-dependent reading meaningless.
    realtime = True

    def __init__(self, antenna_connected: bool = True, seed: int | None = None):
        self.antenna_connected = antenna_connected
        self._next_ready = None
        self._open = False
        self._center = 100.0e6
        self._rate = 2.048e6
        self._gain = "auto"
        self._ppm = 0
        self._rng = np.random.default_rng(seed)
        self.last_error = ""

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> None:
        self._open = True
        self._next_ready = None

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    # -- settings ----------------------------------------------------------
    def set_center_freq(self, hz: float) -> None:
        self._center = float(hz)

    def set_sample_rate(self, hz: float) -> None:
        self._rate = float(hz)

    def set_gain(self, gain) -> None:
        self._gain = gain

    def set_ppm(self, ppm: int) -> None:
        self._ppm = int(ppm)

    def valid_gains_db(self) -> list[float]:
        return [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7,
                20.7, 22.9, 25.4, 28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6,
                40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]

    # -- data --------------------------------------------------------------
    def _gain_db(self) -> float:
        if isinstance(self._gain, str):
            return 30.0
        return float(self._gain)

    def read(self, num_samples: int) -> np.ndarray:
        if not self._open:
            from .device import SdrError
            raise SdrError("Simulated device is not open.")
        n = max(1024, int(num_samples))
        n = ((n + 511) // 512) * 512
        fs = self._rate
        rng = self._rng

        # Extra gain above the reference 30 dB lifts signal and noise together.
        gain_offset = (self._gain_db() - 30.0) * 0.4

        # Build the block in the frequency domain. With x = ifft(X),
        # mean(|x|^2) = sum(|X_k|^2) / n^2, so a target total power P over a set
        # of bins with unit-norm shape s is X_k = sqrt(P) * n * s_k.
        # --- noise floor: total in-band power, spread over every bin ---------
        noise_dbfs = NOISE_TOTAL_DBFS + gain_offset + (0.0 if self.antenna_connected else -3.0)
        p_noise = 10.0 ** (noise_dbfs / 10.0)
        sigma_bin = np.sqrt(p_noise * n / 2.0)      # per real/imag component
        spec = (rng.normal(0.0, sigma_bin, n)
                + 1j * rng.normal(0.0, sigma_bin, n))

        # --- carriers inside the tuned window -------------------------------
        if self.antenna_connected:
            lo = self._center - fs / 2.0
            hi = self._center + fs / 2.0
            bin_hz = fs / n
            for c in CATALOGUE:
                if not (lo < c.freq < hi):
                    continue
                if c.duty < 1.0 and rng.random() > c.duty:
                    continue
                k = int(round((c.freq - self._center) / bin_hz))
                half = max(1, int(round((c.bw / 2.0) / bin_hz)))
                lo_i, hi_i = k - half, k + half + 1
                if lo_i <= -n // 2 + 2 or hi_i >= n // 2 - 2:
                    continue                        # too close to the band edge
                idx = np.arange(lo_i, hi_i) % n
                shape = np.hanning(idx.size + 2)[1:-1]
                norm = np.sqrt(float((shape ** 2).sum())) or 1.0
                shape = shape / norm                # unit-norm shape
                amp = 10.0 ** ((c.level_db + gain_offset) / 20.0)
                ph = rng.uniform(0.0, 2 * np.pi, idx.size)
                spec[idx] += amp * n * shape * np.exp(1j * ph)

        iq = np.fft.ifft(spec).astype(np.complex64)

        # Residual DC offset, like a real RTL-SDR.
        iq += np.complex64(complex(0.004, -0.003))
        np.clip(iq.real, -1.0, 1.0, out=iq.real)
        np.clip(iq.imag, -1.0, 1.0, out=iq.imag)

        if self.realtime:
            duration = n / fs
            now = time.perf_counter()
            if self._next_ready is None:
                self._next_ready = now
            self._next_ready += duration
            delay = self._next_ready - now
            if delay > 0:
                time.sleep(min(delay, 1.0))
            elif delay < -0.5:
                self._next_ready = now      # fell far behind; resynchronise
        return iq

    # -- info --------------------------------------------------------------
    def describe(self) -> dict:
        return {
            "device_index": 0,
            "name": "Simulated RTL-SDR (no hardware)",
            "manufacturer": "simulation",
            "product": "Virtual RTL-SDR Blog V4",
            "serial": "SIM0001",
            "tuner": "Simulated R828D",
            "opened": self._open,
            "center_freq_hz": self._center,
            "sample_rate_hz": self._rate,
            "gain_db": self._gain,
            "ppm": self._ppm,
            "last_error": self.last_error,
            "simulated": True,
            "antenna_connected": self.antenna_connected,
        }
