"""Reusable presentation widgets."""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QDoubleSpinBox, QFormLayout, QFrame, QGroupBox,
                               QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import theme


class StatusPill(QFrame):
    """The big CONNECTED / NOT FOUND indicator."""

    def __init__(self, text: str = "CHECKING...", parent=None):
        super().__init__(parent)
        self._state = "unknown"
        self.setObjectName("statusPill")
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 10, 20, 10)
        lay.setSpacing(2)
        self.label = QLabel(text)
        f = QFont()
        f.setPointSize(17)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        self.label.setFont(f)
        self.label.setAlignment(Qt.AlignCenter)
        self.sub = QLabel("")
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setStyleSheet("color: %s; font-size: 12px;" % theme.TEXT_DIM)
        lay.addWidget(self.label)
        lay.addWidget(self.sub)
        self.set_state("unknown", text)

    def set_state(self, state: str, text: str, sub: str = "") -> None:
        self._state = state
        colors = {
            "good": (theme.GOOD, "rgba(46, 204, 113, 0.12)"),
            "bad": (theme.BAD, "rgba(239, 68, 68, 0.12)"),
            "warn": (theme.WARN, "rgba(240, 160, 32, 0.12)"),
            "unknown": (theme.TEXT_DIM, "rgba(147, 163, 181, 0.08)"),
        }
        fg, bg = colors.get(state, colors["unknown"])
        self.setStyleSheet(
            "#statusPill { background: %s; border: 1px solid %s; border-radius: 12px; }"
            % (bg, fg))
        self.label.setStyleSheet("color: %s; background: transparent;" % fg)
        self.label.setText(text)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))


class MetricCard(QFrame):
    """A titled value tile used across the dashboard."""

    def __init__(self, title: str, value: str = "--", unit: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setStyleSheet(
            "#metricCard { background: %s; border: 1px solid %s; border-radius: 10px; }"
            % (theme.BG_CARD, theme.BORDER))
        self.setMinimumWidth(140)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(4)
        t = QLabel(title.upper())
        t.setStyleSheet("color: %s; font-size: 10px; font-weight: 700; "
                        "letter-spacing: 1px; background: transparent;" % theme.TEXT_FAINT)
        self.value_label = QLabel(value)
        vf = QFont()
        vf.setPointSize(16)
        vf.setBold(True)
        self.value_label.setFont(vf)
        self.value_label.setStyleSheet("color: %s; background: transparent;" % theme.TEXT)
        self.unit_label = QLabel(unit)
        self.unit_label.setStyleSheet("color: %s; font-size: 11px; background: transparent;"
                                      % theme.TEXT_DIM)
        self.unit_label.setVisible(bool(unit))
        lay.addWidget(t)
        lay.addWidget(self.value_label)
        lay.addWidget(self.unit_label)

    def set_value(self, value: str, color: str | None = None, unit: str | None = None) -> None:
        self.value_label.setText(value)
        self.value_label.setStyleSheet("color: %s; background: transparent;"
                                       % (color or theme.TEXT))
        if unit is not None:
            self.unit_label.setText(unit)
            self.unit_label.setVisible(bool(unit))


class SignalMeter(QWidget):
    """Large horizontal dBFS bar with a peak marker and scale."""

    def __init__(self, minimum: float = -100.0, maximum: float = 0.0, parent=None):
        super().__init__(parent)
        self.minimum = minimum
        self.maximum = maximum
        self.value = minimum
        self.peak = minimum
        self.noise = minimum
        self.setMinimumHeight(78)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_values(self, value: float, peak: float | None = None,
                   noise: float | None = None) -> None:
        self.value = float(value)
        if peak is not None:
            self.peak = float(peak)
        if noise is not None:
            self.noise = float(noise)
        self.update()

    def _frac(self, v: float) -> float:
        span = self.maximum - self.minimum
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (v - self.minimum) / span))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        bar_top, bar_h = 12, h - 42
        rect = QRectF(2, bar_top, w - 4, bar_h)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BG))
        p.drawRoundedRect(rect, 8, 8)

        grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
        grad.setColorAt(0.0, QColor("#1c6f8c"))
        grad.setColorAt(0.45, QColor(theme.GOOD))
        grad.setColorAt(0.78, QColor(theme.WARN))
        grad.setColorAt(1.0, QColor(theme.BAD))
        fill = QRectF(rect)
        fill.setWidth(max(0.0, rect.width() * self._frac(self.value)))
        p.setBrush(grad)
        if fill.width() > 1:
            p.drawRoundedRect(fill, 8, 8)

        # noise floor marker
        if self.noise > self.minimum:
            x = rect.left() + rect.width() * self._frac(self.noise)
            p.setPen(QPen(QColor(theme.NOISE), 1, Qt.DashLine))
            p.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))

        # peak marker
        x = rect.left() + rect.width() * self._frac(self.peak)
        p.setPen(QPen(QColor(theme.PEAK), 2))
        p.drawLine(int(x), int(rect.top()) - 3, int(x), int(rect.bottom()) + 3)

        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, 8, 8)

        # scale
        p.setPen(QPen(QColor(theme.TEXT_FAINT), 1))
        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        steps = 6
        for i in range(steps + 1):
            frac = i / steps
            x = rect.left() + rect.width() * frac
            val = self.minimum + (self.maximum - self.minimum) * frac
            p.drawLine(int(x), int(rect.bottom()) + 2, int(x), int(rect.bottom()) + 6)
            # Keep the first and last labels inside the widget.
            box = QRectF(x - 24, rect.bottom() + 8, 48, 14)
            align = Qt.AlignCenter
            if i == 0:
                box = QRectF(rect.left(), rect.bottom() + 8, 48, 14)
                align = Qt.AlignLeft | Qt.AlignVCenter
            elif i == steps:
                box = QRectF(rect.right() - 48, rect.bottom() + 8, 48, 14)
                align = Qt.AlignRight | Qt.AlignVCenter
            p.drawText(box, align, "%d dBFS" % round(val) if i in (0, steps)
                       else "%d" % round(val))
        p.end()


def group(title: str, inner: QWidget | None = None) -> QGroupBox:
    """A titled card. Always carries a QVBoxLayout, so callers can add to it."""
    box = QGroupBox(title)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(12, 8, 12, 12)
    lay.setSpacing(8)
    if inner is not None:
        lay.addWidget(inner)
    return box


def form_group(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setContentsMargins(12, 8, 12, 10)
    form.setSpacing(8)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return box, form


def freq_spin(value_mhz: float, minimum: float = 0.1, maximum: float = 1800.0,
              decimals: int = 4, step: float = 0.1) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setValue(value_mhz)
    box.setSuffix(" MHz")
    box.setMinimumWidth(130)
    box.setAlignment(Qt.AlignRight)
    return box


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("color: %s; background: %s; max-height: 1px;"
                       % (theme.BORDER, theme.BORDER))
    return line


def dim_label(text: str, italic: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    style = "color: %s; font-size: 11px;" % theme.TEXT_DIM
    if italic:
        style += " font-style: italic;"
    lbl.setStyleSheet(style)
    return lbl


def row(*widgets, stretch_last: bool = False, spacing: int = 8) -> QWidget:
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        else:
            lay.addWidget(w)
    if stretch_last:
        lay.addStretch(1)
    return holder
