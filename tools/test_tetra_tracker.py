"""Deterministic checks for TETRA activity tracking and timeline layout."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from rtlsdr_diag.core.detections import Detection, DetectionTracker  # noqa: E402
from rtlsdr_diag.ui.tab_tetra import _duplex_plan_hint  # noqa: E402
from rtlsdr_diag.ui.tables import DetectionTable  # noqa: E402
from rtlsdr_diag.ui.timeline import (FOOTER_HEIGHT, ROW_GAP, ROW_HEIGHT,  # noqa: E402
                                     ActivityTimeline)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("%-48s %s%s" % (name, "OK" if condition else "FAIL",
                           ("  " + detail) if detail else ""))
    if not condition:
        failures.append(name)


# A single tracked channel is on in passes 1, 2 and 4.  Pass 3 separates the
# final sighting into a second burst.
tracker = DetectionTracker(tolerance_hz=12_500.0)
tracker.begin_pass()
det, is_new = tracker.update(390_012_500.0, -50.0, -80.0, 30.0, 14_000.0)
check("first sighting creates a channel", is_new and det.hits == 1)

# Nearby peaks in one completed sweep are one channel observation.  The
# strongest one must win regardless of iteration order.
same, is_new = tracker.update(390_013_000.0, -60.0, -80.0, 20.0, 10_000.0)
check("weaker duplicate does not add a hit",
      same is det and not is_new and det.hits == 1)
check("weaker duplicate cannot overwrite pass level",
      det.seen_passes[1] == -50.0 and det.level_dbfs == -50.0)
tracker.update(390_012_750.0, -42.0, -79.0, 37.0, 12_000.0)
check("strongest duplicate is retained",
      det.hits == 1 and det.seen_passes[1] == -42.0
      and det.level_dbfs == -42.0)

tracker.begin_pass()
tracker.update(390_012_600.0, -45.0, -80.0, 35.0, 13_000.0)
tracker.begin_pass()                         # pass 3: channel is off
tracker.begin_pass()
tracker.update(390_012_550.0, -47.0, -80.0, 33.0, 13_000.0)
passes = tracker.recent_passes()
check("occupancy counts unique completed passes",
      abs(det.occupancy(passes) - 0.75) < 1e-12,
      "got %.3f" % det.occupancy(passes))
check("bursts count contiguous on-air runs",
      det.bursts(passes) == 2, "got %d" % det.bursts(passes))

# Retaining the bounded pass history must prune per-channel history in lockstep
# so old cells cannot leak into later occupancy windows.
bounded = DetectionTracker()
bounded.MAX_PASS_HISTORY = 3
bounded_det = None
for _ in range(5):
    bounded.begin_pass()
    bounded_det, _ = bounded.update(390_100_000.0, -50.0, -80.0, 30.0, 10_000.0)
check("pass history stays bounded", bounded.recent_passes() == [3, 4, 5])
check("channel history is pruned with pass history",
      bounded_det is not None and sorted(bounded_det.seen_passes) == [3, 4, 5])

app = QApplication.instance() or QApplication([])

# Verify the exact values presented by the table, including qualified duplex
# language rather than a transmitter-identity claim.
table = DetectionTable(classify=_duplex_plan_hint)
table.show_occupancy(True)
table.update_rows([det], passes=passes)
check("table presents occupancy", table.item(0, 6).text() == "75%")
check("table presents burst count", table.item(0, 7).text() == "2")
hint = table.item(0, 5).text()
check("duplex label is explicitly inferred",
      hint == "Downlink-plan band (inferred)", hint)
# 385-390 is uplink spectrum paired with 395-400, so a carrier there is a
# terminal, not something outside the plan.
check("the upper uplink half is recognised as uplink",
      _duplex_plan_hint(387_000_000.0) == "Uplink-plan band (inferred)",
      _duplex_plan_hint(387_000_000.0))
check("a frequency outside TETRA altogether stays neutral",
      _duplex_plan_hint(405_000_000.0) == "Outside paired bands",
      _duplex_plan_hint(405_000_000.0))

# Reproduce the real QScrollArea arrangement with more rows than fit in its
# viewport.  The timeline's minimum height must include every row and the axis
# footer; at the bottom scroll position neither may be clipped.
scroll = QScrollArea()
scroll.setWidgetResizable(True)
scroll.setFrameShape(QScrollArea.NoFrame)
scroll.resize(800, 170)
timeline = ActivityTimeline()
scroll.setWidget(timeline)
rows = []
timeline_passes = list(range(1, 9))
for index in range(14):
    row = Detection(380_000_000.0 + index * 25_000.0,
                    -50.0, -80.0, 30.0, 12_000.0)
    row.seen_passes = {p: -50.0 for p in timeline_passes
                       if (p + index) % 3 == 0}
    rows.append(row)
timeline.set_data(rows, timeline_passes)
scroll.show()
app.processEvents()

row_pitch = ROW_HEIGHT + ROW_GAP
content_height = len(rows) * row_pitch + FOOTER_HEIGHT
check("timeline reserves full content height",
      timeline.minimumHeight() >= content_height,
      "minimum=%d required=%d" % (timeline.minimumHeight(), content_height))
bar = scroll.verticalScrollBar()
check("overflowing timeline is scrollable", bar.maximum() > 0)
bar.setValue(bar.maximum())
app.processEvents()
last_row_bottom = timeline.mapTo(
    scroll.viewport(), QPoint(0, (len(rows) - 1) * row_pitch + ROW_HEIGHT)).y()
footer_bottom = timeline.mapTo(
    scroll.viewport(), QPoint(0, len(rows) * row_pitch + 16)).y()
check("bottom row is fully visible when scrolled down",
      0 <= last_row_bottom <= scroll.viewport().height(),
      "row bottom=%d viewport=%d" % (last_row_bottom,
                                      scroll.viewport().height()))
check("timeline footer is not clipped",
      0 <= footer_bottom <= scroll.viewport().height(),
      "footer bottom=%d viewport=%d" % (footer_bottom,
                                         scroll.viewport().height()))

scroll.close()
table.close()

print("")
if failures:
    print("FAILED: %s" % ", ".join(failures))
    sys.exit(1)
print("ALL TETRA TRACKER TESTS PASSED")
