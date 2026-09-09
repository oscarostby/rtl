"""Detected-carrier table."""
from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from ..core.detections import Detection
from . import theme

COLUMNS = ["Frequency", "Level", "SNR", "Bandwidth", "25 kHz channel",
           "Band-plan hint", "Occupancy", "Bursts", "First seen", "Last seen",
           "Status"]


def _fmt_time(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


class DetectionTable(QTableWidget):
    """Shows tracked carriers. Double-click a row to tune to it."""

    tune_requested = Signal(float)      # Hz

    def __init__(self, compact: bool = False, classify=None, parent=None):
        """`classify` maps a frequency to a transmitter-type label, or None."""
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(COLUMNS)
        self._classify = classify
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setSortingEnabled(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        if classify is None:
            self.setColumnHidden(5, True)
        self._show_occupancy = False
        self.setColumnHidden(6, True)
        self.setColumnHidden(7, True)
        if compact:
            for col in (4, 5, 8):
                self.setColumnHidden(col, True)
        self.setToolTip("Detected RF energy only - no demodulation or decoding "
                        "is performed. Double-click a row to tune to it.")
        self.doubleClicked.connect(self._on_double_click)
        self._freqs: list[float] = []

    def _on_double_click(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._freqs):
            self.tune_requested.emit(self._freqs[row])

    def show_occupancy(self, enabled: bool) -> None:
        self._show_occupancy = bool(enabled)
        self.setColumnHidden(6, not enabled)
        self.setColumnHidden(7, not enabled)

    def update_rows(self, detections: list[Detection], raster_hz: float = 25_000.0,
                    active_timeout_s: float = 6.0, passes=None) -> None:
        detections = sorted(detections, key=lambda d: d.freq_hz)
        self._freqs = [d.freq_hz for d in detections]
        self.setRowCount(len(detections))
        for r, d in enumerate(detections):
            active = d.is_active(active_timeout_s)
            cells = [
                "%.4f MHz" % (d.freq_hz / 1e6),
                "%.1f dBFS" % d.level_dbfs,
                "%.1f dB" % d.snr_db,
                "~%.1f kHz" % (d.bandwidth_hz / 1e3),
                "%.4f MHz" % (d.channel_estimate_hz(raster_hz) / 1e6),
                self._classify(d.freq_hz) if self._classify else "",
                ("%.0f%%" % (100.0 * d.occupancy(passes))) if passes else "-",
                str(d.bursts(passes)) if passes else "-",
                _fmt_time(d.first_seen),
                _fmt_time(d.last_seen),
                "Active" if active else "Idle",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c in (1, 2, 3):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif c != 0:
                    item.setTextAlignment(Qt.AlignCenter)
                if c == 10:
                    item.setForeground(QColor(theme.GOOD if active else theme.TEXT_FAINT))
                if c == 5 and text:
                    if text.startswith("Uplink"):
                        item.setForeground(QColor(theme.WARN))
                    elif text.startswith("Downlink"):
                        item.setForeground(QColor(theme.ACCENT))
                    else:
                        item.setForeground(QColor(theme.TEXT_DIM))
                if c == 2:
                    if d.snr_db >= 20:
                        item.setForeground(QColor(theme.GOOD))
                    elif d.snr_db >= 10:
                        item.setForeground(QColor(theme.WARN))
                self.setItem(r, c, item)
