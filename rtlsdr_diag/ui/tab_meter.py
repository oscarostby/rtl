"""Signal-strength meter: monitor one frequency continuously."""
from __future__ import annotations

import time
from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from ..config import MAX_TUNE_HZ, MIN_TUNE_HZ, SPOT_PRESETS
from ..core.smoothing import MODES as SMOOTH_MODES, MEDIUM, LevelSmoother
from ..sdr import dsp
from ..sdr.engine import MODE_SPECTRUM, AcqConfig
from . import theme, widgets

BANDWIDTHS = [
    ("12.5 kHz", 12_500.0),
    ("25 kHz (TETRA raster)", 25_000.0),
    ("50 kHz", 50_000.0),
    ("200 kHz (FM broadcast)", 200_000.0),
    ("1 MHz", 1_000_000.0),
]

HISTORY_SECONDS = 120


class MeterTab(QWidget):
    tab_label = "Signal Meter"

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._reset_stats()
        self._hist_t: deque = deque(maxlen=4000)
        self._hist_v: deque = deque(maxlen=4000)
        self._hist_n: deque = deque(maxlen=4000)
        self._t0 = time.time()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # --- toolbar -----------------------------------------------------------
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_start = QPushButton("Start Monitoring")
        self.btn_start.setProperty("accent", True)
        self.btn_start.clicked.connect(self._toggle)
        bar.addWidget(self.btn_start)

        bar.addWidget(QLabel("Frequency"))
        self.freq = widgets.freq_spin(390.0125, MIN_TUNE_HZ / 1e6, MAX_TUNE_HZ / 1e6,
                                      decimals=4, step=0.0125)
        self.freq.valueChanged.connect(self._freq_changed)
        bar.addWidget(self.freq)

        self.preset = QComboBox()
        self.preset.addItem("Jump to...", None)
        for name, hz in SPOT_PRESETS:
            self.preset.addItem(name, hz)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        bar.addWidget(self.preset)

        bar.addWidget(QLabel("Bandwidth"))
        self.bw = QComboBox()
        for name, hz in BANDWIDTHS:
            self.bw.addItem(name, hz)
        self.bw.setCurrentIndex(1)
        bar.addWidget(self.bw)

        bar.addWidget(QLabel("Smoothing"))
        self.smoothing = QComboBox()
        for m in SMOOTH_MODES:
            self.smoothing.addItem(m, m)
        self.smoothing.setCurrentIndex(SMOOTH_MODES.index(MEDIUM))
        self.smoothing.setToolTip(
            "Exponential moving average. FAST follows every change; SLOW is "
            "steadier for comparing antenna positions.")
        self.smoothing.currentIndexChanged.connect(self._smoothing_changed)
        bar.addWidget(self.smoothing)

        self.btn_reset = QPushButton("Reset statistics")
        self.btn_reset.clicked.connect(self.reset)
        bar.addWidget(self.btn_reset)

        self.btn_log = QPushButton("Start Logging")
        self.btn_log.clicked.connect(self._toggle_logging)
        bar.addWidget(self.btn_log)
        bar.addStretch(1)
        root.addLayout(bar)

        # --- meter --------------------------------------------------------------
        meter_box = widgets.group("Received level")
        self.meter = widgets.SignalMeter(-100.0, 0.0)
        meter_box.layout().addWidget(self.meter)
        self.lbl_now = QLabel("--- dBFS")
        f = self.lbl_now.font()
        f.setPointSize(26)
        f.setBold(True)
        self.lbl_now.setFont(f)
        self.lbl_now.setAlignment(Qt.AlignCenter)
        self.lbl_now.setStyleSheet("color: %s;" % theme.TRACE)
        meter_box.layout().addWidget(self.lbl_now)
        root.addWidget(meter_box)

        # --- statistic cards ----------------------------------------------------
        stats_box = widgets.group("Statistics")
        grid = QGridLayout()
        grid.setSpacing(10)
        self.card_cur = widgets.MetricCard("Current", "--", "dBFS")
        self.card_avg = widgets.MetricCard("Average", "--", "dBFS")
        self.card_max = widgets.MetricCard("Maximum", "--", "dBFS")
        self.card_min = widgets.MetricCard("Minimum", "--", "dBFS")
        self.card_nf = widgets.MetricCard("Noise floor", "--", "dBFS")
        self.card_snr = widgets.MetricCard("SNR", "--", "dB")
        for i, card in enumerate((self.card_cur, self.card_avg, self.card_max,
                                  self.card_min, self.card_nf, self.card_snr)):
            grid.addWidget(card, 0, i)
        holder = QWidget()
        holder.setLayout(grid)
        stats_box.layout().addWidget(holder)
        root.addWidget(stats_box)

        # --- history plot -------------------------------------------------------
        hist_box = widgets.group("Level history")
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.setLabel("left", "Level", units="dBFS")
        self.plot.showGrid(x=True, y=True, alpha=0.22)
        self.plot.getPlotItem().setMenuEnabled(False)
        self.plot.setMinimumHeight(180)
        self.curve = self.plot.plot(pen=pg.mkPen(theme.TRACE, width=1.6))
        self.curve_noise = self.plot.plot(
            pen=pg.mkPen(theme.NOISE, width=1.0, style=Qt.DotLine))
        hist_box.layout().addWidget(self.plot)
        root.addWidget(hist_box, 1)

        root.addWidget(widgets.dim_label(
            "Move the antenna between locations and compare the average and "
            "maximum levels, and especially the SNR. Levels are relative to the "
            "receiver's full scale (dBFS); they are not calibrated to dBm."))

    # ------------------------------------------------------------------
    def _smoothing_changed(self) -> None:
        self._smoother.set_mode(self.smoothing.currentData())

    def _reset_stats(self) -> None:
        self._smoother = getattr(self, "_smoother", LevelSmoother(MEDIUM))
        self._smoother.set_mode(self.smoothing.currentData()
                                if hasattr(self, "smoothing") else MEDIUM)
        self._smoother.reset()
        self._count = 0
        self._sum_lin = 0.0
        self._max = -300.0
        self._min = 300.0
        self._cur = float("nan")
        self._nf = float("nan")

    def reset(self) -> None:
        self._reset_stats()
        self._hist_t.clear()
        self._hist_v.clear()
        self._hist_n.clear()
        self._t0 = time.time()
        self.curve.setData([], [])
        self.curve_noise.setData([], [])
        for card in (self.card_cur, self.card_avg, self.card_max,
                     self.card_min, self.card_nf, self.card_snr):
            card.set_value("--")
        self.meter.set_values(-100.0, -100.0, -100.0)
        self.lbl_now.setText("--- dBFS")

    def _preset_changed(self, index: int) -> None:
        hz = self.preset.currentData()
        if hz:
            self.freq.setValue(hz / 1e6)
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(0)
        self.preset.blockSignals(False)

    def _freq_changed(self, mhz: float) -> None:
        self.reset()
        self.app.engine_update({"center_hz": mhz * 1e6})

    def set_center_mhz(self, mhz: float) -> None:
        self.freq.setValue(mhz)

    def config(self) -> AcqConfig:
        return AcqConfig(mode=MODE_SPECTRUM, center_hz=self.freq.value() * 1e6,
                         sample_rate=1.024e6, gain="auto", fft_size=4096,
                         averages=8, detect=False, label=self.tab_label)

    def _toggle(self) -> None:
        if self.app.is_running_here(self.tab_label):
            self.app.engine_stop()
        else:
            self.reset()
            self.app.engine_start(self.config())

    def set_running(self, running: bool) -> None:
        self.btn_start.setText("Stop Monitoring" if running else "Start Monitoring")

    def _toggle_logging(self) -> None:
        if self.app.logger.is_logging:
            self.app.stop_logging()
        else:
            self.app.start_logging(self.tab_label)

    def set_logging(self, active: bool) -> None:
        self.btn_log.setText("Stop Logging" if active else "Start Logging")
        self.btn_log.setProperty("danger", active)
        self.btn_log.style().unpolish(self.btn_log)
        self.btn_log.style().polish(self.btn_log)

    # ------------------------------------------------------------------
    def update_frame(self, frame) -> None:
        if frame.mode != MODE_SPECTRUM:
            return
        target = self.freq.value() * 1e6
        if abs(frame.center_hz - target) > frame.sample_rate / 2.0:
            return                       # the engine is tuned elsewhere
        bw = float(self.bw.currentData())
        level = dsp.band_power_dbfs(frame.freqs, frame.power_db, target, bw)
        nf = frame.noise_floor_db
        # Noise reference integrated over the same bandwidth, for a fair SNR.
        bin_hz = abs(frame.freqs[1] - frame.freqs[0]) if frame.freqs.size > 1 else 1.0
        nf_band = nf + 10.0 * np.log10(max(bw / bin_hz, 1.0))
        snr = level - nf_band

        self._cur = level
        self._nf = nf_band
        smoothed = self._smoother.update(level, frame.timestamp)
        self._count = self._smoother.count
        self._max = self._smoother.maximum
        self._min = self._smoother.minimum
        avg = self._smoother.average
        level = smoothed

        self.lbl_now.setText("%.1f dBFS" % level)
        color = theme.GOOD if snr >= 20 else (theme.WARN if snr >= 8 else theme.TEXT)
        self.lbl_now.setStyleSheet("color: %s;" % color)
        self.card_cur.set_value("%.1f" % level)
        self.card_avg.set_value("%.1f" % avg)
        self.card_max.set_value("%.1f" % self._max)
        self.card_min.set_value("%.1f" % self._min)
        self.card_nf.set_value("%.1f" % nf_band)
        self.card_snr.set_value("%.1f" % snr, color)
        self.meter.set_values(level, self._max, nf_band)

        t = frame.timestamp - self._t0
        self._hist_t.append(t)
        self._hist_v.append(level)
        self._hist_n.append(nf_band)
        self.curve.setData(list(self._hist_t), list(self._hist_v))
        self.curve_noise.setData(list(self._hist_t), list(self._hist_n))
        if t > HISTORY_SECONDS:
            self.plot.setXRange(t - HISTORY_SECONDS, t, padding=0)

        if self.app.logger.is_logging and self._count % 5 == 0:
            self.app.logger.log(target, level, nf_band, snr, bw, self.tab_label)
