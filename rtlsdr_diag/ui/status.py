"""One status strip, shown identically wherever acquisition state matters."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy)

from ..core.acquisition import AcquisitionState, AcquisitionStatus
from . import theme

_COLOURS = {
    "good": theme.GOOD,
    "warn": theme.WARN,
    "bad": theme.BAD,
    "dim": theme.TEXT_DIM,
}


class SdrStatusStrip(QFrame):
    """State, owner and settings on one line, with a contextual action button.

    The button is the thing that was missing before: when the receiver is
    connected but idle, a page that needs live data should be able to start it
    rather than just report that there is nothing.
    """

    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, action_label: str = "Start Scanner", parent=None):
        super().__init__(parent)
        self.setObjectName("statusStrip")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._action_label = action_label

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(14)

        self.dot = QLabel("●")
        self.dot.setFont(QFont("Segoe UI", 13))
        lay.addWidget(self.dot)

        self.headline = QLabel("SDR NOT CONNECTED")
        f = QFont()
        f.setBold(True)
        f.setPointSize(10)
        self.headline.setFont(f)
        lay.addWidget(self.headline)

        self.owner = QLabel("")
        self.owner.setStyleSheet("color: %s; font-size: 11px;" % theme.TEXT_DIM)
        lay.addWidget(self.owner)

        lay.addStretch(1)

        self.settings = QLabel("")
        self.settings.setStyleSheet(
            "color: %s; font-family: Consolas, monospace; font-size: 11px;"
            % theme.TEXT_FAINT)
        lay.addWidget(self.settings)

        self.button = QPushButton(action_label)
        self.button.setProperty("accent", True)
        self.button.clicked.connect(self._on_click)
        lay.addWidget(self.button)

        self._status = AcquisitionStatus()
        self.update_status(self._status)

    # ------------------------------------------------------------------
    def _on_click(self) -> None:
        if self._status.is_running:
            self.stop_requested.emit()
        else:
            self.start_requested.emit()

    def update_status(self, status: AcquisitionStatus) -> None:
        self._status = status
        colour = _COLOURS.get(status.colour_key(), theme.TEXT_DIM)
        self.dot.setStyleSheet("color: %s;" % colour)
        self.headline.setStyleSheet("color: %s;" % colour)
        self.headline.setText(status.headline())
        self.owner.setText(status.owner_text())
        self.owner.setVisible(bool(status.owner_text()))
        self.settings.setText(status.settings_line() if status.is_connected else "")
        self.setStyleSheet(
            "#statusStrip { background: %s; border: 1px solid %s; "
            "border-radius: 8px; }" % (theme.BG_CARD, theme.BORDER))

        if status.is_running:
            self.button.setText("Stop")
            self.button.setEnabled(True)
        else:
            self.button.setText(self._action_label)
            self.button.setEnabled(
                status.state is not AcquisitionState.DISCONNECTED)
