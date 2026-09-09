"""Catching a terminal that only transmits a quarter of the time.

TETRA is TDMA: a frame lasts 56.7 ms and a terminal occupies one slot of four,
so it radiates for about 14 ms and is silent for the other 42. A sweep step
shorter than a frame can land in that silence and measure nothing, no matter
how close the transmitter is - which is how a radio a few metres away shows up
as an empty band.

This checks that a step is long enough to contain a whole frame, whatever the
phase it happens to start on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy import signal as scisig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rtlsdr_diag.config import TETRA_DUPLEX_SPACING_HZ  # noqa: E402
from rtlsdr_diag.core.detector import (MODE_SPECS, TETRA_MAST,  # noqa: E402
                                       TETRA_MOBILE)
from rtlsdr_diag.sdr.dsp import (find_carriers, freq_axis,  # noqa: E402
                                 noise_floor_db, psd_dbfs)

FRAME_S = 1.0 / 17.65          # one TETRA frame
SLOT_DUTY = 0.25               # one timeslot in four
FFT = 4096

failures: list[str] = []


def check(name, ok, detail=""):
    print("%-58s %s%s" % (name, "OK" if ok else "FAIL",
                          ("  " + detail) if detail else ""), flush=True)
    if not ok:
        failures.append(name)


BAUD = 18_000.0                # TETRA symbol rate


def burst_iq(sample_rate, duration_s, phase_s, offset_hz=300e3,
             amplitude=0.02, seed=3):
    """A TETRA-shaped burst: pi/4-DQPSK, on for one timeslot in four.

    Shaped like the real thing rather than a bare tone, because a tone has
    almost no bandwidth and would not tell you whether the detector measures a
    25 kHz channel correctly.
    """
    n = int(sample_rate * duration_s)
    t = np.arange(n) / sample_rate + phase_s
    sps = sample_rate / BAUD
    rng = np.random.default_rng(seed)
    symbols = rng.choice([np.pi / 4, -np.pi / 4, 3 * np.pi / 4, -3 * np.pi / 4],
                         int(n / sps) + 4)
    constellation = np.exp(1j * np.cumsum(symbols))
    index = np.minimum((np.arange(n) / sps).astype(int), constellation.size - 1)
    taps = scisig.firwin(int(6 * sps) | 1, BAUD / 2 * 1.35, fs=sample_rate)
    baseband = scisig.lfilter(taps, 1.0, constellation[index])
    on = ((t / FRAME_S) % 1.0) < SLOT_DUTY
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.0005
    return amplitude * on * baseband * np.exp(2j * np.pi * offset_hz * t) + noise


def best_snr(iq, sample_rate, combine):
    segments = max(1, len(iq) // FFT)
    power = psd_dbfs(iq, FFT, max_segments=segments, combine=combine)
    return float(power.max() - noise_floor_db(power))


spec = MODE_SPECS[TETRA_MOBILE]
rate = spec.sample_rate
threshold = spec.snr_threshold_db
PHASES = [i * FRAME_S / 8.0 for i in range(8)]

print("--- the band that is actually searched ---")
check("the uplink half is scanned end to end",
      spec.start_hz == 380.0e6 and spec.stop_hz == 390.0e6,
      spec.range_text())
mast = MODE_SPECS[TETRA_MAST]
check("every mast carrier has its terminals inside that window",
      spec.start_hz <= mast.start_hz - TETRA_DUPLEX_SPACING_HZ
      and mast.stop_hz - TETRA_DUPLEX_SPACING_HZ <= spec.stop_hz,
      "%s paired with %s" % (spec.range_text(), mast.range_text()))

print("")
print("--- a step short enough to miss the burst ---")
short = [best_snr(burst_iq(rate, 0.016, p), rate, "mean") for p in PHASES]
missed = sum(1 for v in short if v < threshold)
check("a 16 ms step loses a close transmitter at some phases", missed > 0,
      "%d of %d phases below %.0f dB (worst %.1f)"
      % (missed, len(PHASES), threshold, min(short)))

print("")
print("--- the step this program actually uses ---")
check("the mobile band dwells for at least one whole frame",
      spec.dwell_s >= FRAME_S, "%.0f ms vs a %.1f ms frame"
      % (spec.dwell_s * 1e3, FRAME_S * 1e3))
check("and keeps each bin's loudest segment", spec.psd_combine == "max",
      spec.psd_combine)

long = [best_snr(burst_iq(rate, spec.dwell_s, p), rate, spec.psd_combine)
        for p in PHASES]
check("it finds the transmitter at every phase of the frame",
      all(v >= threshold for v in long),
      "worst %.1f dB, threshold %.0f" % (min(long), threshold))
check("and the reading barely moves with timing",
      max(long) - min(long) < 6.0,
      "spread %.1f dB (16 ms step spread %.1f dB)"
      % (max(long) - min(long), max(short) - min(short)))

print("")
print("--- it is really detected, not just visible ---")
iq = burst_iq(rate, spec.dwell_s, FRAME_S * 3.0 / 8.0)   # a phase that failed before
segments = max(1, len(iq) // FFT)
power = psd_dbfs(iq, FFT, max_segments=segments, combine=spec.psd_combine)
freqs = freq_axis(383.0e6, rate, power.size)
peaks = find_carriers(freqs, power, snr_threshold_db=threshold,
                      min_bandwidth_hz=spec.min_bandwidth_hz,
                      smoothing_hz=spec.smoothing_hz, gap_hz=spec.gap_hz)
check("the detector returns the carrier", len(peaks) >= 1, "%d peaks" % len(peaks))
if peaks:
    strongest = max(peaks, key=lambda p: p.snr_db)
    check("at about where it was transmitted",
          abs(strongest.freq_hz - (383.0e6 + 300e3)) < 30e3,
          "%.4f MHz" % (strongest.freq_hz / 1e6))
    check("measured at the width of a TETRA channel",
          15e3 <= strongest.bandwidth_hz <= 40e3,
          "%.1f kHz (the raster is 25 kHz)"
          % (strongest.bandwidth_hz / 1e3))

print("")
print("--- silence stays silent ---")
rng = np.random.default_rng(7)
n = int(rate * spec.dwell_s)
quiet = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.0005
power = psd_dbfs(quiet, FFT, max_segments=max(1, n // FFT),
                 combine=spec.psd_combine)
freqs = freq_axis(383.0e6, rate, power.size)
peaks = find_carriers(freqs, power, snr_threshold_db=threshold,
                      min_bandwidth_hz=spec.min_bandwidth_hz,
                      smoothing_hz=spec.smoothing_hz, gap_hz=spec.gap_hz)
check("keeping the loudest segment does not invent carriers",
      len(peaks) == 0, "%d peaks in noise" % len(peaks))

print("")
if failures:
    print("UPLINK CATCH TESTS FAILED (%d):" % len(failures))
    for f in failures:
        print("   - %s" % f)
    sys.exit(1)
print("ALL UPLINK CATCH TESTS PASSED")
