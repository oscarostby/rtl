"""Launch the real (on-screen) window in simulation mode and save a PNG per tab.

    python tools/capture_tabs.py [output_dir]

Used to eyeball the UI without a person having to click through it.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rtlsdr_diag.ui import theme  # noqa: E402
from rtlsdr_diag.ui.main_window import MainWindow  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "screenshots")
OUT.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(theme.STYLESHEET)
theme.apply_pyqtgraph_defaults()

win = MainWindow(start_simulated=True)
win.resize(1500, 950)
win.show()

plan = [
    ("start", lambda: win.engine_start(win.spectrum_tab.config()), 2500),
    ("tune", lambda: win.spectrum_tab.controls.center.setValue(390.5), 5000),
    ("01_spectrum", lambda: win.tabs.setCurrentWidget(win.spectrum_tab), 4000),
    ("02_waterfall", lambda: win.tabs.setCurrentWidget(win.waterfall_tab), 12000),
    ("meter_tab", lambda: win.tabs.setCurrentWidget(win.meter_tab), 300),
    ("meter_freq", lambda: win.meter_tab.freq.setValue(390.0125), 300),
    ("meter_go", lambda: win.meter_tab._toggle(), 8000),
    ("03_meter", lambda: None, 300),
    ("tetra_tab", lambda: win.tabs.setCurrentWidget(win.tetra_tab), 300),
    ("tetra_go", lambda: win.tetra_tab._toggle(), 16000),
    ("04_tetra", lambda: None, 300),
    ("scanner_tab", lambda: win.tabs.setCurrentWidget(win.scanner_tab), 300),
    ("scanner_preset", lambda: win.scanner_tab.preset.setCurrentIndex(1), 400),
    ("scanner_go", lambda: win.scanner_tab._toggle(), 18000),
    ("05_scanner", lambda: None, 300),
    ("dash_tab", lambda: win.tabs.setCurrentWidget(win.dashboard_tab), 300),
    ("hwtest", lambda: win.run_hardware_test(), 5000),
    ("06_dashboard_hwtest", lambda: None, 300),
    ("anttest", lambda: win.run_antenna_test(), 7000),
    ("07_dashboard_antenna", lambda: None, 300),
    ("diag_tab", lambda: win.tabs.setCurrentWidget(win.diagnostics_tab), 1500),
    ("08_diagnostics", lambda: None, 300),
]


def shot(name: str) -> None:
    path = OUT / ("%s.png" % name)
    win.grab().save(str(path))
    print("saved %s" % path, flush=True)


def run(i: int = 0) -> None:
    if i >= len(plan):
        print("DONE", flush=True)
        win.close()
        QTimer.singleShot(500, app.quit)
        return
    name, fn, wait = plan[i]
    fn()
    if name[0].isdigit():
        QTimer.singleShot(250, lambda: shot(name))
    QTimer.singleShot(wait, lambda: run(i + 1))


QTimer.singleShot(1500, lambda: run(0))
sys.exit(app.exec())
