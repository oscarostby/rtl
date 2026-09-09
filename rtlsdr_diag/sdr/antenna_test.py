"""Heuristic "is an antenna connected?" check.

IMPORTANT LIMITATION
--------------------
An RTL-SDR is a receive-only device with no directional coupler and no
transmitter. It therefore CANNOT measure SWR, return loss or antenna impedance.
This test only compares how *structured* the received spectrum looks across a
few busy bands: a connected antenna picks up strong broadcast carriers, while a
bare or disconnected input shows an almost featureless noise floor. It is a
plausibility check, not an antenna measurement.
"""
from __future__ import annotations

import time

import numpy as np

from . import dsp
from .device import SdrError

# (label, centre frequency, expected content)
#
# Deliberately spread across VHF and UHF. FM broadcast alone is not a reliable
# probe: Norway switched off national FM in 2017, and several other countries
# have done the same or are close to it, so an empty FM band says nothing about
# the antenna. The 433 MHz ISM band is the most dependable single indicator -
# almost every home has sensors, remotes or weather stations transmitting there.
PROBE_POINTS = [
    ("FM broadcast", 98.0e6,
     "wide-FM carriers - but note that some countries, Norway included, have "
     "switched off national FM, so an empty band here proves nothing"),
    ("DAB+ Band III", 222.0e6, "wideband digital blocks, strong across Europe"),
    ("2 m amateur / VHF", 145.0e6, "narrowband FM repeaters and beacons"),
    ("380-400 MHz band", 390.0e6, "narrowband carriers, location dependent"),
    ("433 MHz ISM", 433.9e6,
     "sensors, remotes and weather stations - usually the easiest signals to "
     "hear indoors"),
]

SAMPLE_RATE = 2.4e6
BLOCK = 131_072
FFT = 4096


def run_antenna_test(engine, progress=lambda msg: None) -> dict:
    t_start = time.time()
    steps: list[dict] = []
    src = engine._test_source()
    if src is None or not src.is_open:
        return {
            "ok": False,
            "verdict": "Unable to determine",
            "confidence": "none",
            "steps": steps,
            "metrics": {},
            "summary": "The receiver could not be opened, so nothing could be measured.",
            "duration_s": round(time.time() - t_start, 2),
        }

    try:
        src.set_sample_rate(SAMPLE_RATE)
        # A fixed, fairly high gain rather than AGC: automatic gain chases the
        # signal and lifts the noise floor with it, which flattens exactly the
        # peak-to-noise contrast this test is trying to measure.
        gains = [g for g in src.valid_gains_db() if g <= 45.0]
        src.set_gain(max(gains) if gains else 40.0)
    except SdrError as exc:
        return {
            "ok": False, "verdict": "Unable to determine", "confidence": "none",
            "steps": steps, "metrics": {},
            "summary": "Receiver rejected its settings: %s" % exc,
            "duration_s": round(time.time() - t_start, 2),
        }

    results = []
    for label, freq, expectation in PROBE_POINTS:
        progress("Measuring %s at %.1f MHz..." % (label, freq / 1e6))
        try:
            src.set_center_freq(freq)
            src.read(8192)                       # discard the retune transient
            iq = src.read(BLOCK)
        except SdrError as exc:
            steps.append({"name": "%s @ %.1f MHz" % (label, freq / 1e6),
                          "ok": False, "detail": str(exc)})
            continue
        power = dsp.psd_dbfs(iq, FFT, max_segments=16)
        freqs = dsp.freq_axis(freq, SAMPLE_RATE, power.size)
        nf = dsp.noise_floor_db(power)
        peak = float(power.max())
        ptm = peak - nf                                    # peak-to-median ratio
        spread = float(np.std(power))
        peaks = dsp.find_carriers(freqs, power, 10.0, 6000.0,
                                  dc_guard_hz=SAMPLE_RATE * 0.004,
                                  dc_center_hz=freq)
        results.append({
            "label": label, "freq_hz": freq, "noise_dbfs": nf, "peak_dbfs": peak,
            "peak_to_noise_db": ptm, "spread_db": spread, "carriers": len(peaks),
        })
        steps.append({
            "name": "%s @ %.1f MHz" % (label, freq / 1e6),
            "ok": True,
            "detail": "noise floor %.1f dBFS, peak %.1f dBFS, peak-to-noise %.1f dB, "
                      "%d carrier(s). Expected: %s"
                      % (nf, peak, ptm, len(peaks), expectation),
        })

    if not results:
        return {
            "ok": False, "verdict": "Unable to determine", "confidence": "none",
            "steps": steps, "metrics": {},
            "summary": "No measurements completed.",
            "duration_s": round(time.time() - t_start, 2),
        }

    best = max(results, key=lambda r: r["peak_to_noise_db"])
    best_ptm = best["peak_to_noise_db"]
    total_carriers = sum(r["carriers"] for r in results)
    best_spread = max(r["spread_db"] for r in results)
    # How many separate bands show real structure? One band alone can be a
    # single very strong local transmitter; several bands means the input is
    # genuinely wideband-connected.
    live_bands = [r for r in results if r["peak_to_noise_db"] >= 12.0]

    metrics = {
        "best_peak_to_noise_db": round(best_ptm, 1),
        "best_band": best["label"],
        "best_band_hz": best["freq_hz"],
        "live_bands": len(live_bands),
        "total_carriers": total_carriers,
        "max_spectral_spread_db": round(best_spread, 1),
        "points": results,
    }

    where = "%s at %.1f MHz" % (best["label"], best["freq_hz"] / 1e6)
    if best_ptm >= 20.0 or len(live_bands) >= 2 or total_carriers >= 3:
        verdict = "Likely antenna connected"
        # One strong band can be a single nearby transmitter reaching a bare
        # connector, so high confidence needs breadth as well as strength.
        confidence = "high" if (best_ptm >= 25.0 and len(live_bands) >= 2) else "medium"
        summary = ("Real signals are reaching the receiver - the strongest was "
                   "%.1f dB above the noise floor (%s), with %d of %d bands "
                   "showing activity. That is what a working antenna path looks "
                   "like." % (best_ptm, where, len(live_bands), len(results)))
        if len(live_bands) == 1:
            summary += (" Only one band was active, so this could also be a "
                        "single strong nearby transmitter picked up by a short "
                        "stub - check that the antenna is screwed on firmly.")
    elif best_ptm >= 12.0:
        verdict = "Likely antenna connected"
        confidence = "low"
        summary = ("Some structure was found (best %.1f dB above noise, %s), but "
                   "it is weak. The antenna is probably connected yet poorly "
                   "placed, indoors, or not resonant on these bands."
                   % (best_ptm, where))
    elif best_ptm < 10.0 and best_spread < 4.0:
        verdict = "Weak/no signal"
        confidence = "high"
        summary = ("The spectrum is essentially flat noise everywhere tested "
                   "(best %.1f dB above noise, spread %.1f dB). This is what an "
                   "unterminated or disconnected input looks like."
                   % (best_ptm, best_spread))
    else:
        verdict = "Unable to determine"
        confidence = "low"
        summary = ("The measurements are inconclusive (best peak-to-noise %.1f dB). "
                   "Try again with the antenna outdoors or near a window."
                   % best_ptm)

    return {
        "ok": True,
        "verdict": verdict,
        "confidence": confidence,
        "steps": steps,
        "metrics": metrics,
        "summary": summary,
        "duration_s": round(time.time() - t_start, 2),
        "disclaimer": (
            "An RTL-SDR cannot measure SWR, return loss or antenna impedance - it "
            "is receive-only and has no directional coupler. This result is a "
            "heuristic based on how much structure appears in the received "
            "spectrum, not an antenna measurement. Use an antenna analyser or VNA "
            "for real SWR/impedance figures."
        ),
    }
