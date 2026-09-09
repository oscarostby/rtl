"""Render the Radar tab with a synthetic scene, to eyeball the drawing.

The sites here are generated at known bearings and ranges from the centre so
the picture can be checked against arithmetic. They are not real transmitters.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rtlsdr_diag.core.geo import LatLon, destination  # noqa: E402
from rtlsdr_diag.core.sites import BearingRecord, Site  # noqa: E402
from rtlsdr_diag.ui import theme  # noqa: E402
from rtlsdr_diag.ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "screenshots"
OUT.mkdir(exist_ok=True)

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(theme.STYLESHEET)
theme.apply_pyqtgraph_defaults()

win = MainWindow(start_simulated=True)
win.resize(1500, 950)
win.show()

HERE = LatLon(59.9139, 10.7522)
tab = win.radar_tab

# name, kind, MHz, bearing, km, mast m, "heard" SNR (None = silent)
SCENE = [
    ("Site A (test)", "DAB", 222.064, 12.0, 18.0, 300, 34.0),
    ("Site B (test)", "FM", 99.300, 75.0, 41.0, 220, 21.0),
    ("Site C (test)", "FM", 101.900, 148.0, 27.0, 180, None),
    ("Site D (test)", "TETRA site", 390.0125, 205.0, 9.5, 60, 26.0),
    ("Site E (test)", "TETRA site", 392.9125, 262.0, 33.0, 90, None),
    ("Site F (test)", "Amateur", 145.600, 318.0, 55.0, 120, 12.0),
    ("Site G (test)", "TV", 482.000, 340.0, 72.0, 400, None),
]


def build() -> None:
    tab.lat.setValue(HERE.lat)
    tab.lon.setValue(HERE.lon)
    tab.range_slider.setValue(tab._km_to_slider(90.0))
    for name, kind, mhz, brg, km, mast, snr in SCENE:
        pos = destination(HERE, brg, km)
        site = Site(name=name, kind=kind, freq_hz=mhz * 1e6,
                    lat=pos.lat, lon=pos.lon, height_m=mast,
                    notes="synthetic test scene")
        if snr is not None:
            site.snr_db = snr
            site.level_dbfs = -80.0 + snr
            site.last_heard = time.time()
        tab.store.add_site(site)

    # Two bearings from different places that cross near Site D.
    target = destination(HERE, 205.0, 9.5)
    obs2 = destination(HERE, 270.0, 6.0)
    from rtlsdr_diag.core.geo import bearing_deg
    tab.store.add_bearing(BearingRecord(
        label="390.0125 MHz", freq_hz=390.0125e6, lat=HERE.lat, lon=HERE.lon,
        bearing_deg=bearing_deg(HERE, target), level_dbfs=-46.0))
    tab.store.add_bearing(BearingRecord(
        label="390.0125 MHz", freq_hz=390.0125e6, lat=obs2.lat, lon=obs2.lon,
        bearing_deg=bearing_deg(obs2, target), level_dbfs=-44.0))

    # A short coverage survey: a few stops with different reception at each.
    from rtlsdr_diag.core.sites import CoveragePoint
    for brg, km, lvl in ((30, 2.0, -38.0), (30, 4.5, -44.0), (75, 3.0, -51.0),
                         (120, 5.0, -63.0), (200, 2.5, -41.0), (250, 6.0, -70.0),
                         (300, 4.0, -58.0), (340, 7.5, -66.0), (10, 6.5, -47.0)):
        pos = destination(HERE, brg, km)
        tab.coverage.add(CoveragePoint(lat=pos.lat, lon=pos.lon,
                                       freq_hz=390.0125e6, level_dbfs=lvl,
                                       noise_dbfs=-80.0, snr_db=lvl + 80.0))

    # A measured antenna pattern peaking toward Site D.
    import math
    tab._sweep = [(b, -78.0 + 30.0 * max(0.0, math.cos(math.radians(b - 205.0))) ** 3)
                  for b in range(0, 360, 10)]
    tab._refresh_all()


def go(step: int = 0) -> None:
    if step == 0:
        win.tabs.setCurrentWidget(tab)
    elif step == 1:
        build()
    elif step == 2:
        tab._triangulate()
    elif step == 3:
        win.grab().save(str(OUT / "09_radar.png"))
        print("saved 09_radar.png", flush=True)
        print("status: %s" % tab.status.text(), flush=True)
    elif step == 4:
        tab.range_slider.setValue(tab._km_to_slider(9.0))
    elif step == 5:
        win.grab().save(str(OUT / "10_radar_zoom.png"))
        print("saved 10_radar_zoom.png", flush=True)
    elif step == 6:
        win.tabs.setCurrentWidget(win.tetra_tab)
        win.tetra_tab._toggle()
    elif step in (7, 8, 9, 10, 11, 12):
        pass                      # let the sweep collect uplink and downlink
    elif step == 13:
        win.grab().save(str(OUT / "11_tetra_duplex.png"))
        print("saved 11_tetra_duplex.png", flush=True)
        print("tetra rows: %d" % win.tetra_tab.table.rowCount(), flush=True)
        win.close()
        QTimer.singleShot(400, app.quit)
        return
    QTimer.singleShot(2200 if step >= 6 else 1200, lambda: go(step + 1))


QTimer.singleShot(1500, lambda: go(0))
sys.exit(app.exec())
