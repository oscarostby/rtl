"""Pointing the receiver at the uplink channels that can actually carry traffic.

A base station carrier names its uplink partner exactly, 10 MHz down. An
RTL-SDR cannot watch the whole uplink band at once, so it is better to sit on
the partners of the masts being received than to sweep past them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from rtlsdr_diag.config import TETRA_DUPLEX_SPACING_HZ  # noqa: E402
from rtlsdr_diag.core.detector import (MODE_SPECS, TETRA_MOBILE)  # noqa: E402
from rtlsdr_diag.core.duplex import (best_window, uplink_partners,  # noqa: E402
                                     watch_window)
from rtlsdr_diag.core.models import (CLASS_FM, CLASS_TETRA,  # noqa: E402
                                     LiveSignalDetection)

failures: list[str] = []


def check(name, ok, detail=""):
    print("%-58s %s%s" % (name, "OK" if ok else "FAIL",
                          ("  " + detail) if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def det(freq_mhz, snr=20.0, cls=CLASS_TETRA):
    return LiveSignalDetection(freq_mhz * 1e6, -50.0, -80.0, snr, 25e3, cls)


spec = MODE_SPECS[TETRA_MOBILE]
WIDTH = spec.sample_rate * 0.75

# Downlink carriers actually measured at one location, with their strengths.
MEASURED = [(390.2625, 21.2), (391.0625, 19.0), (391.4060, 40.5),
            (391.7375, 19.0), (392.5375, 32.5), (393.0125, 35.9),
            (393.8125, 18.0), (394.3125, 18.0)]
dets = [det(f, s) for f, s in MEASURED]

print("--- a mast carrier names its uplink channel ---")
partners = uplink_partners(dets)
check("every mast carrier yields one partner", len(partners) == len(MEASURED),
      "%d from %d" % (len(partners), len(MEASURED)))
check("exactly the duplex spacing below",
      all(abs((d.freq_hz - f) - TETRA_DUPLEX_SPACING_HZ) < 1.0
          for (f, _), d in zip(partners, sorted(dets, key=lambda x: x.freq_hz))),
      "%.1f MHz" % (TETRA_DUPLEX_SPACING_HZ / 1e6))
check("and every partner lands in the uplink half",
      all(spec.contains(f) for f, _ in partners))

check("nothing but mast carriers counts",
      uplink_partners([det(105.8, 30.0, CLASS_FM), det(381.4, 30.0)]) == [],
      "an FM signal and an uplink carrier are not masts")

print("")
print("--- where to put the one window there is ---")
placed = watch_window(dets, WIDTH)
check("a window is chosen once a mast is heard", placed is not None)
start, stop, count = placed
check("it is exactly one receiver window wide",
      abs((stop - start) - WIDTH) < 1.0, "%.3f MHz" % ((stop - start) / 1e6))
check("it stays inside the uplink half",
      spec.start_hz <= start and stop <= spec.stop_hz,
      "%.3f - %.3f MHz" % (start / 1e6, stop / 1e6))
check("it covers more than one channel", count >= 2, "%d channels" % count)

strongest = max(MEASURED, key=lambda x: x[1])[0] - 10.0
check("it covers the partner of the strongest mast",
      start <= strongest * 1e6 <= stop,
      "%.4f MHz, the pair of the %.1f dB carrier"
      % (strongest, max(m[1] for m in MEASURED)))

# Measured on real hardware: 52 partners were known, and 28 weak ones sat
# together several MHz from the three strong ones. Scoring windows by the
# total strength inside them put the receiver on the crowd and pointed it at
# empty spectrum, so the strongest partner has to anchor the placement.
REAL_STRONG = [(393.0130, 39.1), (391.4040, 36.3), (392.5380, 33.3)]
REAL_CROWD = [(397.6 + i * 0.05, 9.0) for i in range(28)]
noisy = [det(f, s) for f, s in REAL_STRONG + REAL_CROWD]
placed2 = watch_window(noisy, WIDTH)
check("28 weak carriers do not drag the window off the near cell",
      placed2[0] <= 383.0130e6 <= placed2[1],
      "%.3f - %.3f MHz, %d channels"
      % (placed2[0] / 1e6, placed2[1] / 1e6, placed2[2]))
check("and the other strong partners come along with it",
      all(placed2[0] <= (f - 10.0) * 1e6 <= placed2[1] for f, _ in REAL_STRONG),
      "the three spanned %.3f MHz"
      % ((max(f for f, _ in REAL_STRONG) - min(f for f, _ in REAL_STRONG))))
check("the crowd is not what got covered",
      not any(placed2[0] <= (f - 10.0) * 1e6 <= placed2[1]
              for f, _ in REAL_CROWD))

# A channel on the very edge of the window is the one measured worst, and the
# anchor is the channel that matters most - it must not end up there.
from rtlsdr_diag.core.duplex import EDGE_MARGIN_HZ  # noqa: E402

EDGE_CASE = [(393.0125, 29.0), (396.0125, 27.2), (391.4055, 27.0),
             (392.5375, 23.7), (393.5855, 21.1)]
placed3 = watch_window([det(f, s) for f, s in EDGE_CASE], WIDTH)
anchor = (max(EDGE_CASE, key=lambda x: x[1])[0] - 10.0) * 1e6
clearance = min(anchor - placed3[0], placed3[1] - anchor)
check("the strongest partner is not left astride the window edge",
      clearance >= EDGE_MARGIN_HZ - 1.0,
      "%.0f kHz clear, %.0f kHz required (a channel is 25 kHz wide)"
      % (clearance / 1e3, EDGE_MARGIN_HZ / 1e3))

print("")
print("--- before anything has been heard ---")
check("with no mast known, nothing is chosen", watch_window([], WIDTH) is None)
check("and an empty list of partners places no window",
      best_window([], WIDTH) is None)

print("")
print("--- what the receiver is actually told ---")
from rtlsdr_diag.ui.app_core import AppCore  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
core = AppCore(simulate=True)
lo, hi = core.sweep_range(TETRA_MOBILE)
check("with nothing heard it sweeps the whole uplink half",
      lo == spec.start_hz and hi == spec.stop_hz,
      "%.1f - %.1f MHz" % (lo / 1e6, hi / 1e6))

for freq, snr in MEASURED:
    core.signal_store.add_or_update_detection(freq * 1e6, -50.0, -80.0, snr,
                                              25e3, source="test")
lo, hi = core.sweep_range(TETRA_MOBILE)
check("once masts are heard it sits on their partners",
      abs((hi - lo) - WIDTH) < 1.0, "%.3f - %.3f MHz" % (lo / 1e6, hi / 1e6))
steps_swept = round((spec.stop_hz - spec.start_hz) / WIDTH)
check("which is one step instead of a sweep of several",
      steps_swept > 1, "sweeping would be %d steps, so each channel would be "
      "watched %.0f%% of the time" % (steps_swept, 100.0 / steps_swept))

core.pair_watch = False
lo, hi = core.sweep_range(TETRA_MOBILE)
check("turning it off goes back to sweeping",
      lo == spec.start_hz and hi == spec.stop_hz,
      "%.1f - %.1f MHz" % (lo / 1e6, hi / 1e6))
core.shutdown()

print("")
if failures:
    print("DUPLEX TESTS FAILED (%d):" % len(failures))
    for f in failures:
        print("   - %s" % f)
    sys.exit(1)
print("ALL DUPLEX TESTS PASSED")
