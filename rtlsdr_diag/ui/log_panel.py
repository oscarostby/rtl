"""The signal log, down the right-hand side.

A survey read top-down: what came on the air, when, how strong, and which end
of a link it was. Painted rather than laid out in a table so the type stays
large enough to read in a car.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..core.observations import (KIND_DAB, KIND_FM, KIND_MAST, KIND_MEANING,
                                 KIND_MOBILE, KIND_ORDER, KIND_OTHER)

PANEL_BG = "#0b0e12"
LINE = "#1d2530"
TEXT = "#e8eef5"
MUTED = "#7b8794"
DIM = "#4a5560"

# The uplink is the one worth noticing: it means a radio near you transmitted.
KIND_COLOUR = {
    KIND_MOBILE: "#2ecc71",
    KIND_MAST: "#3f8fd0",
    KIND_FM: "#8a97a6",
    KIND_DAB: "#8a97a6",
    KIND_OTHER: "#5a6572",
}

ROW_H = 50.0
FILTERS = ["", KIND_MOBILE, KIND_MAST, KIND_FM, KIND_DAB]


class LogList(QWidget):
    """The rows themselves."""

    def __init__(self, log, parent=None):
        super().__init__(parent)
        self.log = log
        self.filter = ""
        self.offset = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumWidth(240)

    def visible_rows(self) -> int:
        return max(1, int(self.height() // ROW_H))

    def wheelEvent(self, event) -> None:  # noqa: N802
        rows = self.log.entries(self.filter)
        step = -1 if event.angleDelta().y() > 0 else 1
        limit = max(0, len(rows) - self.visible_rows())
        self.offset = max(0, min(limit, self.offset + step))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = float(self.width()), float(self.height())
        p.fillRect(self.rect(), QColor(PANEL_BG))

        rows = self.log.entries(self.filter)
        if not rows:
            self._text(p, QRectF(12, h / 2 - 30, w - 24, 60),
                       "nothing logged yet\n\nsignals appear here as they\n"
                       "come on the air", 11, DIM,
                       align=Qt.AlignHCenter | Qt.AlignVCenter)
            p.end()
            return

        now = time.time()
        limit = max(0, len(rows) - self.visible_rows())
        self.offset = min(self.offset, limit)
        y = 0.0
        for row in rows[self.offset:]:
            if y > h:
                break
            self._draw_row(p, QRectF(0, y, w, ROW_H), row, now)
            y += ROW_H
        p.end()

    # ------------------------------------------------------------------
    def _draw_row(self, p: QPainter, rect: QRectF, row, now: float) -> None:
        colour = QColor(KIND_COLOUR.get(row.kind, MUTED))
        live = row.is_active(now)

        if live:
            p.fillRect(rect, QColor(255, 255, 255, 10))
        p.setPen(QPen(QColor(LINE), 1))
        p.drawLine(rect.left() + 8, rect.bottom(), rect.right() - 8,
                   rect.bottom())

        # A bar down the left edge, bright while the channel is still active.
        p.fillRect(QRectF(rect.left(), rect.top() + 4, 3.0, rect.height() - 8),
                   colour if live else colour.darker(200))

        self._text(p, QRectF(rect.left() + 12, rect.top() + 5, 74, 18),
                   row.kind, 10, colour.name(), bold=True, spacing=1.0)
        self._text(p, QRectF(rect.left() + 88, rect.top() + 3, rect.width() - 150, 21),
                   "%.4f" % row.frequency_mhz, 14, TEXT, bold=True)
        self._text(p, QRectF(rect.right() - 62, rect.top() + 5, 54, 18),
                   "%.0f dB" % row.best_snr_db, 11,
                   TEXT if row.best_snr_db >= 15 else MUTED,
                   align=Qt.AlignRight | Qt.AlignVCenter)

        detail = "%s   %d hits" % (row.clock(), row.hits)
        if row.duration_s >= 1.0:
            detail += "   %.0f s" % row.duration_s
        self._text(p, QRectF(rect.left() + 12, rect.top() + 26,
                             rect.width() - 80, 18), detail, 10, MUTED)
        if row.is_brief() and row.kind == KIND_MOBILE:
            self._text(p, QRectF(rect.right() - 74, rect.top() + 26, 66, 18),
                       "BURST", 9, colour.name(), bold=True,
                       align=Qt.AlignRight | Qt.AlignVCenter)

    def _text(self, p, rect, text, size, colour, bold=False, spacing=0.0,
              align=Qt.AlignLeft | Qt.AlignVCenter):
        font = QFont("Segoe UI", size)
        font.setBold(bold)
        if spacing:
            font.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
        p.setFont(font)
        p.setPen(QPen(QColor(colour)))
        p.drawText(rect, align | Qt.TextWordWrap, text)


class LogPanel(QFrame):
    """Header, the list, and what to do with it."""

    save_requested = Signal()
    closed = Signal()

    def __init__(self, log, parent=None):
        super().__init__(parent)
        self.log = log
        self.setStyleSheet(
            "QFrame { background: %s; border-left: 1px solid %s; }"
            "QLabel { color: %s; }"
            "QPushButton { background: #161c24; color: %s; border: 1px solid %s;"
            " border-radius: 4px; padding: 6px; font-size: 11px; }"
            "QPushButton:hover { border-color: %s; }"
            % (PANEL_BG, LINE, MUTED, TEXT, LINE, MUTED))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 8)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(12, 0, 8, 0)
        self.title = QLabel("SIGNAL LOG")
        self.title.setStyleSheet(
            "color: %s; font-size: 12px; font-weight: bold; border: none;" % TEXT)
        head.addWidget(self.title)
        head.addStretch(1)
        close = QPushButton("\N{MULTIPLICATION SIGN}")
        close.setFixedSize(24, 22)
        close.clicked.connect(self.closed.emit)
        head.addWidget(close)
        lay.addLayout(head)

        self.summary = QLabel("")
        self.summary.setStyleSheet(
            "color: %s; font-size: 10px; border: none;" % MUTED)
        self.summary.setContentsMargins(12, 0, 12, 0)
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.list = LogList(log, self)
        lay.addWidget(self.list, 1)

        self.legend = QLabel("")
        self.legend.setStyleSheet(
            "color: %s; font-size: 9px; border: none;" % DIM)
        self.legend.setContentsMargins(12, 0, 12, 0)
        self.legend.setWordWrap(True)
        lay.addWidget(self.legend)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(10, 0, 10, 0)
        self.filter_btn = QPushButton("All")
        self.filter_btn.clicked.connect(self._cycle_filter)
        buttons.addWidget(self.filter_btn)
        self.save_btn = QPushButton("Save CSV")
        self.save_btn.clicked.connect(self.save_requested.emit)
        buttons.addWidget(self.save_btn)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self._clear)
        buttons.addWidget(self.clear_btn)
        lay.addLayout(buttons)

        self.refresh()

    # ------------------------------------------------------------------
    def _cycle_filter(self) -> None:
        current = FILTERS.index(self.list.filter) if self.list.filter in FILTERS else 0
        self.list.filter = FILTERS[(current + 1) % len(FILTERS)]
        self.list.offset = 0
        self.refresh()

    def _clear(self) -> None:
        self.log.clear()
        self.list.offset = 0
        self.refresh()

    def refresh(self) -> None:
        self.filter_btn.setText(self.list.filter or "All")
        counts = self.log.counts()
        parts = ["%s %d" % (k, counts[k]) for k in KIND_ORDER if counts.get(k)]
        self.summary.setText("   ".join(parts) if parts
                             else "waiting for the first signal")
        shown = self.list.filter
        if shown:
            self.legend.setText("%s: %s" % (shown, KIND_MEANING.get(shown, "")))
        else:
            self.legend.setText(
                "MOBILE = a terminal transmitting near you (uplink).   "
                "MAST = fixed infrastructure (downlink).   "
                "Which end of the link, not whose radio.")
        self.list.update()
