"""Signal processing helpers: PSD estimation, noise floor, carrier detection."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:  # scipy is optional at runtime; numpy fallbacks are used if absent
    from scipy import signal as _sp_signal
except Exception:  # pragma: no cover
    _sp_signal = None

_WINDOW_CACHE: dict[int, np.ndarray] = {}


def window(n: int) -> np.ndarray:
    """Blackman-Harris window (low sidelobes -> honest dynamic range)."""
    w = _WINDOW_CACHE.get(n)
    if w is None:
        if _sp_signal is not None:
            w = _sp_signal.windows.blackmanharris(n).astype(np.float64)
        else:  # pragma: no cover
            w = np.blackman(n).astype(np.float64)
        _WINDOW_CACHE[n] = w
    return w


def psd_dbfs(iq: np.ndarray, fft_size: int, max_segments: int = 8,
             combine: str = "mean") -> np.ndarray:
    """Periodogram in dBFS, fft-shifted (index 0 = lowest frequency).

    0 dBFS corresponds to a full-scale complex sinusoid (|I|,|Q| ~ 1.0).

    `combine` decides what happens across the segments of one block:

    "mean"  averages them, which is right for a carrier that is simply there -
            it settles the noise down and the measurement with it.

    "max"   keeps the loudest value each bin reached in any segment. A TDMA
            transmitter is only on for a quarter of the time, so averaging
            spreads its power across three parts silence and buries it. Keeping
            the peak measures the burst as it actually was. The noise floor
            rises too, by about 10*log10 of the segment count's harmonic sum,
            but that costs a few dB where averaging costs the signal entirely.
    """
    n = len(iq)
    if n < fft_size:
        fft_size = max(64, 1 << int(np.floor(np.log2(max(n, 64)))))
    nseg = int(min(max_segments, n // fft_size))
    nseg = max(nseg, 1)
    w = window(fft_size)
    wsum = w.sum()
    peak = combine == "max"
    acc = np.zeros(fft_size, dtype=np.float64)
    used = 0
    for i in range(nseg):
        seg = iq[i * fft_size:(i + 1) * fft_size]
        if len(seg) < fft_size:
            break
        seg = seg - seg.mean()          # remove the RTL-SDR DC offset / centre spike
        spec = np.fft.fft(seg * w) / wsum
        power = spec.real ** 2 + spec.imag ** 2
        if peak:
            np.maximum(acc, power, out=acc)
        else:
            acc += power
        used += 1
    if not peak:
        acc /= max(1, used)
    return np.fft.fftshift(10.0 * np.log10(acc + 1e-20))


def freq_axis(center_hz: float, sample_rate: float, fft_size: int) -> np.ndarray:
    return np.fft.fftshift(np.fft.fftfreq(fft_size, d=1.0 / sample_rate)) + center_hz


# Percentile of the bins taken as the noise floor. The 30th percentile is fine
# for a quiet band but sits *inside* the signals on a busy one - the FM
# broadcast band is roughly 70% occupied, which lifted the estimate by 3 dB and
# hid every station's skirts. The 15th is closer to true noise there and barely
# differs on an empty band.
NOISE_PERCENTILE = 15.0


def noise_floor_db(power_db: np.ndarray, percentile: float = NOISE_PERCENTILE) -> float:
    """Robust noise-floor estimate from the lower part of the distribution."""
    if power_db.size == 0:
        return float("nan")
    return float(np.percentile(power_db, percentile))


def smooth_db(power_db: np.ndarray, span_bins: int) -> np.ndarray:
    """Moving average over the spectrum, for detection only.

    A modulated carrier's spectrum flickers: within one averaged frame an FM
    station has deep momentary nulls, and a plain contiguous-bin search shatters
    it into dozens of slivers. Smoothing before grouping fills the nulls without
    moving the carrier. Levels are always read from the unsmoothed spectrum.
    """
    if span_bins < 2 or power_db.size < span_bins:
        return power_db
    kernel = np.ones(int(span_bins)) / float(int(span_bins))
    return np.convolve(power_db, kernel, mode="same")


def total_power_dbfs(iq: np.ndarray) -> float:
    if iq.size == 0:
        return float("nan")
    p = float(np.mean(iq.real.astype(np.float64) ** 2 + iq.imag.astype(np.float64) ** 2))
    return 10.0 * np.log10(p + 1e-20)


def band_power_dbfs(freqs: np.ndarray, power_db: np.ndarray,
                    center_hz: float, bandwidth_hz: float) -> float:
    """Integrated power inside a bandwidth around center_hz, in dBFS."""
    lo, hi = center_hz - bandwidth_hz / 2.0, center_hz + bandwidth_hz / 2.0
    mask = (freqs >= lo) & (freqs <= hi)
    if not mask.any():
        idx = int(np.argmin(np.abs(freqs - center_hz)))
        mask = np.zeros_like(freqs, dtype=bool)
        mask[idx] = True
    lin = 10.0 ** (power_db[mask] / 10.0)
    return float(10.0 * np.log10(lin.sum() + 1e-20))


@dataclass
class RawPeak:
    freq_hz: float
    level_dbfs: float
    bandwidth_hz: float
    snr_db: float
    noise_dbfs: float


def find_carriers(freqs: np.ndarray, power_db: np.ndarray,
                  snr_threshold_db: float = 8.0,
                  min_bandwidth_hz: float = 4000.0,
                  max_carriers: int = 60,
                  dc_guard_hz: float = 0.0,
                  dc_center_hz: float | None = None,
                  smoothing_hz: float = 0.0,
                  gap_hz: float = 0.0) -> list[RawPeak]:
    """Group contiguous bins above (noise floor + threshold) into carriers.

    ``smoothing_hz`` and ``gap_hz`` must suit the channel plan being scanned:
    wide for FM broadcast, narrow for 25 kHz channels, or adjacent channels
    will be merged into one detection.
    """
    if power_db.size < 8:
        return []
    nf = noise_floor_db(power_db)
    if not np.isfinite(nf):
        return []
    bin_hz = float(abs(freqs[1] - freqs[0])) if freqs.size > 1 else 1.0
    thr = nf + snr_threshold_db

    # Detect on a smoothed copy; measure on the real one.
    span_bins = int(round(smoothing_hz / bin_hz)) if smoothing_hz > 0 else 0
    detect_db = smooth_db(power_db, span_bins) if span_bins >= 2 else power_db
    above = detect_db > thr

    if dc_center_hz is not None and dc_guard_hz > 0:
        guard = np.abs(freqs - dc_center_hz) < (dc_guard_hz / 2.0)
        above = above & ~guard

    gap_tolerance = max(2, int(round(gap_hz / bin_hz))) if gap_hz > 0 else 2
    # The group must genuinely be at least min_bandwidth_hz wide. Testing a
    # quarter of it let 2.5 kHz spurs through a 10 kHz channel filter and
    # flooded the list with narrow Unknown hits.
    min_bins = max(1, int(round(min_bandwidth_hz / bin_hz)))

    peaks: list[RawPeak] = []
    idx = 0
    n = above.size
    while idx < n:
        if not above[idx]:
            idx += 1
            continue
        start = idx
        end = idx
        gap = 0
        j = idx + 1
        while j < n:
            if above[j]:
                end = j
                gap = 0
            else:
                gap += 1
                if gap > gap_tolerance:
                    break
            j += 1
        idx = j
        width_bins = end - start + 1
        if width_bins < min_bins:
            continue
        seg = power_db[start:end + 1]
        k = int(np.argmax(seg))
        peak_level = float(seg[k])
        peak_freq = float(freqs[start + k])

        # Occupied bandwidth: how far the signal stays within 20 dB of its own
        # peak and above the threshold. Measured on the smoothed spectrum, so
        # a momentary modulation null inside the channel does not truncate it;
        # the level above is still read from the unsmoothed data.
        smoothed_peak = float(detect_db[start + k])
        floor = max(thr, smoothed_peak - 20.0)
        lo = start + k
        while lo > start and detect_db[lo - 1] > floor:
            lo -= 1
        hi = start + k
        while hi < end and detect_db[hi + 1] > floor:
            hi += 1
        bw = max(hi - lo + 1, 1) * bin_hz
        peaks.append(RawPeak(peak_freq, peak_level, float(bw),
                             float(peak_level - nf), float(nf)))
    peaks.sort(key=lambda p: p.level_dbfs, reverse=True)
    peaks = _drop_skirts(peaks, bin_hz)
    return peaks[:max_carriers]


def _drop_skirts(peaks: list[RawPeak], bin_hz: float) -> list[RawPeak]:
    """Remove weak fragments sitting on the shoulders of a strong carrier.

    A wide signal (an FM station, say) dips below the threshold here and there,
    which splits it into a main peak plus small fragments on either side. Those
    are not separate transmitters. A fragment is dropped when it is close to a
    much stronger peak - close being scaled by that peak's own width, so
    genuinely adjacent channels (25 kHz apart, each ~20 kHz wide) survive.
    """
    kept: list[RawPeak] = []
    for p in peaks:                       # already sorted strongest first
        shadowed = False
        for strong in kept:
            reach = 0.75 * strong.bandwidth_hz + 2 * bin_hz
            if (abs(p.freq_hz - strong.freq_hz) <= reach
                    and strong.level_dbfs - p.level_dbfs >= 6.0):
                shadowed = True
                break
        if not shadowed:
            kept.append(p)
    return kept


def snap_to_raster(freq_hz: float, raster_hz: float) -> float:
    if raster_hz <= 0:
        return freq_hz
    return round(freq_hz / raster_hz) * raster_hz


def stream_health(iq: np.ndarray) -> dict:
    """Sanity checks on a raw IQ block - catches dead / stuck / clipped streams."""
    out = {"samples": int(iq.size)}
    if iq.size == 0:
        out.update(all_zero=True, stuck=True, std=0.0, dc_offset=0.0,
                   clipping_pct=0.0, power_dbfs=float("nan"))
        return out
    i = iq.real.astype(np.float64)
    q = iq.imag.astype(np.float64)
    out["std"] = float(np.sqrt(np.var(i) + np.var(q)))
    out["dc_offset"] = float(np.hypot(i.mean(), q.mean()))
    out["all_zero"] = bool(np.all(i == 0) and np.all(q == 0))
    out["stuck"] = bool(out["std"] < 1e-6)
    clip = np.count_nonzero((np.abs(i) >= 0.995) | (np.abs(q) >= 0.995))
    out["clipping_pct"] = float(100.0 * clip / iq.size)
    out["power_dbfs"] = total_power_dbfs(iq)
    uniq = np.unique(np.round(i[: min(4096, i.size)] * 127.5).astype(np.int16))
    out["unique_levels"] = int(uniq.size)
    return out
