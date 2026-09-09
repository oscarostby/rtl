"""Shared implementation for the sweeping tabs (TETRA RF check and Scanner)."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout,
                               QLabel, QProgressBar, QPushButton, QScrollArea,
                               QSplitter, QVBoxLayout, QWidget)

from ..config import (DEFAULT_SNR_THRESHOLD_DB, MAX_TUNE_HZ, MIN_TUNE_HZ,
                      SAMPLE_RATES, TETRA_CHANNEL_WIDTH_HZ)
from ..core.signal_store import SignalStore
from ..sdr.engine import MODE_SWEEP, AcqConfig
from . import theme, widgets
from .plots import SpectrumPlot
from .tables import DetectionTable
from .timeline import ActivityTimeline

# A carrier must be seen this many times before it is listed - one bin poking
# above the threshold once is nearly always a noise excursion.
MIN_HITS = 2
# Detections older than this are forgotten, so the table reflects the session
# rather than growing forever.
DETECTION_MAX_AGE_S = 300.0
# How many recent sweeps the occupancy percentage is measured over.
OCCUPANCY_PASSES = 60


class SweepTabBase(QWidget):
    """Range sweep + carrier table + CSV logging."""

    tab_label = "Sweep"
    default_start_mhz = 380.0
    default_stop_mhz = 400.0
    default_raster_hz = TETRA_CHANNEL_WIDTH_HZ
    intro_text = ""
    show_presets = True
    # Optional callable mapping a frequency to a transmitter-type label.
    classifier = None
    # Show the per-channel activity timeline (occupancy over recent sweeps).
    show_timeline = False
    # Detector defaults for this tab; a band preset overrides them.
    default_min_bandwidth_hz = 4000.0
    default_smoothing_hz = 2000.0
    default_gap_hz = 3000.0

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        # The shared store, not a private one: Radar and the Dashboard read the
        # same detections this tab produces, and only one component ever owns
        # the receiver.
        self.tracker: SignalStore = app.signal_store
        self.tracker.tolerance_hz = max(6000.0, self.default_raster_hz * 0.5)
        self._last_sweep_time = 0.0
        self._detector = {"min_bandwidth_hz": self.default_min_bandwidth_hz,
                          "smoothing_hz": self.default_smoothing_hz,
                          "gap_hz": self.default_gap_hz}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        if self.intro_text:
            intro = QLabel(self.intro_text)
            intro.setWordWrap(True)
            intro.setStyleSheet(
                "background: rgba(47,155,255,0.08); border: 1px solid %s; "
                "border-radius: 8px; padding: 10px; color: %s; font-size: 12px;"
                % (theme.ACCENT_DIM, theme.TEXT))
            root.addWidget(intro)

        root.addLayout(self._build_toolbar())

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        splitter = QSplitter(Qt.Vertical)
        self.plot = SpectrumPlot()
        self.plot.hovered.connect(self._on_hover)
        splitter.addWidget(self.plot)

        table_holder = QWidget()
        th = QVBoxLayout(table_holder)
        th.setContentsMargins(0, 6, 0, 0)
        th.setSpacing(6)
        head = QHBoxLayout()
        self.lbl_count = QLabel("0 carriers detected")
        self.lbl_count.setStyleSheet("font-weight: 600;")
        head.addWidget(self.lbl_count)
        head.addStretch(1)
        self.lbl_sweep = QLabel("")
        self.lbl_sweep.setStyleSheet("color: %s; font-family: Consolas, monospace;"
                                     % theme.TEXT_DIM)
        head.addWidget(self.lbl_sweep)
        th.addLayout(head)

        self.table = DetectionTable(classify=self.classifier)
        self.table.show_occupancy(self.show_timeline)
        self.table.tune_requested.connect(self.app.tune_to)
        th.addWidget(self.table)

        self.timeline = None
        if self.show_timeline:
            th.addWidget(widgets.dim_label(
                "Channel activity - one column per completed sweep, newest on "
                "the right. Click a row to tune to it."))
            self.timeline = ActivityTimeline()
            self.timeline.set_classifier(self.classifier)
            self.timeline.channel_clicked.connect(self.app.tune_to)
            timeline_scroll = QScrollArea()
            timeline_scroll.setWidgetResizable(True)
            timeline_scroll.setFrameShape(QScrollArea.NoFrame)
            timeline_scroll.setWidget(self.timeline)
            timeline_scroll.setMinimumHeight(170)
            th.addWidget(timeline_scroll)
        splitter.addWidget(table_holder)
        splitter.setSizes([300, 320])
        root.addWidget(splitter, 1)

        self.footer = widgets.dim_label(self.footer_text())
        root.addWidget(self.footer)

    # ------------------------------------------------------------------
    def footer_text(self) -> str:
        return ("Detection is based on RF energy above the local noise floor. "
                "A carrier is listed after it has been seen at least twice. "
                "Bandwidth is estimated from the -6 dB width of each peak and is "
                "approximate. Double-click a row to tune the Spectrum tab to it.")

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.btn_start = QPushButton("Start Scan")
        self.btn_start.setProperty("accent", True)
        self.btn_start.clicked.connect(self._toggle)
        bar.addWidget(self.btn_start)

        if self.show_presets:
            from ..config import PRESETS
            self.preset = QComboBox()
            self.preset.addItem("Preset...", None)
            for p in PRESETS:
                self.preset.addItem(p.name, p)
            self.preset.currentIndexChanged.connect(self._preset_changed)
            self.preset.setMinimumWidth(230)
            bar.addWidget(self.preset)

        bar.addWidget(QLabel("From"))
        self.start_mhz = widgets.freq_spin(self.default_start_mhz,
                                           MIN_TUNE_HZ / 1e6, MAX_TUNE_HZ / 1e6,
                                           decimals=3, step=1.0)
        self.start_mhz.valueChanged.connect(self._range_changed)
        bar.addWidget(self.start_mhz)

        bar.addWidget(QLabel("To"))
        self.stop_mhz = widgets.freq_spin(self.default_stop_mhz,
                                          MIN_TUNE_HZ / 1e6, MAX_TUNE_HZ / 1e6,
                                          decimals=3, step=1.0)
        self.stop_mhz.valueChanged.connect(self._range_changed)
        bar.addWidget(self.stop_mhz)

        bar.addWidget(QLabel("Rate"))
        self.rate = QComboBox()
        for r in SAMPLE_RATES:
            self.rate.addItem("%.3f MS/s" % (r / 1e6), r)
        self.rate.setCurrentIndex(SAMPLE_RATES.index(2.048e6))
        self.rate.currentIndexChanged.connect(self._range_changed)
        bar.addWidget(self.rate)

        bar.addWidget(QLabel("SNR >"))
        self.snr = QDoubleSpinBox()
        self.snr.setRange(3.0, 60.0)
        self.snr.setValue(DEFAULT_SNR_THRESHOLD_DB)
        self.snr.setSuffix(" dB")
        self.snr.setAlignment(Qt.AlignRight)
        self.snr.valueChanged.connect(
            lambda v: self.app.engine_update({"snr_threshold_db": float(v)}))
        bar.addWidget(self.snr)

        self.chk_agc = QCheckBox("AGC")
        self.chk_agc.setChecked(True)
        self.chk_agc.toggled.connect(
            lambda v: self.app.engine_update({"gain": "auto" if v else 40.0}))
        bar.addWidget(self.chk_agc)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self.clear_detections)
        bar.addWidget(self.btn_clear)

        self.btn_log = QPushButton("Start Logging")
        self.btn_log.clicked.connect(self._toggle_logging)
        bar.addWidget(self.btn_log)

        bar.addStretch(1)
        return bar

    # ------------------------------------------------------------------
    def _preset_changed(self, index: int) -> None:
        preset = self.preset.currentData()
        if preset is None:
            return
        self.start_mhz.setValue(preset.start_hz / 1e6)
        self.stop_mhz.setValue(preset.stop_hz / 1e6)
        idx = self.rate.findData(preset.sample_rate)
        if idx >= 0:
            self.rate.setCurrentIndex(idx)
        self.snr.setValue(preset.snr_threshold_db)
        self._detector = {"min_bandwidth_hz": preset.min_bandwidth_hz,
                          "smoothing_hz": preset.smoothing_hz,
                          "gap_hz": preset.gap_hz}
        self.app.engine_update(dict(self._detector))
        self.footer.setText(preset.note + "  " + self.footer_text())

    def _range_changed(self) -> None:
        self.app.engine_update({
            "sweep_start_hz": self.start_mhz.value() * 1e6,
            "sweep_stop_hz": self.stop_mhz.value() * 1e6,
            "sample_rate": float(self.rate.currentData()),
        })

    def config(self) -> AcqConfig:
        lo = min(self.start_mhz.value(), self.stop_mhz.value()) * 1e6
        hi = max(self.start_mhz.value(), self.stop_mhz.value()) * 1e6
        return AcqConfig(
            mode=MODE_SWEEP,
            center_hz=(lo + hi) / 2.0,
            sample_rate=float(self.rate.currentData()),
            gain="auto" if self.chk_agc.isChecked() else 40.0,
            sweep_start_hz=lo, sweep_stop_hz=hi,
            snr_threshold_db=float(self.snr.value()),
            min_bandwidth_hz=self._detector["min_bandwidth_hz"],
            smoothing_hz=self._detector["smoothing_hz"],
            gap_hz=self._detector["gap_hz"],
            detect=True,
            label=self.tab_label,
        )

    def _toggle(self) -> None:
        if self.app.is_running_here(self.tab_label):
            self.app.engine_stop()
        else:
            self.plot.reset_peak()
            self.app.engine_start(self.config())

    def set_running(self, running: bool) -> None:
        self.btn_start.setText("Stop Scan" if running else "Start Scan")
        if not running:
            self.progress.setValue(0)

    def clear_detections(self) -> None:
        # Shared store: clear everything, because a half-cleared store is more
        # confusing than an empty one, and only one tab scans at a time.
        self.tracker.clear()
        if self.timeline is not None:
            self.timeline.set_data([], [])
        self.table.update_rows([])
        self.lbl_count.setText("0 carriers detected")
        self.plot.reset_peak()
        self.plot.clear_markers()

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

    def _on_hover(self, mhz: float, level: float) -> None:
        self.lbl_sweep.setText("%.4f MHz   %.1f dBFS" % (mhz, level))

    # ------------------------------------------------------------------
    def update_frame(self, frame) -> None:
        """Live progress while a sweep step is being measured."""
        if frame.mode != MODE_SWEEP:
            return
        lo = min(self.start_mhz.value(), self.stop_mhz.value())
        hi = max(self.start_mhz.value(), self.stop_mhz.value())
        span = max(hi - lo, 1e-6)
        pos = (frame.center_hz / 1e6 - lo) / span
        self.progress.setValue(int(max(0.0, min(1.0, pos)) * 100))

    def update_sweep(self, result) -> None:
        if result.label != self.tab_label:
            return
        self.plot.set_data(result.freqs, result.power_db, result.noise_floor_db)
        self._last_sweep_time = result.duration_s
        self.lbl_sweep.setText("sweep %.1f s   %.3f-%.3f MHz   noise floor %.1f dBFS"
                               % (result.duration_s, result.start_hz / 1e6,
                                  result.stop_hz / 1e6, result.noise_floor_db))
        self.progress.setValue(100)

    def update_peaks(self, peaks, label: str) -> None:
        if label != self.tab_label:
            return
        # Each batch of peaks is one completed sweep of the range.
        self.tracker.begin_pass()
        logging_on = self.app.logger.is_logging
        gain = self.app.current_gain()
        for p in peaks:
            self.tracker.add_or_update_detection(
                p.freq_hz, p.level_dbfs, p.noise_dbfs, p.snr_db,
                p.bandwidth_hz, source=self.tab_label, gain_db=gain)
            if logging_on:
                self.app.logger.log(p.freq_hz, p.level_dbfs, p.noise_dbfs,
                                    p.snr_db, p.bandwidth_hz, self.tab_label)
        self.tracker.prune(DETECTION_MAX_AGE_S)
        items = [d for d in self.tracker.confirmed_items(MIN_HITS)
                 if d.source == self.tab_label]
        window = self.tracker.recent_passes(OCCUPANCY_PASSES)
        self.table.update_rows(items, self.default_raster_hz, passes=window)
        active = sum(1 for d in items if d.is_active())
        pending = sum(1 for d in self.tracker.items()
                      if d.source == self.tab_label and d.hits < MIN_HITS)
        text = "%d carriers detected  (%d active now)" % (len(items), active)
        if pending:
            text += "   %d unconfirmed" % pending
        self.lbl_count.setText(text)
        top = sorted(peaks, key=lambda p: p.level_dbfs, reverse=True)[:20]
        self.plot.add_markers([p.freq_hz for p in top], None)
        if self.timeline is not None:
            self.timeline.set_data(items, self.tracker.recent_passes(120))
        self.app.report_detections(items, self.tab_label)
