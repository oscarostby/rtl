"""Waterfall tab: spectrum on top, scrolling waterfall underneath."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from ..sdr.engine import MODE_SPECTRUM
from . import theme, widgets
from .plots import SpectrumPlot, WaterfallPlot


class WaterfallTab(QWidget):
    # The waterfall displays the Spectrum tab's job rather than owning one.
    tab_label = "Spectrum"

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # --- toolbar ---------------------------------------------------------
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.btn_start = QPushButton("Start")
        self.btn_start.setProperty("accent", True)
        self.btn_start.clicked.connect(self._toggle)
        bar.addWidget(self.btn_start)

        self.btn_clear = QPushButton("Clear waterfall")
        self.btn_clear.clicked.connect(self._clear)
        bar.addWidget(self.btn_clear)

        bar.addWidget(QLabel("Floor offset"))
        self.slider_floor = QSlider(Qt.Horizontal)
        self.slider_floor.setRange(-25, 20)
        self.slider_floor.setValue(-5)
        self.slider_floor.setFixedWidth(120)
        self.slider_floor.valueChanged.connect(self._levels_changed)
        bar.addWidget(self.slider_floor)
        self.lbl_floor = QLabel("-5 dB")
        self.lbl_floor.setStyleSheet("color: %s;" % theme.TEXT_DIM)
        bar.addWidget(self.lbl_floor)

        bar.addWidget(QLabel("Range"))
        self.slider_range = QSlider(Qt.Horizontal)
        self.slider_range.setRange(15, 90)
        self.slider_range.setValue(45)
        self.slider_range.setFixedWidth(120)
        self.slider_range.valueChanged.connect(self._levels_changed)
        bar.addWidget(self.slider_range)
        self.lbl_range = QLabel("45 dB")
        self.lbl_range.setStyleSheet("color: %s;" % theme.TEXT_DIM)
        bar.addWidget(self.lbl_range)

        bar.addWidget(QLabel("History"))
        self.spin_history = QSpinBox()
        self.spin_history.setRange(60, 1200)
        self.spin_history.setValue(320)
        self.spin_history.setSuffix(" rows")
        self.spin_history.valueChanged.connect(self._history_changed)
        bar.addWidget(self.spin_history)

        self.chk_spectrum = QCheckBox("Show spectrum")
        self.chk_spectrum.setChecked(True)
        self.chk_spectrum.toggled.connect(self._toggle_spectrum)
        bar.addWidget(self.chk_spectrum)

        bar.addStretch(1)
        self.lbl_info = QLabel("Idle.")
        self.lbl_info.setStyleSheet("color: %s; font-family: Consolas, monospace;"
                                    % theme.TEXT_DIM)
        bar.addWidget(self.lbl_info)
        root.addLayout(bar)

        # --- plots ------------------------------------------------------------
        self.splitter = QSplitter(Qt.Vertical)
        self.spectrum = SpectrumPlot()
        self.spectrum.hovered.connect(self._on_hover)
        self.waterfall = WaterfallPlot(history=320)
        self.splitter.addWidget(self.spectrum)
        self.splitter.addWidget(self.waterfall)
        self.splitter.setSizes([260, 460])
        root.addWidget(self.splitter, 1)

        root.addWidget(widgets.dim_label(
            "X axis: frequency (MHz).  Y axis: time, newest row at the top.  "
            "Colour: received power relative to the current noise floor."))

    # ------------------------------------------------------------------
    def _toggle(self) -> None:
        if self.app.is_running_here(self.tab_label):
            self.app.engine_stop()
        else:
            self.app.engine_start(self.app.spectrum_tab.config())

    def _clear(self) -> None:
        self.waterfall.clear()
        self.spectrum.reset_peak()

    def _levels_changed(self) -> None:
        floor = self.slider_floor.value()
        rng = self.slider_range.value()
        self.lbl_floor.setText("%d dB" % floor)
        self.lbl_range.setText("%d dB" % rng)
        self.waterfall.set_dynamic_range(floor, rng)

    def _history_changed(self, value: int) -> None:
        self.waterfall.history = int(value)
        self.waterfall.clear()

    def _toggle_spectrum(self, shown: bool) -> None:
        self.spectrum.setVisible(shown)

    def set_running(self, running: bool) -> None:
        self.btn_start.setText("Stop" if running else "Start")
        if not running:
            self.lbl_info.setText("Idle.")

    def _on_hover(self, mhz: float, level: float) -> None:
        self.lbl_info.setText("%.4f MHz   %.1f dBFS" % (mhz, level))

    # ------------------------------------------------------------------
    def update_frame(self, frame) -> None:
        self.spectrum.set_data(frame.freqs, frame.power_db, frame.noise_floor_db)
        if frame.mode == MODE_SPECTRUM:
            self.waterfall.add_row(frame.freqs, frame.power_db,
                                   frame.noise_floor_db, frame.timestamp)
