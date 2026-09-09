"""Live spectrum analyser tab."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from ..sdr.engine import MODE_SPECTRUM, AcqConfig
from . import theme, widgets
from .controls import AcqControls
from .plots import SpectrumPlot


class SpectrumTab(QWidget):
    tab_label = "Spectrum"

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # --- left: controls ---------------------------------------------------
        side = QWidget()
        side.setFixedWidth(300)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(10)

        self.controls = AcqControls()
        self.controls.changed.connect(self._on_control_change)
        side_lay.addWidget(self.controls)

        disp_box, disp_form = widgets.form_group("Display")
        self.chk_peak = QCheckBox("Peak hold")
        self.chk_peak.setChecked(True)
        self.chk_peak.toggled.connect(self.plot_set_peak)
        self.chk_auto = QCheckBox("Auto Y scale")
        self.chk_auto.setChecked(True)
        self.chk_auto.toggled.connect(lambda v: self.plot.set_autoscale(v))
        self.chk_markers = QCheckBox("Mark detected carriers")
        self.chk_markers.setChecked(True)
        self.btn_reset_peak = QPushButton("Reset peak hold")
        self.btn_reset_peak.clicked.connect(lambda: self.plot.reset_peak())
        disp_form.addRow(self.chk_peak)
        disp_form.addRow(self.chk_auto)
        disp_form.addRow(self.chk_markers)
        disp_form.addRow(self.btn_reset_peak)
        side_lay.addWidget(disp_box)

        run_box = widgets.group("Acquisition")
        self.btn_start = QPushButton("Start")
        self.btn_start.setProperty("accent", True)
        self.btn_start.clicked.connect(self._toggle)
        run_box.layout().addWidget(self.btn_start)
        self.lbl_status = widgets.dim_label("Idle.")
        run_box.layout().addWidget(self.lbl_status)
        side_lay.addWidget(run_box)
        side_lay.addStretch(1)
        root.addWidget(side)

        # --- right: plot -------------------------------------------------------
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        header = QHBoxLayout()
        self.lbl_hover = QLabel("Hover over the spectrum for a frequency / level readout")
        self.lbl_hover.setStyleSheet(
            "color: %s; font-family: Consolas, monospace; font-size: 12px;" % theme.TEXT_DIM)
        self.lbl_noise = QLabel("")
        self.lbl_noise.setStyleSheet("color: %s; font-size: 12px;" % theme.NOISE)
        header.addWidget(self.lbl_hover)
        header.addStretch(1)
        header.addWidget(self.lbl_noise)
        right_lay.addLayout(header)

        self.plot = SpectrumPlot()
        self.plot.hovered.connect(self._on_hover)
        right_lay.addWidget(self.plot, 1)

        legend = QLabel(
            "<span style='color:%s'>&#9473;&#9473; live</span> &nbsp;&nbsp;"
            "<span style='color:%s'>&#9476;&#9476; peak hold</span> &nbsp;&nbsp;"
            "<span style='color:%s'>&#8943; noise floor</span>"
            % (theme.TRACE, theme.PEAK, theme.NOISE))
        legend.setStyleSheet("font-size: 11px;")
        right_lay.addWidget(legend)
        root.addWidget(right, 1)

    # ------------------------------------------------------------------
    def plot_set_peak(self, enabled: bool) -> None:
        self.plot.set_peak_hold(enabled)

    def _toggle(self) -> None:
        if self.app.is_running_here(self.tab_label):
            self.app.engine_stop()
        else:
            self.app.engine_start(self.config())

    def config(self) -> AcqConfig:
        v = self.controls.values()
        return AcqConfig(mode=MODE_SPECTRUM, center_hz=v["center_hz"],
                         sample_rate=v["sample_rate"], gain=v["gain"], ppm=v["ppm"],
                         fft_size=v["fft_size"], averages=v["averages"],
                         detect=True, label=self.tab_label)

    def _on_control_change(self, changes: dict) -> None:
        if "fft_size" in changes or "averages" in changes:
            self.plot.reset_peak()
        if "center_hz" in changes or "sample_rate" in changes:
            self.plot.reset_peak()
        self.app.engine_update(changes)

    def set_running(self, running: bool) -> None:
        self.btn_start.setText("Stop" if running else "Start")
        self.lbl_status.setText("Receiving." if running else "Idle.")

    def set_center_mhz(self, mhz: float) -> None:
        self.controls.set_center_mhz(mhz)

    # ------------------------------------------------------------------
    def update_frame(self, frame) -> None:
        if frame.mode != MODE_SPECTRUM:
            return
        self.plot.set_data(frame.freqs, frame.power_db, frame.noise_floor_db)
        peak = float(np.max(frame.power_db)) if frame.power_db.size else float("nan")
        self.lbl_noise.setText("noise floor %.1f dBFS   peak %.1f dBFS   SNR %.1f dB"
                               % (frame.noise_floor_db, peak,
                                  peak - frame.noise_floor_db))

    def update_peaks(self, peaks) -> None:
        if not self.chk_markers.isChecked():
            self.plot.clear_markers()
            return
        top = sorted(peaks, key=lambda p: p.level_dbfs, reverse=True)[:12]
        self.plot.add_markers([p.freq_hz for p in top],
                              ["%.3f" % (p.freq_hz / 1e6) for p in top])

    def _on_hover(self, mhz: float, level: float) -> None:
        if np.isfinite(level):
            self.lbl_hover.setText("Frequency  %.4f MHz     Signal level  %.1f dBFS"
                                   % (mhz, level))
