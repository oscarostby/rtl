"""Render the three detector pages to screenshots/."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from rtlsdr_diag.ui.app_core import AppCore
from rtlsdr_diag.ui.detector_window import DetectorWindow

OUT = ROOT / "screenshots"
OUT.mkdir(exist_ok=True)

app = QApplication(sys.argv)
core = AppCore(simulate=True)
win = DetectorWindow(core)
win.resize(1100, 720)
win.show()
core.start()

plan = [
    (lambda: None, 14000),
    (lambda: win.grab().save(str(OUT / "18_detector_fm.png")), 400),
    (lambda: win.stack.go_to(1), 9000),
    (lambda: win.grab().save(str(OUT / "19_detector_dab.png")), 400),
    (lambda: win.stack.go_to(2), 12000),
    (lambda: win.grab().save(str(OUT / "20_detector_mast.png")), 400),
    (lambda: win.stack.go_to(3), 14000),
    (lambda: win.grab().save(str(OUT / "21_detector_mobile.png")), 400),
]


def go(i=0):
    if i >= len(plan):
        for key, st in win.states.items():
            print("%-6s %s" % (key, st.current.describe() if st.current else "no candidate"), flush=True)
        print("DONE", flush=True)
        win.close()
        raise SystemExit(0)
    fn, wait = plan[i]
    fn()
    QTimer.singleShot(wait, lambda: go(i + 1))


QTimer.singleShot(1200, lambda: go(0))
sys.exit(app.exec())
