"""Render the Smart Scanner page after a scan, to screenshots/."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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
smart = win.smart_tab

plan = [
    (lambda: win.tabs.setCurrentWidget(smart), 600),
    (lambda: smart.status_strip.button.click(), 16000),
    (lambda: win.tabs.setCurrentWidget(smart), 2500),
    (lambda: win.grab().save(str(OUT / "16_smart_scanner.png")), 400),
    (lambda: smart.btn_source.setChecked(True), 1200),
    (lambda: win.grab().save(str(OUT / "17_smart_source.png")), 400),
]


def go(i=0):
    if i >= len(plan):
        print("detections: %d" % len(win.signal_store), flush=True)
        sel = smart.selected()
        print("selected: %s" % (sel.describe() if sel else "none"), flush=True)
        print("log: %s" % smart.log_line.text(), flush=True)
        print("DONE", flush=True)
        win.close()
        raise SystemExit(0)
    fn, wait = plan[i]
    fn()
    QTimer.singleShot(wait, lambda: go(i + 1))


QTimer.singleShot(1500, lambda: go(0))
sys.exit(app.exec())
