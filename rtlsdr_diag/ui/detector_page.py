"""One full-screen detector page, drawn rather than laid out in widgets.

Everything is painted so the type size can scale with the window: this is meant
to be read at arm's length in a car, not clicked with a mouse.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.detector import METER_SEGMENTS, trend_label

BG = "#000000"
DIM = "#3a4048"
TEXT = "#e8eef5"
MUTED = "#7b8794"

STRENGTH_COLOURS = {
    "Very Weak": "#4a5560",
    "Weak": "#3f7fb0",
    "Medium": "#f0a020",
    "Strong": "#35d6a4",
    "Very Strong": "#2ecc71",
}
TREND_COLOURS = {
    "RAPIDLY RISING": "#2ecc71",
    "RISING": "#35d6a4",
    "STABLE": "#8a97a6",
    "FALLING": "#f0a020",
    "RAPIDLY FALLING": "#ef4444",
}


class DetectorPage(QWidget):
    """Title, frequency, segment meter, strength word, SNR and trend."""

    lock_toggled = Signal()

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 240)
        self.setAutoFillBackground(False)
        self._scan_phase = 0.0
        self._freq_rect = QRectF()

    # ------------------------------------------------------------------
    def advance_scan_animation(self, dt: float = 0.05) -> None:
        self._scan_phase = (self._scan_phase + dt) % 2.0

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # Tapping the frequency toggles the lock; anything else is ignored so
        # a stray touch while driving cannot change the mode.
        if self._freq_rect.contains(event.position()):
            self.lock_toggled.emit()

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = float(self.width()), float(self.height())
        p.fillRect(self.rect(), QColor(BG))

        unit = min(w / 40.0, h / 26.0)      # one type unit
        state = self.state
        detection = state.current

        # --- title --------------------------------------------------------
        title = state.spec.title
        self._text(p, QRectF(0, h * 0.04, w, unit * 3.2), title,
                   size=unit * 2.4, colour=MUTED, bold=True, spacing=6.0)
        if state.spec.caption:
            self._text(p, QRectF(0, h * 0.04 + unit * 3.0, w, unit * 1.5),
                       state.spec.caption, size=unit * 0.95, colour=DIM)

        # --- frequency ----------------------------------------------------
        self._freq_rect = QRectF(0, h * 0.18, w, unit * 6.0)
        if detection is not None:
            freq_text = "%.4f" % (detection.freq_hz / 1e6)
            self._text(p, self._freq_rect, freq_text, size=unit * 5.0,
                       colour=TEXT, bold=True)
            # Clear of the digits' baseline, not tucked under them. The DAB
            # block rides on the same line - it is arithmetic on the published
            # Band III raster, not decoded ensemble data.
            unit_line = "MHz"
            block = state.block_name()
            channel = state.channel_name()
            if block:
                unit_line = "MHz     Block %s" % block
            elif channel:
                unit_line = "MHz     ch %s" % channel
            self._text(p, QRectF(0, h * 0.18 + unit * 6.3, w, unit * 1.8),
                       unit_line, size=unit * 1.2, colour=MUTED)
        else:
            self._text(p, self._freq_rect, "SCANNING", size=unit * 3.4,
                       colour=DIM, bold=True, spacing=4.0)
            self._text(p, QRectF(0, h * 0.18 + unit * 4.6, w, unit * 2.2),
                       state.spec.range_text(), size=unit * 1.5, colour=MUTED)

        # --- meter --------------------------------------------------------
        meter_h = unit * 3.6
        meter_w = w * 0.74
        meter = QRectF((w - meter_w) / 2.0, h * 0.49, meter_w, meter_h)
        self._draw_meter(p, meter, detection is not None)

        # --- strength word ------------------------------------------------
        if detection is not None:
            label = state.strength_label()
            self._text(p, QRectF(0, meter.bottom() + unit * 0.6, w, unit * 2.6),
                       label.upper(), size=unit * 2.0,
                       colour=STRENGTH_COLOURS.get(label, TEXT), bold=True,
                       spacing=3.0)

            # --- SNR ------------------------------------------------------
            snr_text = "SNR %.0f dB" % state.shown_snr_db()
            if state.listening:
                snr_text += "     ♪ LISTENING"
            self._text(p, QRectF(0, meter.bottom() + unit * 3.4, w, unit * 2.0),
                       snr_text, size=unit * 1.5,
                       colour="#35d6a4" if state.listening else MUTED)

            # --- trend ----------------------------------------------------
            arrow, words = trend_label(state.trend(15.0))
            if words:
                colour = TREND_COLOURS.get(words, MUTED)
                self._text(p,
                           QRectF(0, meter.bottom() + unit * 5.2, w, unit * 2.4),
                           "%s  %s" % (arrow, words), size=unit * 1.7,
                           colour=colour, bold=True, spacing=2.0)
                result = state.trend(15.0)
                self._text(p,
                           QRectF(0, meter.bottom() + unit * 7.2, w, unit * 1.6),
                           "%+.1f dB / 15 s" % result.change_db,
                           size=unit * 1.0, colour=DIM)

        else:
            # The caption already sits under the title - say what the empty
            # meter means instead of repeating it.
            quiet = ("waiting for a transmission" if state.spec.bursty
                     else "nothing detected in this band")
            self._text(p, QRectF(0, meter.bottom() + unit * 1.0, w, unit * 1.8),
                       quiet, size=unit * 1.2, colour=DIM)
            age = state.seconds_since_activity()
            if age != float("inf"):
                if age < 90:
                    ago = "%.0f s ago" % age
                elif age < 5400:
                    ago = "%.0f min ago" % (age / 60.0)
                else:
                    ago = "over an hour ago"
                self._text(p,
                           QRectF(0, meter.bottom() + unit * 2.8, w, unit * 1.8),
                           "last activity %s   %.4f MHz"
                           % (ago, state.last_activity_freq_hz / 1e6),
                           size=unit * 1.1, colour=MUTED)

        # --- what the sound means -----------------------------------------
        if state.listening and state.spec.audio_mode == "ENV":
            self._text(p, QRectF(0, h * 0.945, w, unit * 1.4),
                       "beeps get faster and higher as the signal gets "
                       "stronger", size=unit * 1.0, colour=MUTED)
            self._text(p, QRectF(0, h * 0.975, w, unit * 1.3),
                       "TETRA carries no sound to play - this follows the "
                       "signal only", size=unit * 0.85, colour=DIM)

        # --- lock ---------------------------------------------------------
        if state.is_locked:
            self._text(p, QRectF(w * 0.5, h * 0.10, w * 0.45, unit * 1.6),
                       "LOCK", size=unit * 1.1, colour="#f0a020", bold=True,
                       align=Qt.AlignRight | Qt.AlignVCenter, spacing=3.0)
        p.end()

    # ------------------------------------------------------------------
    def _draw_meter(self, p: QPainter, rect: QRectF, has_signal: bool) -> None:
        gap = rect.width() * 0.012
        seg_w = (rect.width() - gap * (METER_SEGMENTS - 1)) / METER_SEGMENTS
        lit = self.state.segments(METER_SEGMENTS)
        label = self.state.strength_label()
        colour = QColor(STRENGTH_COLOURS.get(label, TEXT))

        for i in range(METER_SEGMENTS):
            x = rect.left() + i * (seg_w + gap)
            seg = QRectF(x, rect.top(), seg_w, rect.height())
            if has_signal and i < lit:
                grad = QLinearGradient(seg.topLeft(), seg.bottomLeft())
                grad.setColorAt(0.0, colour.lighter(115))
                grad.setColorAt(1.0, colour)
                p.setBrush(grad)
                p.setPen(Qt.NoPen)
            elif not has_signal:
                # Scanning: a slow sweep along the empty meter.
                pos = (self._scan_phase * METER_SEGMENTS) % (METER_SEGMENTS * 2)
                near = abs(i - (pos if pos < METER_SEGMENTS
                                else METER_SEGMENTS * 2 - pos))
                alpha = max(0, 70 - int(near * 26))
                p.setBrush(QColor(120, 160, 200, alpha))
                p.setPen(QPen(QColor(DIM), 1))
            else:
                p.setBrush(QColor(20, 24, 28))
                p.setPen(QPen(QColor(DIM), 1))
            p.drawRoundedRect(seg, 3, 3)

    def _text(self, p: QPainter, rect: QRectF, text: str, size: float,
              colour: str, bold: bool = False, spacing: float = 0.0,
              align=Qt.AlignHCenter | Qt.AlignVCenter) -> None:
        font = QFont("Segoe UI", max(6, int(size)))
        font.setBold(bold)
        if spacing:
            font.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
        p.setFont(font)
        p.setPen(QPen(QColor(colour)))
        p.drawText(rect, align, text)
