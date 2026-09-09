"""End-to-end hardware self-test for the RTL-SDR.

Runs inside the acquisition worker thread so it shares the open device handle.
"""
from __future__ import annotations

import time

import numpy as np

from . import dsp
from .device import (SdrError, device_count, enumerate_devices,
                     library_available, library_status)

TEST_FREQS_HZ = (100.0e6, 390.0e6, 433.0e6)
TEST_SAMPLE_RATE = 2.048e6
TEST_SAMPLES = 262_144


def _step(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def run_hardware_test(engine, progress=lambda msg: None) -> dict:
    """Open the device, tune it, pull IQ and sanity-check the stream."""
    steps: list[dict] = []
    metrics: dict = {}
    t_start = time.time()

    # 1 - driver / library --------------------------------------------------
    simulated = getattr(engine, "_simulate", False)
    if simulated:
        steps.append(_step("Driver library", True,
                           "Simulation mode - no real driver involved."))
    elif library_available():
        steps.append(_step("Driver library", True, library_status()))
    else:
        steps.append(_step("Driver library", False, library_status()))
        return _finish(steps, metrics, t_start,
                       "librtlsdr could not be loaded. See the Driver Help tab.")

    # 2 - USB enumeration ---------------------------------------------------
    progress("Enumerating USB devices...")
    if simulated:
        count = 1
        steps.append(_step("USB enumeration", True, "1 simulated device"))
    else:
        count = device_count()
        infos = enumerate_devices()
        if count > 0:
            detail = "; ".join(i.label() for i in infos) or "%d device(s)" % count
            steps.append(_step("USB enumeration", True, detail))
        else:
            steps.append(_step("USB enumeration", False,
                               "No RTL-SDR devices reported by the driver."))
            return _finish(steps, metrics, t_start,
                           "No RTL-SDR found on USB. Check the cable, then the "
                           "WinUSB driver (see Driver Help).")
    metrics["device_count"] = count

    # 3 - open --------------------------------------------------------------
    progress("Opening device...")
    src = engine._test_source()
    if src is None or not src.is_open:
        steps.append(_step("Open device", False,
                           engine.last_error or "Device could not be opened."))
        return _finish(steps, metrics, t_start,
                       "The device was found but could not be opened. On Windows "
                       "this almost always means the WinUSB driver is missing "
                       "(use Zadig) or another program has the device open.")
    info = src.describe()
    steps.append(_step("Open device", True,
                       "%s  (tuner: %s, serial: %s)" % (info.get("name", "?"),
                                                        info.get("tuner", "?"),
                                                        info.get("serial", "") or "n/a")))
    metrics.update(device_name=info.get("name", ""), tuner=info.get("tuner", ""),
                   serial=info.get("serial", ""))

    # 4 - configure ---------------------------------------------------------
    progress("Setting sample rate and frequency...")
    try:
        src.set_sample_rate(TEST_SAMPLE_RATE)
        src.set_gain("auto")
        src.set_ppm(0)
        src.set_center_freq(TEST_FREQS_HZ[0])
        actual = src.describe()
        rate = actual.get("sample_rate_hz") or TEST_SAMPLE_RATE
        cf = actual.get("center_freq_hz") or TEST_FREQS_HZ[0]
        rate_ok = abs(float(rate) - TEST_SAMPLE_RATE) / TEST_SAMPLE_RATE < 0.05
        steps.append(_step("Configure receiver", rate_ok,
                           "sample rate %.4f MS/s, centre %.4f MHz"
                           % (float(rate) / 1e6, float(cf) / 1e6)))
        metrics["sample_rate_hz"] = float(rate)
    except SdrError as exc:
        steps.append(_step("Configure receiver", False, str(exc)))
        return _finish(steps, metrics, t_start, "The device rejected its settings.")

    # 5 - retune check ------------------------------------------------------
    progress("Verifying the tuner follows frequency changes...")
    retune_ok = True
    retune_detail = []
    for f in TEST_FREQS_HZ:
        try:
            src.set_center_freq(f)
            got = float(src.describe().get("center_freq_hz") or 0.0)
            err_hz = abs(got - f)
            ok = err_hz < 5000.0
            retune_ok = retune_ok and ok
            retune_detail.append("%.3f -> %.3f MHz" % (f / 1e6, got / 1e6))
        except SdrError as exc:
            retune_ok = False
            retune_detail.append("%.3f MHz failed: %s" % (f / 1e6, exc))
    steps.append(_step("Tuner retune", retune_ok, ",  ".join(retune_detail)))

    # 6 - sample capture ----------------------------------------------------
    progress("Capturing IQ samples...")
    try:
        src.set_center_freq(TEST_FREQS_HZ[0])
        t0 = time.perf_counter()
        iq = src.read(TEST_SAMPLES)
        elapsed = time.perf_counter() - t0
    except SdrError as exc:
        steps.append(_step("Sample capture", False, str(exc)))
        return _finish(steps, metrics, t_start,
                       "The device is open but no samples could be read.")

    n = int(iq.size)
    expected = n / float(metrics.get("sample_rate_hz", TEST_SAMPLE_RATE))
    throughput = n / elapsed if elapsed > 0 else 0.0
    steps.append(_step("Sample capture", n > 0,
                       "%d samples in %.3f s (%.2f MS/s effective, expected %.3f s)"
                       % (n, elapsed, throughput / 1e6, expected)))
    metrics.update(samples_received=n, capture_seconds=round(elapsed, 4),
                   effective_rate_hz=round(throughput, 1))

    # 7 - stream health -----------------------------------------------------
    progress("Analysing the sample stream...")
    health = dsp.stream_health(iq)
    metrics.update(health)
    problems = []
    if health["all_zero"]:
        problems.append("every sample is zero")
    if health["stuck"]:
        problems.append("the stream is stuck (no variation)")
    if health.get("unique_levels", 999) < 8:
        problems.append("only %d distinct ADC levels" % health.get("unique_levels", 0))
    if health["clipping_pct"] > 5.0:
        problems.append("%.1f%% of samples are clipping - reduce gain"
                        % health["clipping_pct"])
    if health["dc_offset"] > 0.25:
        problems.append("very large DC offset (%.3f)" % health["dc_offset"])
    stream_ok = not health["all_zero"] and not health["stuck"]
    steps.append(_step(
        "Sample stream health", stream_ok,
        "; ".join(problems) if problems else
        "std %.4f, DC offset %.4f, clipping %.2f%%, %d distinct levels"
        % (health["std"], health["dc_offset"], health["clipping_pct"],
           health.get("unique_levels", 0))))

    # 8 - power / spectrum --------------------------------------------------
    power = dsp.psd_dbfs(iq, 4096, max_segments=16)
    nf = dsp.noise_floor_db(power)
    peak = float(power.max())
    avg_power = health["power_dbfs"]
    metrics.update(average_power_dbfs=round(avg_power, 2),
                   noise_floor_dbfs=round(nf, 2),
                   peak_dbfs=round(peak, 2),
                   peak_to_noise_db=round(peak - nf, 2))
    power_ok = np.isfinite(avg_power) and avg_power > -120.0
    steps.append(_step("Signal power", power_ok,
                       "average %.1f dBFS, noise floor %.1f dBFS, peak %.1f dBFS "
                       "(peak-to-noise %.1f dB)" % (avg_power, nf, peak, peak - nf)))

    ok = all(s["ok"] for s in steps)
    if ok:
        summary = ("Hardware test PASSED. %d samples received at %.3f MS/s, "
                   "average power %.1f dBFS, noise floor %.1f dBFS."
                   % (n, metrics.get("sample_rate_hz", 0) / 1e6, avg_power, nf))
        if simulated:
            summary += " (Simulation mode - this did not test real hardware.)"
    else:
        failed = [s["name"] for s in steps if not s["ok"]]
        summary = "Hardware test FAILED at: " + ", ".join(failed)
    return _finish(steps, metrics, t_start, summary, ok)


def _finish(steps, metrics, t_start, summary, ok=None) -> dict:
    if ok is None:
        ok = all(s["ok"] for s in steps) if steps else False
    return {
        "ok": bool(ok),
        "steps": steps,
        "metrics": metrics,
        "summary": summary,
        "duration_s": round(time.time() - t_start, 2),
    }
