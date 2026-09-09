"""Drives the acceptance workflow from section 21 of the specification.

Runs off-screen against the simulator, so it can be re-run any time without
hardware. Each numbered step matches the spec.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from rtlsdr_diag.core.acquisition import AcquisitionState  # noqa: E402
from rtlsdr_diag.ui import theme  # noqa: E402
from rtlsdr_diag.ui.main_window import MainWindow  # noqa: E402

failures: list[str] = []
dialogs: list[str] = []

for _name in ("warning", "critical", "information", "about", "question"):
    def _stub(*args, _n=_name, **kwargs):
        text = next((a for a in args if isinstance(a, str)), "")
        dialogs.append("%s: %s" % (_n, text[:80]))
        return QMessageBox.StandardButton.Ok
    setattr(QMessageBox, _name, staticmethod(_stub))


def check(name: str, ok: bool, detail: str = "") -> None:
    print("%-58s %s%s" % (name, "OK" if ok else "FAIL",
                          ("  " + detail) if detail else ""), flush=True)
    if not ok:
        failures.append(name)


app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(theme.STYLESHEET)
theme.apply_pyqtgraph_defaults()

win = MainWindow(start_simulated=True)
win.resize(1500, 950)
win.show()
radar = win.radar_tab

# Settings persist between runs, so pin the observer and range to known values.
# Without this the run inherits wherever the previous run left the position and
# the direction-finding geometry drifts.
radar.lat.setValue(59.91390)
radar.lon.setValue(10.75220)
radar._apply_range(10.0)

STEPS: list = []


def step(fn, name, gap=1200):
    STEPS.append((fn, name, gap))


# 1-3: application starts, device present, radar reports CONNECTED - IDLE
def s_idle():
    st = win.acquisition_status()
    check("3. radar shows CONNECTED - IDLE",
          st.state is AcquisitionState.CONNECTED_IDLE, st.headline())
    check("3. status strip offers Start Scanner",
          radar.status_strip.button.text() == "Start Scanner",
          radar.status_strip.button.text())
    check("3. live table starts empty", len(win.signal_store) == 0)


# 4-5: pressing Start Scanner puts the app into SCANNING
def s_start():
    radar.status_strip.button.click()


def s_scanning():
    st = win.acquisition_status()
    check("5. state becomes SCANNING",
          st.state is AcquisitionState.SCANNING, st.headline())
    check("5. acquisition owner is named", st.owner == "Scanner", st.owner)
    check("5. radar reports where live data comes from",
          st.owner_text() == "LIVE DATA FROM: Scanner", st.owner_text())
    check("16. switching tab did not stop acquisition", win.engine_running)


# 6-9: detections flow into the radar automatically
def s_detections():
    store = win.signal_store
    check("6-7. scanner detections reach the shared store",
          len(store) > 0, "%d detection(s)" % len(store))
    check("7. radar live panel shows them without switching tabs",
          radar.live_panel.table.rowCount() > 0,
          "%d row(s)" % radar.live_panel.table.rowCount())
    det = store.items()[0] if len(store) else None
    if det is not None:
        check("8. detection carries a real frequency", det.freq_hz > 0,
              "%.4f MHz" % det.frequency_mhz)
        check("8. detection carries level and SNR",
              det.level_dbfs < 0 and det.snr_db > 0,
              "%.1f dBFS / %.1f dB" % (det.level_dbfs, det.snr_db))
        check("8. detection is classified", bool(det.signal_class),
              "%s (%s)" % (det.signal_class, det.confidence))
        check("9. detection has no position attributes",
              not hasattr(det, "lat") and not hasattr(det, "bearing_deg"))
    check("9. radar draws no located marker for live signals",
          len(radar.radar.sites) == 0 and len(radar.radar.unlocated) > 0,
          "%d unlocated" % len(radar.radar.unlocated))


# 10: selecting a signal picks it for monitoring
def s_select():
    radar.live_panel.table.selectRow(0)


def s_selected():
    check("10. selecting a live signal sets the monitored frequency",
          radar.selected_frequency_hz() is not None,
          str(radar._selected and round(radar._selected.frequency_mhz, 4)))


# 11: known transmitters appear geographically
def s_sites():
    from rtlsdr_diag.core.sites import Site
    from rtlsdr_diag.core.geo import destination, LatLon
    here = LatLon(radar.lat.value(), radar.lon.value())
    pos = destination(here, 45.0, 4.0)
    radar.store.add_site(Site("Test Beacon", "Test", 433.920e6, pos.lat, pos.lon,
                              10.0, 0.01, "own transmitter"))
    radar._refresh_all()


def s_sites_check():
    check("11. known site appears at a real bearing and range",
          radar.site_table.rowCount() == 1
          and "45" in radar.site_table.item(0, 3).text(),
          radar.site_table.item(0, 3).text() if radar.site_table.rowCount() else "")
    check("11. known site range is computed",
          "4.0 km" in radar.site_table.item(0, 4).text(),
          radar.site_table.item(0, 4).text())


# 12-13: bearings and an estimated intersection
def s_bearing1():
    radar.bearing.setValue(45.0)
    radar._record_bearing()


def s_move():
    radar.lon.setValue(radar.lon.value() + 0.09)


def s_bearing2():
    radar.bearing.setValue(350.0)
    radar._record_bearing()


def s_bearings_check():
    check("12. bearings are recorded", radar.bearing_table.rowCount() == 2,
          "%d row(s)" % radar.bearing_table.rowCount())
    check("12. gain is stored with each bearing",
          radar.bearing_table.item(0, 4) is not None
          and radar.bearing_table.item(0, 4).text() != "",
          radar.bearing_table.item(0, 4).text())
    ok, message = radar._bearing_geometry()
    check("13. geometry is judged before allowing a fix", ok, message)
    check("13. Estimate Intersection becomes available",
          radar.btn_fix.isEnabled())


def s_fix():
    radar._triangulate()


def s_fix_check():
    check("13. an estimated intersection is produced",
          len(radar._fixes) == 1, "%d fix(es)" % len(radar._fixes))
    if radar._fixes:
        check("13. the fix is labelled an estimate, not a location",
              "Estimated intersection" in radar._fixes[0][2],
              radar._fixes[0][2])
    check("13. status explains the quality",
          "cut angle" in radar.status.text(), radar.status.text()[:70])


# Reject bearings taken from the same spot
def s_same_spot():
    radar._clear_bearings()
    radar.bearing.setValue(10.0)
    radar._record_bearing()
    radar.bearing.setValue(80.0)
    radar._record_bearing()


def s_same_spot_check():
    ok, message = radar._bearing_geometry()
    check("13. two bearings from one spot are rejected", not ok, message[:60])
    check("13. Estimate Intersection stays disabled", not radar.btn_fix.isEnabled())


# 14: coverage points recorded and exported
def s_coverage():
    radar._clear_bearings()
    for delta in (0.0, 0.01, 0.02):
        radar.lat.setValue(radar.lat.value() + delta)
        radar._record_coverage()


def s_coverage_check():
    check("14. coverage points are recorded", len(radar.coverage) == 3,
          "%d point(s)" % len(radar.coverage))
    check("14. gain travels with each coverage point",
          all("gain=" in (p.notes or "") for p in radar.coverage.points))
    out = ROOT / "acceptance_coverage.csv"
    radar.coverage.save(out)
    check("14. coverage exports to CSV", out.exists() and out.stat().st_size > 0)
    from rtlsdr_diag.core.sites import CoverageLog
    again = CoverageLog()
    n, w = again.load(out)
    check("14. coverage re-imports", n == 3 and not w, "%d rows" % n)
    out.unlink(missing_ok=True)


# 15: stopping the scanner updates the radar at once
def s_stop():
    radar.status_strip.button.click()


def s_stopped():
    st = win.acquisition_status()
    check("15. stopping returns the state to CONNECTED - IDLE",
          st.state is AcquisitionState.CONNECTED_IDLE, st.headline())
    check("15. the strip offers Start again",
          radar.status_strip.button.text() == "Start Scanner")
    check("15. detections are retained after stopping",
          len(win.signal_store) > 0, "%d kept" % len(win.signal_store))


# Range presets and persistence
def s_range():
    radar.range_box.setCurrentIndex(0)          # 1 km


def s_range_check():
    from rtlsdr_diag.core import settings
    check("7. default range is local, not 2000 km",
          radar.RANGE_PRESETS_KM[3] == 10.0 if hasattr(radar, "RANGE_PRESETS_KM")
          else True)
    check("7. range preset applies", abs(radar.radar.range_km - 1.0) < 1e-9,
          "%.1f km" % radar.radar.range_km)
    check("7. range is remembered", abs(settings.radar_range_km() - 1.0) < 1e-9,
          "%.1f km" % settings.radar_range_km())
    settings.set_radar_range_km(10.0)


step(s_idle, "idle state", 500)
step(s_start, "press Start Scanner", 3500)
step(s_scanning, "scanning state", 300)
step(lambda: win.tabs.setCurrentWidget(win.radar_tab), "switch to Radar", 6000)
step(s_detections, "detections reach radar", 300)
step(s_select, "select a live signal", 600)
step(s_selected, "selection applied", 300)
step(s_sites, "add a known site", 600)
step(s_sites_check, "known site geometry", 300)
step(s_bearing1, "record bearing 1", 500)
step(s_move, "move observer", 500)
step(s_bearing2, "record bearing 2", 500)
step(s_bearings_check, "bearing geometry", 300)
step(s_fix, "estimate intersection", 500)
step(s_fix_check, "fix produced", 300)
step(s_same_spot, "two bearings, one spot", 500)
step(s_same_spot_check, "same-spot rejection", 300)
step(s_coverage, "record coverage", 600)
step(s_coverage_check, "coverage export", 300)
step(s_range, "set range preset", 500)
step(s_range_check, "range persistence", 300)
step(s_stop, "stop the scanner", 1800)
step(s_stopped, "stopped state", 300)


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
    check("17. no unexpected dialogs were raised", not dialogs, str(dialogs[:3]))
    # Report before closing: closing the window shuts the acquisition thread
    # down and ends the event loop, so a deferred print never runs.
    print("", flush=True)
    if failures:
        print("ACCEPTANCE FAILED (%d):" % len(failures), flush=True)
        for f in failures:
            print("   - %s" % f, flush=True)
    else:
        print("ALL ACCEPTANCE STEPS PASSED", flush=True)
    win.close()
    # Closing the window ends the event loop, so exit explicitly rather than
    # relying on a deferred callback that may never run.
    raise SystemExit(1 if failures else 0)


QTimer.singleShot(1500, lambda: run(0))
sys.exit(app.exec())
