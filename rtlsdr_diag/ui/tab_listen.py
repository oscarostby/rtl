"""Listen tab: demodulate and play FM broadcast, narrow FM and AM."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from ..config import DAB_BLOCKS, MAX_TUNE_HZ, MIN_TUNE_HZ, dab_block_for
from ..sdr.demod import (MODE_AM, MODE_NFM, MODE_SAMPLE_RATE, MODE_WFM, MODES)
from ..sdr.engine import MODE_SPECTRUM, AcqConfig
from . import theme, widgets
from .audio import AudioPlayer

MODE_HELP = {
    MODE_WFM: "Wide FM - broadcast radio, 87.5-108 MHz, 180 kHz wide.",
    MODE_NFM: "Narrow FM - amateur, marine and PMR traffic, 12.5 kHz channels.",
    MODE_AM: "AM - aviation airband, 118-137 MHz.",
}

PRESETS = [
    ("FM broadcast 100.0", 100.0e6, MODE_WFM),
    ("FM broadcast 93.5", 93.5e6, MODE_WFM),
    ("Airband 121.5 (AM)", 121.5e6, MODE_AM),
    ("2 m calling 145.500 (NFM)", 145.5e6, MODE_NFM),
    ("70 cm 433.500 (NFM)", 433.5e6, MODE_NFM),
]

DAB_NOTE = (
    "DAB+ cannot be played by this application. Unlike FM it is not a matter of "
    "demodulating a carrier: a DAB ensemble needs OFDM synchronisation, DQPSK "
    "demodulation, Viterbi decoding, time de-interleaving, Reed-Solomon "
    "correction and finally an HE-AAC v2 audio decoder. That is a large "
    "specialist codebase. Use "
    "welle.io (welle.io) or dablin - both are free, both work with this same "
    "RTL-SDR, and welle.io will also show you the ensemble contents. What this "
    "application can tell you is whether a block is present and how strong it "
    "is: use the Scanner on 174-240 MHz. The selector below provides the exact "
    "standard block centres."
)


class ListenTab(QWidget):
    tab_label = "Listen"

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.player = AudioPlayer()
        self._level_db = -200.0

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        root.addLayout(self._build_toolbar())

        # --- level -----------------------------------------------------
        level_box = widgets.group("Channel level")
        self.meter = widgets.SignalMeter(-90.0, 0.0)
        level_box.layout().addWidget(self.meter)
        self.lbl_level = QLabel("--- dBFS")
        f = self.lbl_level.font()
        f.setPointSize(20)
        f.setBold(True)
        self.lbl_level.setFont(f)
        self.lbl_level.setAlignment(Qt.AlignCenter)
        self.lbl_level.setStyleSheet("color: %s;" % theme.TRACE)
        level_box.layout().addWidget(self.lbl_level)
        root.addWidget(level_box)

        # --- status ----------------------------------------------------
        info_box = widgets.group("Audio")
        grid = QGridLayout()
        grid.setSpacing(10)
        self.card_mode = widgets.MetricCard("Mode", "WFM")
        self.card_freq = widgets.MetricCard("Tuned", "--", "MHz")
        self.card_rate = widgets.MetricCard("Audio rate", "--", "Hz")
        self.card_device = widgets.MetricCard("Output", "--")
        self.card_drop = widgets.MetricCard("Dropped blocks", "0")
        for i, card in enumerate((self.card_mode, self.card_freq, self.card_rate,
                                  self.card_device, self.card_drop)):
            grid.addWidget(card, 0, i)
        holder = QWidget()
        holder.setLayout(grid)
        info_box.layout().addWidget(holder)
        self.lbl_help = widgets.dim_label(MODE_HELP[MODE_WFM])
        info_box.layout().addWidget(self.lbl_help)
        root.addWidget(info_box)

        # --- DAB -------------------------------------------------------
        dab_box = widgets.group("DAB+")
        dab_text = QLabel(DAB_NOTE)
        dab_text.setWordWrap(True)
        dab_text.setStyleSheet(
            "background: rgba(240,160,32,0.08); border: 1px solid %s; "
            "border-radius: 8px; padding: 10px; color: %s; font-size: 12px;"
            % (theme.WARN, theme.TEXT))
        dab_box.layout().addWidget(dab_text)
        row = QHBoxLayout()
        row.addWidget(QLabel("Jump to block"))
        self.dab_block = QComboBox()
        self.dab_block.addItem("Select a DAB block...", None)
        for name, mhz in DAB_BLOCKS:
            self.dab_block.addItem("%s  %.3f MHz" % (name, mhz), mhz * 1e6)
        self.dab_block.currentIndexChanged.connect(self._jump_to_block)
        row.addWidget(self.dab_block)
        row.addStretch(1)
        rh = QWidget()
        rh.setLayout(row)
        dab_box.layout().addWidget(rh)
        root.addWidget(dab_box)

        root.addStretch(1)
        self.status = widgets.dim_label("")
        root.addWidget(self.status)

        self._mode_changed()

    # ------------------------------------------------------------------
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.btn_listen = QPushButton("Listen")
        self.btn_listen.setProperty("accent", True)
        self.btn_listen.clicked.connect(self._toggle)
        bar.addWidget(self.btn_listen)

        bar.addWidget(QLabel("Mode"))
        self.mode = QComboBox()
        for m in MODES:
            self.mode.addItem(m, m)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        bar.addWidget(self.mode)

        bar.addWidget(QLabel("Frequency"))
        self.freq = widgets.freq_spin(100.0, MIN_TUNE_HZ / 1e6, MAX_TUNE_HZ / 1e6,
                                      decimals=4, step=0.1)
        self.freq.valueChanged.connect(self._freq_changed)
        bar.addWidget(self.freq)

        self.preset = QComboBox()
        self.preset.addItem("Presets...", None)
        for name, hz, mode in PRESETS:
            self.preset.addItem(name, (hz, mode))
        self.preset.currentIndexChanged.connect(self._preset_changed)
        bar.addWidget(self.preset)

        bar.addWidget(QLabel("Volume"))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(60)
        self.volume.setFixedWidth(120)
        self.volume.valueChanged.connect(self._volume_changed)
        bar.addWidget(self.volume)

        bar.addWidget(QLabel("Squelch"))
        self.squelch = QDoubleSpinBox()
        self.squelch.setRange(-100.0, 0.0)
        self.squelch.setValue(-100.0)
        self.squelch.setSuffix(" dBFS")
        self.squelch.setToolTip("Mute the audio when the channel is quieter "
                                "than this. -100 dBFS means never mute.")
        self.squelch.valueChanged.connect(self._squelch_changed)
        bar.addWidget(self.squelch)

        bar.addStretch(1)
        return bar

    # ------------------------------------------------------------------
    def config(self) -> AcqConfig:
        mode = self.mode.currentData()
        return AcqConfig(
            mode=MODE_SPECTRUM,
            center_hz=self.freq.value() * 1e6,
            sample_rate=MODE_SAMPLE_RATE[mode],
            gain="auto", fft_size=4096, averages=1, detect=False,
            audio=True, audio_mode=mode, audio_offset_hz=0.0,
            squelch_db=float(self.squelch.value()),
            volume=1.0,
            label=self.tab_label)

    def _toggle(self) -> None:
        if self.app.is_running_here(self.tab_label):
            self.app.engine_stop()
            self.player.stop()
            self.status.setText("Stopped.")
            return
        block = dab_block_for(self.freq.value() * 1e6)
        if block:
            self.status.setText(
                "DAB block %s is digital and cannot be played here. Use the "
                "Scanner to check its RF signal level." % block)
            return
        if not self.player.is_available:
            self.status.setText("No audio output is available: %s"
                                % self.player.last_error)
            return
        if not self.player.start(48_000):
            self.status.setText("Could not open the audio device: %s"
                                % self.player.last_error)
            return
        self.player.set_volume(self.volume.value() / 100.0)
        self.card_device.set_value(self.player.device_name()[:22])
        self.app.reset_audio()
        self.app.engine_start(self.config())
        self.status.setText("Listening on %.4f MHz in %s."
                            % (self.freq.value(), self.mode.currentData()))

    def set_running(self, running: bool) -> None:
        self.btn_listen.setText("Stop" if running else "Listen")
        if not running:
            self.player.stop()

    # ------------------------------------------------------------------
    def _mode_changed(self) -> None:
        mode = self.mode.currentData()
        self.card_mode.set_value(mode)
        self.lbl_help.setText(MODE_HELP[mode])
        if self.app.is_running_here(self.tab_label):
            # The capture rate differs per mode, so restart cleanly.
            self.app.reset_audio()
            self.app.engine_start(self.config())

    def _freq_changed(self, mhz: float) -> None:
        self.card_freq.set_value("%.4f" % mhz)
        block = dab_block_for(mhz * 1e6)
        if block:
            if self.app.is_running_here(self.tab_label):
                # Do not silently retune an analog demodulator onto a DAB
                # ensemble and send digital noise to the speakers.
                self.app.engine_stop()
                self.player.stop()
            self.status.setText(
                "%.3f MHz is DAB block %s - this application cannot play DAB+; "
                "see the note below." % (mhz, block))
        elif self.app.is_running_here(self.tab_label):
            self.app.engine_update({"center_hz": mhz * 1e6})

    def _preset_changed(self, index: int) -> None:
        data = self.preset.currentData()
        if data:
            hz, mode = data
            idx = self.mode.findData(mode)
            if idx >= 0:
                self.mode.setCurrentIndex(idx)
            self.freq.setValue(hz / 1e6)
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(0)
        self.preset.blockSignals(False)

    def _volume_changed(self, value: int) -> None:
        self.player.set_volume(value / 100.0)

    def _squelch_changed(self, value: float) -> None:
        self.app.engine_update({"squelch_db": float(value)})

    def _jump_to_block(self, index: int) -> None:
        hz = self.dab_block.currentData()
        if hz:
            self.freq.setValue(hz / 1e6)
        self.dab_block.blockSignals(True)
        self.dab_block.setCurrentIndex(0)
        self.dab_block.blockSignals(False)

    def set_center_mhz(self, mhz: float) -> None:
        self.freq.setValue(mhz)

    # ------------------------------------------------------------------
    def on_audio(self, pcm: bytes, rate: int) -> None:
        self.player.write(pcm)
        self.card_rate.set_value(str(rate))
        stats = self.player.stats()
        self.card_drop.set_value(str(stats["dropped_blocks"]),
                                 theme.WARN if stats["dropped_blocks"] else None)

    def on_audio_level(self, level_db: float, squelched: bool) -> None:
        self._level_db = level_db
        self.meter.set_values(level_db, None, -90.0)
        self.lbl_level.setText("%.1f dBFS%s"
                               % (level_db, "   (squelched)" if squelched else ""))
        color = theme.TEXT_FAINT if squelched else (
            theme.GOOD if level_db > -50 else theme.WARN)
        self.lbl_level.setStyleSheet("color: %s;" % color)

    def update_frame(self, frame) -> None:
        pass
