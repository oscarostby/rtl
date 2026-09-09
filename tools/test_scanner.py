"""Tests for the Smart Scanner logic: proximity, trend, mode, survey."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rtlsdr_diag.core.motion import (AUTO, DRIVE, STATIONARY,  # noqa: E402
                                     DriveSurvey, MotionMode)
from rtlsdr_diag.core.proximity import (FALLING, MEDIUM, RISING,  # noqa: E402
                                        STABLE, STRONG, VERY_STRONG, VERY_WEAK,
                                        WEAK, CalibrationProfile,
                                        proximity_fraction, proximity_label,
                                        trend)
from rtlsdr_diag.core.signal_store import SignalStore  # noqa: E402
from rtlsdr_diag.sdr import dsp  # noqa: E402

failures: list[str] = []


def check(name, ok, detail=""):
    print("%-54s %s%s" % (name, "OK" if ok else "FAIL",
                          ("  " + detail) if detail else ""))
    if not ok:
        failures.append(name)


# ===========================================================================
print("--- proximity ---")
for snr, expected in ((35.0, VERY_STRONG), (25.0, STRONG), (15.0, MEDIUM),
                      (8.0, WEAK), (2.0, VERY_WEAK)):
    got = proximity_label(snr)
    check("SNR %.0f dB -> %s" % (snr, expected), got == expected, got)
check("NaN SNR is treated as the weakest",
      proximity_label(float("nan")) == VERY_WEAK)
check("proximity fraction is bounded",
      proximity_fraction(-5.0) == 0.0 and proximity_fraction(500.0) == 1.0)
check("proximity rises with SNR",
      proximity_fraction(10.0) < proximity_fraction(30.0))

# ===========================================================================
print("")
print("--- trend ---")
now = 1000.0
rising = [(now - 20 + i, -60.0 + i * 1.0, 20.0) for i in range(20)]
falling = [(now - 20 + i, -40.0 - i * 1.0, 20.0) for i in range(20)]
flat = [(now - 20 + i, -50.0, 20.0) for i in range(20)]
noisy = [(now - 20 + i, -50.0 + (1.0 if i % 2 else -1.0), 20.0) for i in range(20)]

check("rising level detected", trend(rising, 15.0, now).direction == RISING)
check("falling level detected", trend(falling, 15.0, now).direction == FALLING)
check("flat level is stable", trend(flat, 15.0, now).direction == STABLE)
check("small wobble is not a trend",
      trend(noisy, 15.0, now).direction == STABLE,
      "%.2f dB" % trend(noisy, 15.0, now).change_db)
r = trend(rising, 15.0, now)
check("trend reports the magnitude", r.change_db > 2.0, "%+.1f dB" % r.change_db)
check("trend reports the window", r.window_s == 15.0)
check("empty history is stable", trend([], 15.0, now).direction == STABLE)
check("too few samples is stable",
      trend(rising[-2:], 15.0, now).direction == STABLE)
check("trend text mentions the window", "/ 15 s" in r.text(), r.text())
# A window shorter than the data must only use the recent part.
short = trend(rising, 5.0, now)
check("shorter window uses fewer samples", short.samples < r.samples,
      "%d vs %d" % (short.samples, r.samples))

# ===========================================================================
print("")
print("--- motion mode hysteresis ---")
m = MotionMode(AUTO)
check("no GPS defaults to stationary", m.effective == STATIONARY)
check("no GPS is reported honestly", not m.has_gps and "no GPS" in m.describe(),
      m.describe())

t = 0.0
for _ in range(3):                       # under the 5 s dwell
    t += 1.0
    m.update_speed(20.0, t)
check("brief movement does not flip the mode immediately",
      m.effective == STATIONARY, m.effective)
for _ in range(4):                       # now past 5 s of sustained movement
    t += 1.0
    m.update_speed(20.0, t)
check("sustained movement switches to DRIVE", m.effective == DRIVE, m.effective)

for _ in range(5):                       # slow, but not for long enough
    t += 1.0
    m.update_speed(0.5, t)
check("a brief stop does not flip back", m.effective == DRIVE, m.effective)
for _ in range(12):
    t += 1.0
    m.update_speed(0.5, t)
check("sustained stop switches to STATIONARY", m.effective == STATIONARY,
      m.effective)

# Between the thresholds nothing changes.
m2 = MotionMode(AUTO)
t = 0.0
for _ in range(20):
    t += 1.0
    m2.update_speed(20.0, t)
check("moving before the ambiguous band", m2.effective == DRIVE)
for _ in range(30):
    t += 1.0
    m2.update_speed(3.5, t)              # between 2 and 5 km/h
check("ambiguous speed preserves the previous mode", m2.effective == DRIVE,
      m2.effective)

# Losing the fix must not invent movement.
before = m2.effective
m2.update_speed(None, t + 1.0)
check("losing GPS holds the last mode", m2.effective == before)

# A manual choice overrides AUTO.
m2.select(STATIONARY)
check("manual selection overrides AUTO", m2.effective == STATIONARY)
check("describe reports the manual mode", m2.describe() == STATIONARY)

# ===========================================================================
print("")
print("--- drive survey ---")
survey = DriveSurvey(interval_s=1.0)
base = 1000.0
for i in range(5):
    survey.record(59.90 + i * 0.001, 10.70, 30.0, 100e6,
                  -60.0 + i * 2.0, 20.0, "40.2", now=base + i)
check("one sample per interval", len(survey) == 5, "%d" % len(survey))
blocked = survey.record(59.95, 10.70, 30.0, 100e6, -50.0, 20.0, "40.2",
                        now=base + 4.2)
check("samples faster than the interval are dropped", blocked is None)
forced = survey.record(59.95, 10.70, 30.0, 100e6, -50.0, 20.0, "40.2",
                       now=base + 4.3, force=True)
check("a forced sample is always taken", forced is not None and len(survey) == 6)

check("strongest point found",
      abs(survey.strongest().level_dbfs - (-50.0)) < 1e-9,
      "%.1f" % survey.strongest().level_dbfs)
check("weakest point found",
      abs(survey.weakest().level_dbfs - (-60.0)) < 1e-9)
check("route length is positive", survey.route_length_km() > 0,
      "%.3f km" % survey.route_length_km())
check("single gain reported", survey.gains_used() == {"40.2"},
      str(survey.gains_used()))
survey.record(59.96, 10.70, 30.0, 100e6, -55.0, 20.0, "auto",
              now=base + 10.0)
check("mixed gains are detectable", len(survey.gains_used()) == 2,
      str(sorted(str(g) for g in survey.gains_used())))

tmp = Path(tempfile.mkdtemp())
path = tmp / "survey.csv"
survey.save(path)
again = DriveSurvey()
n, w = again.load(path)
check("survey CSV round trip", n == len(survey) and not w, "%d rows" % n)
check("round trip keeps positions",
      abs(again.samples[0].lat - survey.samples[0].lat) < 1e-6)

bad = tmp / "bad.csv"
bad.write_text("latitude,longitude,level_dbfs\n95.0,10.0,-50\n59.9,10.0,-50\n",
               encoding="utf-8")
n2, w2 = again.load(bad)
check("out-of-range survey rows rejected with a reason",
      n2 == 1 and len(w2) == 1 and "latitude" in w2[0], str(w2))

# ===========================================================================
print("")
print("--- calibration is the only route to a distance ---")
profile = CalibrationProfile.from_two_points("Test beacon", 433.92e6,
                                             10.0, -40.0, 100.0, -60.0)
check("path-loss exponent recovered from two points",
      abs(profile.path_loss_exponent - 2.0) < 0.05,
      "%.2f" % profile.path_loss_exponent)
check("distance at the reference level",
      abs(profile.estimate_distance_m(-40.0) - 10.0) < 0.5,
      "%.1f m" % profile.estimate_distance_m(-40.0))
check("weaker signal implies further away",
      profile.estimate_distance_m(-60.0) > profile.estimate_distance_m(-40.0))
try:
    CalibrationProfile.from_two_points("bad", 1e6, 10.0, -40.0, 10.0, -50.0)
    check("identical reference distances rejected", False)
except ValueError:
    check("identical reference distances rejected", True)

# ===========================================================================
print("")
print("--- detector: wide and narrow channels ---")
# A synthetic FM-like carrier: wide, with modulation nulls inside it.
rng = np.random.default_rng(7)
n = 8192
fs = 2.4e6
freqs = np.linspace(100e6 - fs / 2, 100e6 + fs / 2, n)
bin_hz = freqs[1] - freqs[0]
power = -80.0 + rng.normal(0.0, 1.5, n)
centre = n // 2
half = int((180e3 / 2) / bin_hz)
power[centre - half:centre + half] += 40.0
# Punch modulation nulls through it, as a real FM signal has: wide enough to
# break a naive contiguous-bin search, which is exactly the failure that made
# one station appear as dozens of Unknown slivers.
null_w = max(3, int(round(4e3 / bin_hz)))
for k in range(centre - half, centre + half, null_w * 3):
    power[k:k + null_w] -= 34.0

flat = dsp.find_carriers(freqs, power, 10.0, 60e3, smoothing_hz=0.0, gap_hz=0.0)
smoothed = dsp.find_carriers(freqs, power, 10.0, 60e3,
                             smoothing_hz=15e3, gap_hz=40e3)
# Without smoothing the nulls break the carrier into pieces, each too narrow
# to survive the 60 kHz minimum - so the station vanishes entirely rather than
# being reported once. Either way it is not recovered correctly.
check("without smoothing a nulled carrier is not recovered", len(flat) != 1,
      "%d detection(s)" % len(flat))
check("smoothing recovers it as one signal", len(smoothed) == 1,
      "%d detection(s)" % len(smoothed))
if smoothed:
    check("recovered bandwidth is about right",
          120e3 < smoothed[0].bandwidth_hz < 260e3,
          "%.0f kHz" % (smoothed[0].bandwidth_hz / 1e3))

# Two narrow channels 25 kHz apart must stay separate with narrow settings.
power2 = -80.0 + rng.normal(0.0, 1.0, n)
for offset in (-12.5e3, 12.5e3):
    k = centre + int(offset / bin_hz)
    w = int((10e3 / 2) / bin_hz)
    power2[k - w:k + w] += 35.0
narrow = dsp.find_carriers(freqs, power2, 10.0, 8e3,
                           smoothing_hz=2e3, gap_hz=5e3)
check("adjacent 25 kHz channels stay separate", len(narrow) == 2,
      "%d detection(s)" % len(narrow))
wide_settings = dsp.find_carriers(freqs, power2, 10.0, 8e3,
                                  smoothing_hz=15e3, gap_hz=40e3)
check("wide settings would merge them (why presets matter)",
      len(wide_settings) == 1, "%d" % len(wide_settings))

# Narrow spurs must not pass a wide minimum bandwidth.
power3 = -80.0 + rng.normal(0.0, 1.0, n)
k = centre
w = max(1, int((2e3 / 2) / bin_hz))
power3[k - w:k + w] += 35.0
spur = dsp.find_carriers(freqs, power3, 10.0, 60e3,
                         smoothing_hz=15e3, gap_hz=40e3)
check("a 2 kHz spur is rejected by a 60 kHz minimum", len(spur) == 0,
      "%d" % len(spur))

# ===========================================================================
print("")
print("--- store feeds trend ---")
store = SignalStore()
now = time.time()
for i in range(12):
    store.begin_pass()
    store.add_or_update_detection(100e6, -60.0 + i, -80.0, 20.0 + i, 180e3,
                                  source="Scanner")
det = store.items()[0]
check("history accumulates with each pass", len(det.history) == 12,
      "%d" % len(det.history))
check("average is computed from history",
      det.average_level_db(3600.0) < det.level_dbfs)
check("history_since filters by age",
      len(det.history_since(0.0, det.history[-1][0])) >= 1)
check("min level tracked", abs(det.min_level_db() - (-60.0)) < 1e-9)

print("")
if failures:
    print("FAILED (%d): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("ALL SCANNER TESTS PASSED")
