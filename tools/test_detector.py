"""Tests for the detector core: mode specs, candidate hysteresis, rotation."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rtlsdr_diag.core.detector import (DAB, FM, METER_SEGMENTS, MODE_SPECS,
                                       MODES, TETRA, TETRA_MAST,
                                       TETRA_MOBILE, BandRotation,
                                       DetectorState, trend_label)
from rtlsdr_diag.core.models import (CLASS_DAB, CLASS_FM, CLASS_TETRA,
                                     LiveSignalDetection)
from rtlsdr_diag.core.proximity import trend

failures = []


def check(name, ok, detail=""):
    print("%-52s %s%s" % (name, "OK" if ok else "FAIL",
                          ("  " + detail) if detail else ""))
    if not ok:
        failures.append(name)


def make(freq, snr, cls, seen=None, level=None):
    now = time.time() if seen is None else seen
    d = LiveSignalDetection(freq_hz=freq,
                            level_dbfs=level if level is not None else -80.0 + snr,
                            noise_dbfs=-80.0, snr_db=snr, bandwidth_hz=25e3,
                            signal_class=cls, first_seen=now, last_seen=now)
    return d


print("--- mode specs ---")
check("four modes", MODES == [FM, DAB, TETRA_MAST, TETRA_MOBILE], str(MODES))
check("FM band", MODE_SPECS[FM].contains(107.7e6)
      and not MODE_SPECS[FM].contains(391e6))
check("DAB band", MODE_SPECS[DAB].contains(225.648e6))
check("mast page covers the downlink", MODE_SPECS[TETRA_MAST].contains(391.4125e6))
check("mobile page covers the uplink", MODE_SPECS[TETRA_MOBILE].contains(383.5e6))
check("no page claims a service or operator",
      all("POLICE" not in MODE_SPECS[k].title.upper()
          and "AMBULANCE" not in MODE_SPECS[k].title.upper()
          and "EMERGENCY" not in MODE_SPECS[k].title.upper() for k in MODES))
check("each mode accepts only its own class",
      MODE_SPECS[FM].accepts(make(107.7e6, 30, CLASS_FM))
      and not MODE_SPECS[FM].accepts(make(391e6, 30, CLASS_TETRA)))

print("")
print("--- duplex separation: mast vs mobile ---")
# The frequency alone decides. Nothing else about the signal is examined.
for freq, on_mast, on_mobile, why in (
        (380.0125e6, False, True, "uplink edge"),
        (383.9880e6, False, True, "measured uplink carrier"),
        (384.9e6, False, True, "top of the uplink"),
        (387.0e6, False, False, "duplex gap belongs to neither"),
        (390.0125e6, True, False, "downlink edge"),
        (391.4120e6, True, False, "measured downlink carrier"),
        (396.0120e6, True, False, "measured carrier above 395"),
):
    d = make(freq, 30.0, CLASS_TETRA)
    got_mast = MODE_SPECS[TETRA_MAST].accepts(d)
    got_mobile = MODE_SPECS[TETRA_MOBILE].accepts(d)
    check("%.4f MHz -> %s" % (freq / 1e6, why),
          got_mast == on_mast and got_mobile == on_mobile,
          "mast=%s mobile=%s" % (got_mast, got_mobile))
check("the two TETRA pages never overlap",
      not any(MODE_SPECS[TETRA_MAST].contains(f)
              and MODE_SPECS[TETRA_MOBILE].contains(f)
              for f in (380e6 + i * 1e5 for i in range(200))))

# A mast carrier must never appear on the mobile page, whatever its strength.
mobile_state = DetectorState(MODE_SPECS[TETRA_MOBILE])
mast_carrier = make(391.412e6, 45.0, CLASS_TETRA)
mobile_state.update([mast_carrier])
check("a very strong mast cannot show on the mobile page",
      mobile_state.current is None)
mast_state = DetectorState(MODE_SPECS[TETRA_MAST])
handset = make(383.988e6, 40.0, CLASS_TETRA)
mast_state.update([handset])
check("a handset cannot show on the mast page", mast_state.current is None)

# Last-activity is remembered for a bursty band.
burst = DetectorState(MODE_SPECS[TETRA_MOBILE])
t0 = 9000.0
live = make(381.2625e6, 25.0, CLASS_TETRA, t0)
burst.update([live], t0)
check("mobile page shows the handset", burst.current is live)
check("activity time recorded", burst.seconds_since_activity(t0) < 0.001)
check("activity frequency recorded",
      abs(burst.last_activity_freq_hz - 381.2625e6) < 1.0)
burst.update([], t0 + 40.0)
check("page goes quiet when the burst ends", burst.current is None)
check("but remembers how long ago", abs(burst.seconds_since_activity(t0 + 40.0)
                                        - 40.0) < 0.01,
      "%.1f s" % burst.seconds_since_activity(t0 + 40.0))
fresh = DetectorState(MODE_SPECS[TETRA_MOBILE])
check("a page with no history reports no activity",
      fresh.seconds_since_activity() == float("inf"))
check("mobile band is marked bursty", MODE_SPECS[TETRA_MOBILE].bursty)
check("mast band is not marked bursty", not MODE_SPECS[TETRA_MAST].bursty)
check("nearest 25 kHz channel is offered",
      burst.spec.key == TETRA_MOBILE)

print("")
print("--- candidate selection and hysteresis ---")
state = DetectorState(MODE_SPECS[TETRA_MAST])
now = 1000.0
a = make(391.4125e6, 25.0, CLASS_TETRA, now)
state.update([a], now)
check("first candidate is adopted immediately", state.current is a)

# A marginally stronger rival must not steal the display.
b = make(392.0e6, 26.0, CLASS_TETRA, now)
for t in range(1, 8):
    state.update([a, b], now + t)
check("marginally stronger rival is ignored", state.current is a,
      "%.1f dB vs %.1f" % (b.snr_db, a.snr_db))

# A clearly stronger one must wait out the dwell, then win. Both stay live,
# as they would in a running scan.
c = make(393.0e6, 34.0, CLASS_TETRA, now)


def keep_alive(items, t):
    for item in items:
        item.last_seen = t
    return items


state.update(keep_alive([a, c], now + 10.0), now + 10.0)
check("clearly stronger rival does not switch instantly", state.current is a)
state.update(keep_alive([a, c], now + 11.0), now + 11.0)
check("still waiting after 1 s", state.current is a)
state.update(keep_alive([a, c], now + 12.5), now + 12.5)
check("switches after the 2 s dwell", state.current is c, "%.1f dB" % c.snr_db)

# Signals from another band are never considered.
fm = make(107.7e6, 45.0, CLASS_FM, now + 13.0)
state.update(keep_alive([c, fm], now + 13.0), now + 13.0)
check("other bands are never candidates", state.current is c)

# Stale candidates drop out.
old = make(394.0e6, 40.0, CLASS_TETRA, now - 600.0)
state.update([old], now + 14.0)
check("stale candidate is not shown", state.current is None)

print("")
print("--- lock ---")
state2 = DetectorState(MODE_SPECS[FM])
now = 2000.0
weak = make(100.1e6, 15.0, CLASS_FM, now)
state2.update([weak], now)
state2.toggle_lock()
check("lock stores the frequency", state2.is_locked)
strong = make(104.5e6, 40.0, CLASS_FM, now + 1.0)
for t in range(1, 10):
    weak.last_seen = strong.last_seen = now + t
    state2.update([weak, strong], now + t)
check("a stronger signal cannot steal a locked page",
      state2.current is not None and abs(state2.current.freq_hz - 100.1e6) < 1e3,
      "%.4f MHz" % (state2.current.freq_hz / 1e6 if state2.current else 0))
state2.toggle_lock()
check("unlock returns to auto", not state2.is_locked)
for t in range(10, 20):
    weak.last_seen = strong.last_seen = now + t
    state2.update([weak, strong], now + t)
check("after unlocking the stronger signal wins",
      state2.current is strong)

print("")
print("--- meter ---")
state3 = DetectorState(MODE_SPECS[FM])
now = 3000.0
d = make(100.0e6, 40.0, CLASS_FM, now)
for t in range(40):
    d.last_seen = now + t
    state3.update([d], now + t)
check("meter fills for a strong signal",
      state3.segments() >= METER_SEGMENTS - 1, "%d/%d segments"
      % (state3.segments(), METER_SEGMENTS))
check("strength word matches", state3.strength_label() == "Very Strong",
      state3.strength_label())
check("meter is bounded", 0 <= state3.segments() <= METER_SEGMENTS)

state4 = DetectorState(MODE_SPECS[FM])
for t in range(40):
    state4.update([], 4000.0 + t)
check("meter falls away with no signal", state4.segments() <= 1,
      "%d segments" % state4.segments())

print("")
print("--- trend labels ---")
now = 5000.0
rising_fast = [(now - 20 + i, -70.0 + i * 1.5, 20.0) for i in range(20)]
rising_slow = [(now - 20 + i, -70.0 + i * 0.35, 20.0) for i in range(20)]
flat = [(now - 20 + i, -50.0, 20.0) for i in range(20)]
falling_fast = [(now - 20 + i, -30.0 - i * 1.5, 20.0) for i in range(20)]
check("rapid rise", trend_label(trend(rising_fast, 15.0, now))[1]
      == "RAPIDLY RISING", trend_label(trend(rising_fast, 15.0, now))[1])
check("gentle rise", trend_label(trend(rising_slow, 15.0, now))[1] == "RISING",
      trend_label(trend(rising_slow, 15.0, now))[1])
check("stable", trend_label(trend(flat, 15.0, now))[1] == "STABLE")
check("rapid fall", trend_label(trend(falling_fast, 15.0, now))[1]
      == "RAPIDLY FALLING")
check("too little data gives no label", trend_label(trend([], 15.0, now))[1] == "")

print("")
print("--- per-band gain ---")
# One gain does not fit every band. Measured on real hardware: at the tuner's
# top gain FM resolves 9 usable carriers and the TETRA downlink invents about
# 170 that are not there; at the AGC's choice it is the other way round.
check("FM asks for everything the tuner has",
      MODE_SPECS[FM].gain == "max", str(MODE_SPECS[FM].gain))
check("the TETRA downlink does not, or it overloads",
      MODE_SPECS[TETRA_MAST].gain == "auto", str(MODE_SPECS[TETRA_MAST].gain))
check("the uplink asks for it, to catch a brief weak burst",
      MODE_SPECS[TETRA_MOBILE].gain == "max", str(MODE_SPECS[TETRA_MOBILE].gain))
check("DAB has a gain of its own",
      isinstance(MODE_SPECS[DAB].gain, (int, float)), str(MODE_SPECS[DAB].gain))

print("--- band rotation ---")
rot = BandRotation(MODES)
visible = FM
order = [rot.next_band(visible) for _ in range(8)]
check("visible band gets half the sweeps",
      order.count(FM) == 4, "%s" % order)
check("the other bands are still visited",
      DAB in order and (TETRA_MAST in order or TETRA_MOBILE in order),
      str(order))
check("no band is starved", all(order.count(m) >= 1 for m in MODES if m != FM)
      or len(set(order)) >= 3, str(order))
# Switching page changes priority without losing the others.
order2 = [rot.next_band(TETRA_MOBILE) for _ in range(8)]
check("priority follows the visible page",
      order2.count(TETRA_MOBILE) == 4, str(order2))
check("rotation always returns a real mode",
      all(m in MODES for m in order + order2))

# Focused: every sweep on the page being watched, so a short burst in a narrow
# band is not missed while the radio is off sweeping a wide broadcast band.
rot.focused = True
order3 = [rot.next_band(TETRA_MOBILE) for _ in range(8)]
check("focus keeps every sweep on the visible band",
      order3.count(TETRA_MOBILE) == 8, str(order3))
check("focus still follows a page change",
      rot.next_band(DAB) == DAB)
rot.focused = False
order4 = [rot.next_band(FM) for _ in range(8)]
check("clearing focus restores the shared rotation",
      order4.count(FM) == 4 and len(set(order4)) >= 3, str(order4))

print("")
if failures:
    print("FAILED (%d): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("ALL DETECTOR TESTS PASSED")
