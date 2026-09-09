"""Render the Listen tab and the TETRA activity timeline to screenshots/."""
from __future__ import annotations

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

plan = [
    ("tetra_tab", lambda: win.tabs.setCurrentWidget(win.tetra_tab), 400),
    ("tetra_go", lambda: win.tetra_tab._toggle(), 22000),
    ("shot_tetra", lambda: win.grab().save(str(OUT / "12_tetra_timeline.png")), 400),
    ("tetra_stop", lambda: win.tetra_tab._toggle(), 800),
    ("listen_tab", lambda: win.tabs.setCurrentWidget(win.listen_tab), 400),
    ("listen_tune", lambda: win.listen_tab.freq.setValue(100.1), 400),
    ("listen_go", lambda: win.listen_tab._toggle(), 5000),
    ("shot_listen", lambda: win.grab().save(str(OUT / "13_listen.png")), 400),
    ("listen_stop", lambda: win.listen_tab._toggle(), 600),
]


def go(i: int = 0) -> None:
    if i >= len(plan):
        print("tetra rows: %d, passes: %d"
              % (len(win.tetra_tab.tracker.items()),
                 len(win.tetra_tab.tracker.recent_passes())), flush=True)
        print("audio: %s" % win.listen_tab.player.stats(), flush=True)
        print("DONE", flush=True)
        win.close()
        QTimer.singleShot(500, app.quit)
        return
    name, fn, wait = plan[i]
    fn()
    if name.startswith("shot"):
        print("saved %s" % name, flush=True)
    QTimer.singleShot(wait, lambda: go(i + 1))


QTimer.singleShot(1500, lambda: go(0))
sys.exit(app.exec())
