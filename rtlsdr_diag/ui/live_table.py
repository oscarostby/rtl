"""The LIVE RF DETECTIONS panel: what the receiver is hearing, right now."""
from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..core.models import SIGNAL_CLASSES
from . import theme, widgets

COLUMNS = ["Active", "Frequency", "Type", "Level", "SNR", "Bandwidth",
           "Confidence", "Source", "Last seen"]

SORT_STRENGTH = "Signal strength"
SORT_FREQUENCY = "Frequency"
SORT_LAST_SEEN = "Last seen"
SORT_MODES = [SORT_STRENGTH, SORT_FREQUENCY, SORT_LAST_SEEN]

CLASS_COLORS = {
    "FM Broadcast": theme.PEAK,
    "Airband": "#a06cd5",
    "DAB": theme.ACCENT,
    "Amateur": theme.BAD,
    "Marine VHF": "#4fd1c5",
    "ISM": "#d9a441",
    "TETRA-like": theme.TRACE,
    "Unknown": theme.TEXT_FAINT,
}


class LiveDetectionPanel(QWidget):
    """Sortable, filterable view of the shared signal store."""

    selection_changed = Signal(object)     # LiveSignalDetection or None
    tune_requested = Signal(float)         # Hz
    clear_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._detections: list = []
        self._shown: list = []
        self._selected_id = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("Sort"))
        self.sort_mode = QComboBox()
        for mode in SORT_MODES:
            self.sort_mode.addItem(mode, mode)
        self.sort_mode.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.sort_mode)

        bar.addWidget(QLabel("Class"))
        self.class_filter = QComboBox()
        self.class_filter.addItem("All classes", None)
        for name in SIGNAL_CLASSES:
            self.class_filter.addItem(name, name)
        self.class_filter.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.class_filter)

        self.chk_active = QCheckBox("Active only")
        self.chk_active.setChecked(False)
        self.chk_active.toggled.connect(self.refresh)
        bar.addWidget(self.chk_active)

        bar.addWidget(QLabel("Stale after"))
        self.stale = QDoubleSpinBox()
        self.stale.setRange(2.0, 600.0)
        self.stale.setValue(15.0)
        self.stale.setSuffix(" s")
        self.stale.setToolTip("How long without a sighting before a signal "
                              "stops counting as active.")
        self.stale.valueChanged.connect(self.refresh)
        bar.addWidget(self.stale)

        self.btn_clear = QPushButton("Clear detections")
        self.btn_clear.clicked.connect(self.clear_requested.emit)
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)

        self.count = QLabel("0 signals")
        self.count.setStyleSheet("font-weight: 600;")
        bar.addWidget(self.count)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(190)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.doubleClicked.connect(self._on_double_click)
        root.addWidget(self.table, 1)

        self.hint = widgets.dim_label(
            "Click a signal to monitor it; double-click to tune the Signal "
            "Meter to it. Levels are dBFS - relative to the receiver's full "
            "scale, not calibrated to dBm.")
        root.addWidget(self.hint)

    # ------------------------------------------------------------------
    def stale_timeout_s(self) -> float:
        return float(self.stale.value())

    def set_detections(self, detections) -> None:
        self._detections = list(detections)
        self.refresh()

    def selected(self):
        for det in self._shown:
            if det.id == self._selected_id:
                return det
        return None

    # ------------------------------------------------------------------
    def _filtered(self) -> list:
        timeout = self.stale_timeout_s()
        wanted = self.class_filter.currentData()
        items = list(self._detections)
        if wanted:
            items = [d for d in items if d.signal_class == wanted]
        if self.chk_active.isChecked():
            items = [d for d in items if d.is_active(timeout)]
        mode = self.sort_mode.currentData()
        if mode == SORT_FREQUENCY:
            items.sort(key=lambda d: d.freq_hz)
        elif mode == SORT_LAST_SEEN:
            items.sort(key=lambda d: d.last_seen, reverse=True)
        else:
            items.sort(key=lambda d: d.level_dbfs, reverse=True)
        return items

    def refresh(self) -> None:
        timeout = self.stale_timeout_s()
        items = self._filtered()
        self._shown = items
        active = sum(1 for d in items if d.is_active(timeout))
        self.count.setText("%d signal(s), %d active" % (len(items), active))

        self.table.blockSignals(True)
        self.table.setRowCount(len(items))
        select_row = -1
        for r, d in enumerate(items):
            is_active = d.is_active(timeout)
            if d.id == self._selected_id:
                select_row = r
            cells = [
                "●" if is_active else "○",
                "%.4f MHz" % d.frequency_mhz,
                d.signal_class,
                "%.1f dBFS" % d.level_dbfs,
                "%.1f dB" % d.snr_db,
                "~%.1f kHz" % (d.bandwidth_hz / 1e3),
                d.confidence,
                d.source or "-",
                _dt.datetime.fromtimestamp(d.last_seen).strftime("%H:%M:%S"),
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setForeground(QColor(theme.GOOD if is_active
                                              else theme.TEXT_FAINT))
                elif c in (3, 4, 5):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif c != 1:
                    item.setTextAlignment(Qt.AlignCenter)
                if c == 2:
                    item.setForeground(QColor(CLASS_COLORS.get(
                        d.signal_class, theme.TEXT)))
                if c == 4:
                    item.setForeground(QColor(
                        theme.GOOD if d.snr_db >= 20 else
                        theme.WARN if d.snr_db >= 10 else theme.TEXT))
                if c == 6:
                    item.setForeground(QColor(
                        theme.GOOD if d.confidence == "HIGH" else
                        theme.WARN if d.confidence == "MEDIUM" else
                        theme.TEXT_FAINT))
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        if select_row >= 0:
            self.table.selectRow(select_row)

    # ------------------------------------------------------------------
    def _on_selection(self) -> None:
        """Report a change of selection - not every redraw.

        refresh() reselects the current row, which fires this again. Emitting
        unconditionally meant a routine detection update overwrote whatever the
        page had put in its status line.
        """
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            if self._selected_id is not None:
                self._selected_id = None
                self.selection_changed.emit(None)
            return
        row = rows[0].row()
        if 0 <= row < len(self._shown):
            det = self._shown[row]
            if det.id == self._selected_id:
                return
            self._selected_id = det.id
            self.selection_changed.emit(det)

    def _on_double_click(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._shown):
            self.tune_requested.emit(self._shown[row].freq_hz)
