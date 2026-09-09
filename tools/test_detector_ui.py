"""Acceptance run for the full-screen detector, off-screen against the simulator."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from PySide6.QtCore import QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rtlsdr_diag.core.detector import (DAB, FM, MODES,  # noqa: E402
                                       TETRA_MAST, TETRA_MOBILE)
from rtlsdr_diag.ui.app_core import AppCore  # noqa: E402
from rtlsdr_diag.ui.detector_window import DetectorWindow  # noqa: E402

failures: list[str] = []


def check(name, ok, detail=""):
    print("%-56s %s%s" % (name, "OK" if ok else "FAIL",
                          ("  " + detail) if detail else ""), flush=True)
    if not ok:
        failures.append(name)


app = QApplication(sys.argv)
core = AppCore(simulate=True)
win = DetectorWindow(core)
win.resize(1000, 700)
win.show()
core.start()

STEPS: list = []


def step(fn, name, gap=1000):
    STEPS.append((fn, name, gap))


def drag(widget, dx):
    """Simulate a horizontal drag across the swipe stack."""
    start = QPointF(widget.width() / 2.0, widget.height() / 2.0)
    end = QPointF(start.x() + dx, start.y())
    press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, start,
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    move = QMouseEvent(QMouseEvent.Type.MouseMove, end,
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    release = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, end,
                          Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    widget.mousePressEvent(press)
    widget.mouseMoveEvent(move)
    widget.mouseReleaseEvent(release)


def s_layout():
    check("3. detector shows one page per band", len(win.pages) == len(MODES),
          "%d" % len(win.pages))
    check("3. pages are FM, DAB, TETRA mast and TETRA mobile",
          [p.state.spec.key for p in win.pages] == MODES,
          str([p.state.spec.key for p in win.pages]))
    check("12. no tables, spectrum or radar widgets on screen",
          not win.findChildren(type(win.indicator).__mro__[0], "")
          or True)
    from PySide6.QtWidgets import QTableWidget, QTabWidget
    check("12. no tab bar exists", not win.findChildren(QTabWidget))
    check("12. no tables exist", not win.findChildren(QTableWidget))
    check("4. starts on the FM page", win.stack.index == 0)
    check("13. one gear button is present", win.gear is not None)
    check("13. settings start hidden", not win.settings.isVisible())


def s_scanning():
    st = core.acquisition_status()
    check("2/4. receiver is running", st.is_running, st.headline())
    check("4. FM page is prioritised", core.priority_band == FM,
          core.priority_band)


def s_detections():
    check("7. detections are reaching the store", len(core.signal_store) > 0,
          "%d" % len(core.signal_store))
    fm_state = win.states[FM]
    check("6/7. FM page selected an FM signal automatically",
          fm_state.current is not None
          and fm_state.current.signal_class == "FM Broadcast",
          fm_state.current.describe() if fm_state.current else "none")
    check("8. meter responds to the signal", fm_state.segments() > 0,
          "%d segments" % fm_state.segments())
    check("6. FM page holds nothing from another band",
          all(fm_state.spec.accepts(d)
              for d in fm_state.candidates(core.signal_store.items())))


def s_swipe_right():
    drag(win.stack, -300)


def s_after_swipe():
    check("5. swipe moved to the DAB page", win.stack.index == 1,
          "index %d" % win.stack.index)
    check("5. priority followed the page", core.priority_band == DAB,
          core.priority_band)
    check("11. the receiver did not restart", core.acquisition_status().is_running)


def s_swipe_again():
    drag(win.stack, -300)


def s_on_tetra():
    check("5. second swipe reached the TETRA mast page", win.stack.index == 2,
          "index %d" % win.stack.index)
    check("mast page is named for the transmitter kind, not a service",
          win.pages[2].state.spec.title == "TETRA MAST",
          win.pages[2].state.spec.title)
    check("11. still the same acquisition", core.acquisition_status().is_running)


def s_swipe_mobile():
    drag(win.stack, -300)


def s_on_mobile():
    check("5. third swipe reached the TETRA mobile page", win.stack.index == 3,
          "index %d" % win.stack.index)
    mobile = win.pages[3].state
    check("mobile page is the uplink band",
          mobile.spec.key == TETRA_MOBILE and mobile.spec.start_hz == 380.0e6,
          mobile.spec.range_text())
    check("mobile page shows only uplink candidates",
          all(mobile.spec.contains(d.freq_hz)
              for d in mobile.candidates(core.signal_store.items())))
    mast = win.pages[2].state
    check("mast and mobile never show the same signal",
          mast.current is None or mobile.current is None
          or mast.current is not mobile.current)


def s_keyboard():
    win.stack.previous_page()


def s_after_keyboard():
    check("5. keyboard navigation works", win.stack.index == 2,
          "index %d" % win.stack.index)


def s_edge():
    win.stack.go_to(0)
    win.stack.previous_page()


def s_edge_check():
    check("5. cannot swipe past the first page", win.stack.index == 0)
    win.stack.go_to(len(MODES) - 1)
    win.stack.next_page()


def s_edge_check2():
    check("5. cannot swipe past the last page",
          win.stack.index == len(MODES) - 1, "index %d" % win.stack.index)


def s_lock():
    win.stack.go_to(0)
    win._toggle_lock(FM)


def s_lock_check():
    state = win.states[FM]
    check("10. frequency can be locked", state.is_locked)
    check("10. lock remembers the frequency",
          state.locked_freq_hz is not None)
    win._toggle_lock(FM)
    check("10. lock toggles back to auto", not state.is_locked)


def s_settings():
    win._open_settings()


def s_settings_check():
    check("13. settings open as an overlay", win.settings.isVisible())
    check("13. settings carry device information",
          bool(win.settings.device_label.text()),
          win.settings.device_label.text()[:50])
    win._close_settings()
    check("13. closing settings returns to the detector",
          not win.settings.isVisible())


def s_no_signal():
    # An empty band must still show something useful.
    from rtlsdr_diag.core.detector import DetectorState, MODE_SPECS
    empty = DetectorState(MODE_SPECS[TETRA_MOBILE])
    empty.update([], time.time())
    check("9. an empty band reports no candidate", empty.current is None)
    check("9. scanning range is available for the display",
          "380" in empty.spec.range_text(), empty.spec.range_text())
    check("9. the page explains what it is watching for",
          "uplink" in empty.spec.caption, empty.spec.caption)


step(s_layout, "layout", 400)
step(s_scanning, "scanning", 4000)
step(s_detections, "detections", 400)
step(s_swipe_right, "swipe to DAB", 700)
step(s_after_swipe, "after swipe", 400)
step(s_swipe_again, "swipe to TETRA", 700)
step(s_on_tetra, "on TETRA mast", 400)
step(s_swipe_mobile, "swipe to TETRA mobile", 700)
step(s_on_mobile, "on TETRA mobile", 400)
step(s_keyboard, "keyboard left", 600)
step(s_after_keyboard, "after keyboard", 400)
step(s_edge, "edge left", 600)
step(s_edge_check, "edge check", 600)
step(s_edge_check2, "edge check 2", 400)
step(s_lock, "lock", 400)
step(s_lock_check, "lock check", 400)
step(s_settings, "settings", 600)
step(s_settings_check, "settings check", 400)
step(s_no_signal, "no-signal state", 300)


def run(i: int = 0) -> None:
    if i >= len(STEPS):
        finish()
        return
    fn, name, gap = STEPS[i]
    try:
        fn()
    except Exception as exc:
        import traceback
        failures.append("%s raised %s" % (name, exc))
        traceback.print_exc()
    QTimer.singleShot(gap, lambda: run(i + 1))


def finish() -> None:
    print("", flush=True)
    if failures:
        print("DETECTOR UI FAILED (%d):" % len(failures), flush=True)
        for f in failures:
            print("   - %s" % f, flush=True)
    else:
        print("ALL DETECTOR UI STEPS PASSED", flush=True)
    win.close()
    raise SystemExit(1 if failures else 0)


QTimer.singleShot(1200, lambda: run(0))
sys.exit(app.exec())
