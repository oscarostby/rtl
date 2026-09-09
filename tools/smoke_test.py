"""Headless self-test: builds the whole GUI offscreen and drives every tab.

Run from the project root:

    python tools/smoke_test.py

It uses simulation mode, so no RTL-SDR hardware is required. Any exception
raised anywhere in the UI or the acquisition thread fails the run.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from rtlsdr_diag.ui import theme  # noqa: E402
from rtlsdr_diag.ui.main_window import MainWindow  # noqa: E402

errors: list[str] = []
dialogs: list[str] = []

# Modal dialogs would block a headless run forever - record them instead.
for _name in ("warning", "critical", "information", "about", "question"):
    def _stub(*args, _n=_name, **kwargs):
        text = next((a for a in args if isinstance(a, str)), "")
        dialogs.append("%s: %s" % (_n, text.replace("\n", " ")[:160]))
        print("DIALOG[%s]: %s" % (_n, text.replace("\n", " ")[:160]), flush=True)
        return QMessageBox.StandardButton.Ok
    setattr(QMessageBox, _name, staticmethod(_stub))


snapshots: list[tuple[str, str]] = []


def snapshot(name: str) -> None:
    body = [
        "meter current=%s avg=%s max=%s min=%s nf=%s snr=%s" % (
            win.meter_tab.card_cur.value_label.text(),
            win.meter_tab.card_avg.value_label.text(),
            win.meter_tab.card_max.value_label.text(),
            win.meter_tab.card_min.value_label.text(),
            win.meter_tab.card_nf.value_label.text(),
            win.meter_tab.card_snr.value_label.text()),
        "listen: %s | level %s | audio %s" % (
            win.listen_tab.btn_listen.text(), win.listen_tab.lbl_level.text(),
            win.listen_tab.player.stats()),
        "tetra timeline rows=%d passes=%d" % (
            len(win.tetra_tab.tracker.items()),
            len(win.tetra_tab.tracker.recent_passes())),
        "radar coverage: %d point(s), level range %s" % (
            len(win.radar_tab.coverage), win.radar_tab.coverage.level_range()),
        "radar: %d site(s), %d bearing(s), %d fix(es), %d rose point(s) - %s" % (
            len(win.radar_tab.store.sites), len(win.radar_tab.store.bearings),
            len(win.radar_tab._fixes), len(win.radar_tab._sweep),
            win.radar_tab.status.text()[:120]),
        "tetra rows=%d  scanner rows=%d  dashboard rows=%d" % (
            win.tetra_tab.table.rowCount(), win.scanner_tab.table.rowCount(),
            win.dashboard_tab.table.rowCount()),
        "tetra count label: %s" % win.tetra_tab.lbl_count.text(),
        "scanner count label: %s" % win.scanner_tab.lbl_count.text(),
        win.dashboard_tab.test_output.toPlainText(),
    ]
    snapshots.append((name, chr(10).join(body)))


def _excepthook(exc_type, exc, tb):
    errors.append("".join(traceback.format_exception(exc_type, exc, tb)))
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _excepthook

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(theme.STYLESHEET)
theme.apply_pyqtgraph_defaults()

win = MainWindow(start_simulated=True)
win.resize(1440, 920)
win.show()

# Steps run one after another: each schedules the next only when it has
# actually run, so a slow redraw delays the schedule instead of collapsing
# several steps into one burst.
STEPS: list[tuple] = []


def later(fn, name, gap=1200):
    STEPS.append((fn, name, gap))


def _run_steps(index: int = 0) -> None:
    if index >= len(STEPS):
        QTimer.singleShot(2500, finish)
        return
    fn, name, gap = STEPS[index]
    print("STEP: %s" % name, flush=True)
    try:
        fn()
    except Exception:
        errors.append("step %s:" % name)
        errors.append(traceback.format_exc())
        traceback.print_exc()
    QTimer.singleShot(gap, lambda: _run_steps(index + 1))


later(lambda: win.engine_start(win.spectrum_tab.config()), "start spectrum", 600)
later(lambda: win.tabs.setCurrentWidget(win.spectrum_tab), "show spectrum")
later(lambda: win.tabs.setCurrentWidget(win.waterfall_tab), "show waterfall")
later(lambda: win.spectrum_tab.controls.center.setValue(390.5), "tune 390.5 MHz")
later(lambda: win.spectrum_tab.controls.agc.setChecked(False), "manual gain")
later(lambda: win.spectrum_tab.controls.fft.setCurrentIndex(4), "fft 8192")
later(lambda: win.tabs.setCurrentWidget(win.meter_tab), "show signal meter")
later(lambda: win.meter_tab._toggle(), "meter stop")
later(lambda: win.meter_tab._toggle(), "meter start")
later(lambda: snapshot("signal meter"), "snapshot meter", 2500)
later(lambda: win.tabs.setCurrentWidget(win.tetra_tab), "show TETRA tab")
later(lambda: win.tetra_tab._toggle(), "stop current job")
later(lambda: win.tetra_tab._toggle(), "start TETRA sweep")
later(lambda: win.start_logging("smoke"), "start CSV logging", 8000)
later(lambda: snapshot("TETRA sweep"), "snapshot tetra", 5000)
later(lambda: win.tabs.setCurrentWidget(win.scanner_tab), "show scanner")
later(lambda: win.scanner_tab.preset.setCurrentIndex(1), "scanner preset: FM broadcast")
later(lambda: win.scanner_tab._toggle(), "stop tetra sweep")
later(lambda: win.scanner_tab._toggle(), "start FM scan")
later(lambda: snapshot("FM scan"), "snapshot scanner", 9000)
later(win.stop_logging, "stop CSV logging")
later(lambda: win.tabs.setCurrentWidget(win.listen_tab), "show listen")
later(lambda: win.listen_tab.mode.setCurrentIndex(0), "listen: WFM")
later(lambda: win.listen_tab.freq.setValue(100.1), "listen: tune 100.1")
later(lambda: win.listen_tab._toggle(), "listen: start audio", 4000)
later(lambda: snapshot("listen"), "snapshot listen", 500)
later(lambda: win.listen_tab.mode.setCurrentIndex(1), "listen: switch to NFM", 3000)
later(lambda: win.listen_tab._toggle(), "listen: stop audio")
later(lambda: win.tabs.setCurrentWidget(win.radar_tab), "show radar")
later(lambda: win.radar_tab.lat.setValue(59.9139), "radar: set latitude")
later(lambda: win.radar_tab.lon.setValue(10.7522), "radar: set longitude")
later(lambda: win.radar_tab._add_site(), "radar: add site")
later(lambda: win.radar_tab.range_box.setCurrentIndex(0), "radar: range 1 km")
later(lambda: win.radar_tab.range_box.setCurrentIndex(6), "radar: range 100 km")
later(lambda: win.radar_tab.range_box.setCurrentIndex(3), "radar: range 10 km")
later(lambda: win.radar_tab.btn_help.setChecked(True), "radar: open help")
later(lambda: win.radar_tab.btn_help.setChecked(False), "radar: close help")
later(lambda: win.radar_tab.live_panel.sort_mode.setCurrentIndex(1),
      "radar: sort live by frequency")
later(lambda: win.radar_tab.live_panel.chk_active.setChecked(True),
      "radar: live active-only filter")
later(lambda: win.radar_tab.live_panel.chk_active.setChecked(False),
      "radar: live filter off")
later(lambda: win.radar_tab.live_panel.class_filter.setCurrentIndex(6),
      "radar: filter to TETRA-like")
later(lambda: win.radar_tab.live_panel.class_filter.setCurrentIndex(0),
      "radar: all classes")
later(lambda: win.radar_tab.bearing.setValue(35.0), "radar: bearing 35")
later(lambda: win.radar_tab._record_bearing(), "radar: record bearing 1")
later(lambda: win.radar_tab.lon.setValue(10.9000), "radar: move east")
later(lambda: win.radar_tab.bearing.setValue(320.0), "radar: bearing 320")
later(lambda: win.radar_tab._record_bearing(), "radar: record bearing 2")
later(lambda: win.radar_tab._triangulate(), "radar: triangulate")
later(lambda: win.radar_tab._add_sweep_point(), "radar: sweep point")
later(lambda: win.radar_tab._record_coverage(), "radar: coverage point 1")
later(lambda: win.radar_tab.lat.setValue(59.9500), "radar: move north")
later(lambda: win.radar_tab._record_coverage(), "radar: coverage point 2")
later(lambda: win.radar_tab.lon.setValue(11.0500), "radar: move east again")
later(lambda: win.radar_tab._record_coverage(), "radar: coverage point 3")
later(lambda: snapshot("radar"), "snapshot radar", 500)
later(lambda: win.tabs.setCurrentWidget(win.diagnostics_tab), "show diagnostics")
later(win.diagnostics_tab.copy_diagnostics, "copy diagnostics")
later(lambda: win.tabs.setCurrentWidget(win.dashboard_tab), "show dashboard")
later(win.run_hardware_test, "hardware test", 800)
later(lambda: snapshot("hardware test"), "snapshot hardware test", 5000)
later(win.run_antenna_test, "antenna test (antenna connected)")
later(lambda: snapshot("antenna test - connected"), "snapshot antenna ok", 7000)
later(lambda: win.set_sim_antenna(False), "simulate antenna disconnected")
later(win.run_antenna_test, "antenna test (disconnected)", 800)
later(lambda: snapshot("antenna test - disconnected"), "snapshot antenna bad", 7000)
later(lambda: win.tune_to(390.0125e6), "tune_to() from a table row")


def finish() -> None:
    print("=" * 70, flush=True)
    print("Dashboard status : %r / %r" % (win.dashboard_tab.pill.label.text(),
                                          win.dashboard_tab.pill.sub.text()))
    print("Status bar       : %s | %s | %s" % (win.status_device.text(),
                                               win.status_mode.text(),
                                               win.status_log.text()))
    print("TETRA rows       : %d" % win.tetra_tab.table.rowCount())
    print("Scanner rows     : %d" % win.scanner_tab.table.rowCount())
    print("Dashboard rows   : %d" % win.dashboard_tab.table.rowCount())
    print("Meter current    : %s dBFS" % win.meter_tab.card_cur.value_label.text())
    print("Meter SNR        : %s dB" % win.meter_tab.card_snr.value_label.text())
    print("-" * 70)
    for name, body in snapshots:
        print("#" * 70)
        print("SNAPSHOT: %s" % name)
        print("#" * 70)
        print(body[:3500])
    print("=" * 70)
    print("dialogs raised: %s" % (dialogs or "none"))
    win.close()
    QTimer.singleShot(600, app.quit)


QTimer.singleShot(600, lambda: _run_steps(0))

rc = app.exec()
print("Qt exit code: %s" % rc)
if errors:
    print("FAILED - %d error(s)" % len(errors))
    for e in errors:
        print(e)
    sys.exit(2)
print("SMOKE TEST PASSED - no exceptions raised")
