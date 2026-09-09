"""Channel activity timeline: which channels were busy, and when.

One row per detected channel, one column per completed sweep, newest on the
right. A cell is coloured when the channel was above the detection threshold on
that pass. It is an occupancy record, nothing more - no content, no identity.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

LABEL_WIDTH = 118
ROW_HEIGHT = 15
ROW_GAP = 2
FOOTER_HEIGHT = 18


class ActivityTimeline(QWidget):
    """Rows of channels against recent sweep passes."""

    channel_clicked = Signal(float)          # frequency in Hz

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._rows: list = []                # Detection objects
        self._passes: list[int] = []
        self._classify = None
        self._level_range = (-90.0, -30.0)
        self._hover_row = -1

    def set_classifier(self, classify) -> None:
        self._classify = classify

    def set_data(self, detections, passes, level_range=None) -> None:
        self._rows = sorted(detections, key=lambda d: d.freq_hz)
        self._passes = list(passes)
        if level_range:
            self._level_range = level_range
        # Grow to fit every channel; the scroll area around us handles the rest.
        needed = len(self._rows) * (ROW_HEIGHT + ROW_GAP) + FOOTER_HEIGHT
        self.setMinimumHeight(max(120, needed))
        self.update()

    # ------------------------------------------------------------------
    def _row_at(self, y: float) -> int:
        idx = int(y // (ROW_HEIGHT + ROW_GAP))
        return idx if 0 <= idx < len(self._rows) else -1

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        row = self._row_at(event.position().y())
        if row != self._hover_row:
            self._hover_row = row
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_row = -1
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        row = self._row_at(event.position().y())
        if row >= 0:
            self.channel_clicked.emit(self._rows[row].freq_hz)

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG_ELEV))
        if not self._rows or not self._passes:
            p.setPen(QPen(QColor(theme.TEXT_FAINT)))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "No channel activity recorded yet - start a scan.")
            p.end()
            return

        width = self.width()
        grid_w = max(10.0, width - LABEL_WIDTH - 8)
        n = len(self._passes)
        cell_w = grid_w / n
        lo, hi = self._level_range
        span = max(hi - lo, 1e-6)

        font = QFont("Consolas", 8)
        p.setFont(font)

        for r, det in enumerate(self._rows):
            y = r * (ROW_HEIGHT + ROW_GAP)
            if y > self.height():
                break

            if r == self._hover_row:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 14))
                p.drawRect(QRectF(0, y, width, ROW_HEIGHT))

            # Frequency label, tinted by uplink/downlink when we know it.
            label_col = theme.TEXT_DIM
            if self._classify:
                kind = self._classify(det.freq_hz)
                if kind.startswith("Uplink"):
                    label_col = theme.WARN
                elif kind.startswith("Downlink"):
                    label_col = theme.ACCENT
            p.setPen(QPen(QColor(label_col)))
            p.drawText(QRectF(4, y, LABEL_WIDTH - 8, ROW_HEIGHT),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       "%.4f" % (det.freq_hz / 1e6))

            # Activity cells.
            p.setPen(Qt.NoPen)
            for i, pass_id in enumerate(self._passes):
                x = LABEL_WIDTH + i * cell_w
                level = det.seen_passes.get(pass_id)
                if level is None:
                    p.setBrush(QColor(255, 255, 255, 10))
                else:
                    frac = max(0.0, min(1.0, (level - lo) / span))
                    p.setBrush(QColor(int(40 + 215 * frac),
                                      int(150 + 55 * frac),
                                      int(190 - 150 * frac), 235))
                p.drawRect(QRectF(x, y, max(1.0, cell_w - 0.8), ROW_HEIGHT))

        # Axis hint, in the reserved strip below the last row.
        footer_y = len(self._rows) * (ROW_HEIGHT + ROW_GAP) + 2
        p.setPen(QPen(QColor(theme.TEXT_FAINT)))
        p.drawText(QRectF(LABEL_WIDTH, footer_y, 120, 14),
                   Qt.AlignLeft | Qt.AlignVCenter, "older")
        p.drawText(QRectF(width - 120, footer_y, 116, 14),
                   Qt.AlignRight | Qt.AlignVCenter, "newest sweep")
        p.end()
