"""Shared receiver-control panel."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout,
                               QLabel, QSpinBox, QVBoxLayout, QWidget)

from ..config import (DEFAULT_AVERAGES, DEFAULT_FFT_SIZE, FFT_SIZES,
                      MAX_TUNE_HZ, MIN_TUNE_HZ, SAMPLE_RATES, SPOT_PRESETS)
from . import theme, widgets


class AcqControls(QWidget):
    """Centre frequency / span / rate / gain / AGC / PPM / FFT size."""

    changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._emitting = True
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # --- tuning ---------------------------------------------------------
        tune_box, tune = widgets.form_group("Tuning")
        self.center = widgets.freq_spin(100.0, MIN_TUNE_HZ / 1e6, MAX_TUNE_HZ / 1e6)
        self.center.setToolTip("Centre frequency the receiver is tuned to.")
        tune.addRow("Centre", self.center)

        self.spot = QComboBox()
        self.spot.addItem("Jump to...", None)
        for name, hz in SPOT_PRESETS:
            self.spot.addItem(name, hz)
        tune.addRow("Preset", self.spot)

        self.rate = QComboBox()
        for r in SAMPLE_RATES:
            self.rate.addItem("%.3f MS/s" % (r / 1e6), r)
        self.rate.setCurrentIndex(SAMPLE_RATES.index(2.048e6))
        self.rate.setToolTip(
            "Sample rate. On an RTL-SDR this also sets the visible span; rates "
            "above 2.56 MS/s often drop samples on USB.")
        tune.addRow("Sample rate", self.rate)

        self.span_label = QLabel("2.048 MHz")
        self.span_label.setStyleSheet("color: %s;" % theme.TEXT_DIM)
        tune.addRow("Span", self.span_label)
        root.addWidget(tune_box)

        # --- gain -----------------------------------------------------------
        gain_box, gain = widgets.form_group("Gain and correction")
        self.agc = QCheckBox("Automatic gain (AGC)")
        self.agc.setChecked(True)
        gain.addRow(self.agc)

        self.gain = QDoubleSpinBox()
        self.gain.setRange(0.0, 60.0)
        self.gain.setSingleStep(0.5)
        self.gain.setValue(30.0)
        self.gain.setSuffix(" dB")
        self.gain.setEnabled(False)
        self.gain.setAlignment(Qt.AlignRight)
        gain.addRow("Tuner gain", self.gain)

        self.ppm = QSpinBox()
        self.ppm.setRange(-200, 200)
        self.ppm.setValue(0)
        self.ppm.setSuffix(" ppm")
        self.ppm.setAlignment(Qt.AlignRight)
        self.ppm.setToolTip("Crystal frequency correction in parts per million.")
        gain.addRow("Frequency correction", self.ppm)
        root.addWidget(gain_box)

        # --- analysis -------------------------------------------------------
        fft_box, fft = widgets.form_group("Analysis")
        self.fft = QComboBox()
        for n in FFT_SIZES:
            self.fft.addItem(str(n), n)
        self.fft.setCurrentIndex(FFT_SIZES.index(DEFAULT_FFT_SIZE))
        fft.addRow("FFT size", self.fft)

        self.averages = QSpinBox()
        self.averages.setRange(1, 64)
        self.averages.setValue(DEFAULT_AVERAGES)
        self.averages.setAlignment(Qt.AlignRight)
        self.averages.setToolTip("Number of FFTs averaged per displayed frame.")
        fft.addRow("Averaging", self.averages)

        self.res_label = QLabel("")
        self.res_label.setStyleSheet("color: %s;" % theme.TEXT_DIM)
        fft.addRow("Bin width", self.res_label)
        root.addWidget(fft_box)

        # --- wiring ---------------------------------------------------------
        self.center.valueChanged.connect(lambda v: self._emit({"center_hz": v * 1e6}))
        self.rate.currentIndexChanged.connect(self._rate_changed)
        self.fft.currentIndexChanged.connect(self._fft_changed)
        self.averages.valueChanged.connect(lambda v: self._emit({"averages": int(v)}))
        self.ppm.valueChanged.connect(lambda v: self._emit({"ppm": int(v)}))
        self.gain.valueChanged.connect(self._gain_changed)
        self.agc.toggled.connect(self._agc_changed)
        self.spot.currentIndexChanged.connect(self._spot_changed)
        self._update_labels()

    # -- helpers -------------------------------------------------------------
    def _emit(self, changes: dict) -> None:
        if self._emitting:
            self.changed.emit(changes)

    def _update_labels(self) -> None:
        rate = self.rate.currentData()
        fft = self.fft.currentData()
        self.span_label.setText("%.3f MHz" % (rate / 1e6))
        self.res_label.setText("%.1f Hz" % (rate / fft))

    def _rate_changed(self) -> None:
        self._update_labels()
        self._emit({"sample_rate": float(self.rate.currentData())})

    def _fft_changed(self) -> None:
        self._update_labels()
        self._emit({"fft_size": int(self.fft.currentData())})

    def _gain_changed(self, value: float) -> None:
        if not self.agc.isChecked():
            self._emit({"gain": float(value)})

    def _agc_changed(self, checked: bool) -> None:
        self.gain.setEnabled(not checked)
        self._emit({"gain": "auto" if checked else float(self.gain.value())})

    def _spot_changed(self, index: int) -> None:
        hz = self.spot.currentData()
        if hz:
            self.center.setValue(hz / 1e6)
        self.spot.blockSignals(True)
        self.spot.setCurrentIndex(0)
        self.spot.blockSignals(False)

    def populate_gains(self, gains: list[float]) -> None:
        if not gains:
            return
        self.gain.setRange(min(gains), max(gains))
        self.gain.setToolTip("Supported tuner gains (dB): "
                             + ", ".join("%.1f" % g for g in gains))

    def set_center_mhz(self, mhz: float) -> None:
        self.center.setValue(mhz)

    def values(self) -> dict:
        return {
            "center_hz": self.center.value() * 1e6,
            "sample_rate": float(self.rate.currentData()),
            "fft_size": int(self.fft.currentData()),
            "averages": int(self.averages.value()),
            "ppm": int(self.ppm.value()),
            "gain": "auto" if self.agc.isChecked() else float(self.gain.value()),
        }
