"""The signal log: what it records, how it groups it, and what it refuses to say."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from rtlsdr_diag.core.models import (CLASS_DAB, CLASS_FM,  # noqa: E402
                                     CLASS_TETRA, LiveSignalDetection)
from rtlsdr_diag.core.observations import (BRIEF_S, KIND_DAB,  # noqa: E402
                                           KIND_FM, KIND_MAST, KIND_MOBILE,
                                           ObservationLog, channel_of,
                                           kind_for)
from rtlsdr_diag.ui.log_panel import LogPanel  # noqa: E402

failures: list[str] = []


def check(name, ok, detail=""):
    print("%-58s %s%s" % (name, "OK" if ok else "FAIL",
                          ("  " + detail) if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def det(freq_mhz, snr=20.0, cls=CLASS_TETRA, level=-50.0):
    return LiveSignalDetection(freq_mhz * 1e6, level, -80.0, snr, 20e3, cls)


app = QApplication(sys.argv)

print("--- which end of the link ---")
# The band plan is the whole basis for this: uplink is a terminal, downlink is
# infrastructure. Nothing else about the signal is looked at.
check("a downlink carrier is fixed infrastructure",
      kind_for(det(396.0125)) == KIND_MAST)
check("an uplink carrier is a terminal nearby",
      kind_for(det(381.2625)) == KIND_MOBILE)
check("the same shape of signal in each band reads differently",
      kind_for(det(391.4125)) != kind_for(det(383.9)))
check("FM is FM", kind_for(det(105.8, cls=CLASS_FM)) == KIND_FM)
check("DAB is DAB", kind_for(det(217.5, cls=CLASS_DAB)) == KIND_DAB)

print("")
print("--- sightings ---")
log = ObservationLog()
t = 1000.0
log.record([det(396.0125, 35.0)], t)
log.record([det(396.0125, 33.0)], t + 2)
log.record([det(396.0130, 37.0)], t + 4)      # same channel, measured slightly off
check("one carrier seen repeatedly is one sighting", len(log) == 1,
      "%d" % len(log))
row = log.entries()[0]
check("its hits are counted", row.hits == 3, "%d" % row.hits)
check("the best signal is kept", abs(row.best_snr_db - 37.0) < 1e-6,
      "%.1f" % row.best_snr_db)
check("nearby measurements snap to one channel",
      channel_of(396.0130e6) == channel_of(396.0125e6))

log.record([det(396.0125, 30.0)], t + 200)    # long after it went quiet
check("coming back later is a new sighting", len(log) == 2, "%d" % len(log))

print("")
print("--- bursts against carriers ---")
burst = ObservationLog()
burst.record([det(381.2625, 18.0)], t)
burst.record([det(381.2625, 19.0)], t + 1.0)
carrier = burst.entries()[0]
check("a short transmission reads as a burst", carrier.is_brief(),
      "%.1f s" % carrier.duration_s)
for i in range(20):
    burst.record([det(396.0125, 35.0)], t + i * 1.0)
mast = [s for s in burst.entries() if s.kind == KIND_MAST][0]
check("something that sits there does not", not mast.is_brief(),
      "%.1f s > %.0f" % (mast.duration_s, BRIEF_S))

print("")
print("--- counting ---")
counts = burst.counts()
check("kinds are counted separately",
      counts[KIND_MOBILE] == 1 and counts[KIND_MAST] == 1, str(counts))
check("distinct channels are counted", burst.channels(KIND_MAST) == 1)
check("filtering returns only that kind",
      all(s.kind == KIND_MOBILE for s in burst.entries(KIND_MOBILE)))

print("")
print("--- the exported file ---")
out = Path(tempfile.gettempdir()) / "test_signal_log.csv"
burst.save(out)
lines = out.read_text(encoding="utf-8").strip().splitlines()
header = lines[0].split(",")
check("a header names every column", header == ObservationLog.CSV_HEADER,
      str(len(header)) + " columns")
check("one row per sighting", len(lines) - 1 == len(burst),
      "%d rows" % (len(lines) - 1))
check("the kind is written out", "MOBILE" in lines[1] or "MOBILE" in lines[2])
body = out.read_text(encoding="utf-8").lower()
# The log records RF. It cannot know who is transmitting, so it must never
# suggest that it does.
for word in ("police", "ambulance", "fire", "emergency", "vehicle", "car",
             "user", "subscriber", "identity"):
    if word in body:
        check("the file never claims to know whose radio it is", False, word)
        break
else:
    check("the file never claims to know whose radio it is", True)
out.unlink(missing_ok=True)

print("")
print("--- the panel ---")
panel = LogPanel(burst)
panel.resize(400, 600)
panel.refresh()
check("the summary counts what was seen", "MAST" in panel.summary.text(),
      panel.summary.text())
check("the legend explains the two kinds",
      "uplink" in panel.legend.text() and "downlink" in panel.legend.text())
check("the legend says what it is not",
      "whose" in panel.legend.text(), panel.legend.text()[-40:])
panel._cycle_filter()
check("the filter narrows to one kind", panel.list.filter == KIND_MOBILE,
      panel.list.filter)
check("the button says which", panel.filter_btn.text() == KIND_MOBILE)
panel._clear()
check("clearing empties the log", len(burst) == 0)
check("and the panel says so", "waiting" in panel.summary.text(),
      panel.summary.text())

print("")
print("--- fed by the receiver ---")
from rtlsdr_diag.ui.app_core import AppCore  # noqa: E402
from rtlsdr_diag.sdr.dsp import RawPeak  # noqa: E402

core = AppCore(simulate=True)
core._on_peaks([RawPeak(396.0125e6, -40.0, 20e3, 35.0, -80.0),
                RawPeak(381.2625e6, -60.0, 20e3, 18.0, -80.0)], "Detector")
kinds = {s.kind for s in core.observations.entries()}
check("detections reach the log through the normal path",
      kinds == {KIND_MAST, KIND_MOBILE}, str(kinds))
core.shutdown()

print("")
if failures:
    print("SIGNAL LOG TESTS FAILED (%d):" % len(failures))
    for f in failures:
        print("   - %s" % f)
    sys.exit(1)
print("ALL SIGNAL LOG TESTS PASSED")
