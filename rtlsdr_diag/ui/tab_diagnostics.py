"""Diagnostics: device internals, buffer statistics and driver help."""
from __future__ import annotations

import datetime as _dt
import platform
import sys

from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QTextEdit, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..sdr import device as dev
from ..sdr import dll_loader
from . import driver_help, theme, widgets


class DiagnosticsTab(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._device_info: dict = {}
        self._stats: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # --- toolbar -----------------------------------------------------------
        bar = QHBoxLayout()
        self.btn_copy = QPushButton("Copy Diagnostics")
        self.btn_copy.setProperty("accent", True)
        self.btn_copy.clicked.connect(self.copy_diagnostics)
        bar.addWidget(self.btn_copy)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.app.rescan_devices)
        bar.addWidget(self.btn_refresh)

        self.btn_reopen = QPushButton("Reopen device")
        self.btn_reopen.clicked.connect(self.app.reopen_device)
        bar.addWidget(self.btn_reopen)

        self.chk_sim = QCheckBox("Simulation mode (no hardware)")
        self.chk_sim.toggled.connect(self.app.set_simulation)
        bar.addWidget(self.chk_sim)

        self.chk_sim_ant = QCheckBox("Simulated antenna connected")
        self.chk_sim_ant.setChecked(True)
        self.chk_sim_ant.setEnabled(False)
        self.chk_sim_ant.toggled.connect(self.app.set_sim_antenna)
        bar.addWidget(self.chk_sim_ant)

        bar.addStretch(1)
        self.lbl_copied = QLabel("")
        self.lbl_copied.setStyleSheet("color: %s;" % theme.GOOD)
        bar.addWidget(self.lbl_copied)
        root.addLayout(bar)

        # --- report ------------------------------------------------------------
        report_box = widgets.group("Diagnostics report")
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(430)
        report_box.layout().addWidget(self.text)
        root.addWidget(report_box)

        # --- driver help --------------------------------------------------------
        help_box = widgets.group("Windows driver help (Zadig / WinUSB)")
        help_box.layout().addWidget(driver_help.DriverHelpWidget(compact=True))
        root.addWidget(help_box)
        root.addStretch(1)

        self.refresh_text()

    # ------------------------------------------------------------------
    def update_device_state(self, info: dict) -> None:
        self._device_info = dict(info)
        self.chk_sim.blockSignals(True)
        self.chk_sim.setChecked(bool(info.get("simulated")))
        self.chk_sim.blockSignals(False)
        self.chk_sim_ant.setEnabled(bool(info.get("simulated")))
        self.refresh_text()

    def update_stats(self, stats: dict) -> None:
        self._stats = dict(stats)
        self.refresh_text()

    # ------------------------------------------------------------------
    def build_report(self) -> str:
        info = self._device_info
        stats = self._stats
        L: list[str] = []
        add = L.append

        add("RTL-SDR DIAGNOSTICS REPORT")
        add("Generated: %s" % _dt.datetime.now().isoformat(timespec="seconds"))
        add("=" * 68)
        add("")
        add("[ Application ]")
        add("  Version              : %s" % __version__)
        add("  Python               : %s" % sys.version.split()[0])
        add("  Platform             : %s %s" % (platform.system(), platform.release()))
        add("  Machine              : %s" % platform.machine())
        try:
            import PySide6
            add("  PySide6              : %s" % PySide6.__version__)
        except Exception:
            pass
        try:
            import numpy
            add("  numpy                : %s" % numpy.__version__)
        except Exception:
            pass
        try:
            import pyqtgraph
            add("  pyqtgraph            : %s" % pyqtgraph.__version__)
        except Exception:
            pass
        add("")

        add("[ RTL-SDR library ]")
        add("  Backend in use       : %s" % dev.BACKEND)
        add("  pyrtlsdr             : %s (optional)" % dev.PYRTLSDR_VERSION)
        add("  librtlsdr loadable   : %s" % ("yes" if dev.library_available() else "no"))
        add("  rtlsdr.dll path      : %s" % (dll_loader.found_dll() or "not found"))
        try:
            from ..sdr import native
            lib = native.load()
            if lib is not None and lib.missing:
                add("  absent (optional)    : %s" % ", ".join(lib.missing))
            if native.load_error():
                add("  native load error    : %s" % native.load_error())
        except Exception as exc:
            add("  native probe failed  : %s" % exc)
        if dev.RTLSDR_IMPORT_ERROR:
            add("  pyrtlsdr error       : %s" % dev.RTLSDR_IMPORT_ERROR)
        for line in dll_loader.report():
            add("  loader               : %s" % line)
        add("")

        add("[ USB device ]")
        add("  Devices on USB       : %s" % info.get("usb_device_count", "?"))
        add("  Simulation mode      : %s" % ("ON" if info.get("simulated") else "off"))
        add("  Connected / open     : %s" % ("yes" if info.get("connected") else "no"))
        add("  Device index         : %s" % info.get("device_index", "-"))
        add("  Name                 : %s" % (info.get("name") or "-"))
        add("  Manufacturer         : %s" % (info.get("manufacturer") or "-"))
        add("  Product              : %s" % (info.get("product") or "-"))
        add("  Serial               : %s" % (info.get("serial") or "-"))
        add("  Tuner                : %s" % (info.get("tuner") or "-"))
        if not info.get("simulated"):
            for d in dev.enumerate_devices():
                add("  Enumerated           : %s" % d.label())
        add("")

        add("[ Receiver settings ]")
        cf = info.get("center_freq_hz")
        sr = info.get("sample_rate_hz")
        add("  Centre frequency     : %s" % ("%.6f MHz" % (cf / 1e6) if cf else "-"))
        add("  Sample rate          : %s" % ("%.6f MS/s" % (sr / 1e6) if sr else "-"))
        add("  Gain                 : %s" % info.get("gain_db", "-"))
        add("  Frequency correction : %s ppm" % info.get("ppm", "-"))
        add("")

        st = self.app.acquisition_status()
        add("[ Acquisition ]")
        add("  State                : %s" % st.state.value)
        add("  Headline             : %s" % st.headline())
        add("  Acquisition owner    : %s" % (st.owner or "none"))
        add("  Centre frequency     : %s" % (
            "%.6f MHz" % (st.center_hz / 1e6) if st.center_hz else "-"))
        if st.stop_hz > st.start_hz > 0:
            add("  Sweep range          : %.4f - %.4f MHz"
                % (st.start_hz / 1e6, st.stop_hz / 1e6))
        add("  Gain                 : %s" % (
            "AUTO" if isinstance(st.gain_db, str) else "%.1f dB" % float(st.gain_db)))
        add("  PPM correction       : %+d" % st.ppm)
        add("")
        add("[ Live detections ]")
        store = self.app.signal_store
        add("  Tracked signals      : %d" % len(store))
        add("  Active now           : %d" % store.active_count())
        add("  Sweep passes         : %d" % store.pass_index)
        add("  Match tolerance      : %.0f Hz" % store.tolerance_hz)
        add("  Classes present      : %s" % (", ".join(store.classes_present())
                                             or "none"))
        add("")
        add("[ Acquisition / buffers ]")
        add("  Acquisition running  : %s" % ("yes" if stats.get("running") else "no"))
        add("  Mode                 : %s" % stats.get("mode", "-"))
        add("  Block size           : %s samples" % stats.get("block_size", "-"))
        add("  Frames processed     : %s" % stats.get("frames", 0))
        add("  Frame rate           : %s frames/s" % stats.get("fps", 0))
        add("  Frames skipped (UI)  : %s" % stats.get("frames_skipped", 0))
        add("  Last sample at       : %s" % stats.get("last_sample_time", "-"))
        add("    note: frames dropped because the display was still busy. Samples")
        add("    were still analysed; only the redraw was skipped.")
        add("  Total samples read   : %s" % stats.get("total_samples", 0))
        add("  Dropped samples (est): %s" % stats.get("dropped_estimate", 0))
        add("    note: librtlsdr does not report drops directly; this is estimated")
        add("    from the gap between consecutive synchronous reads.")
        add("")

        add("[ Last device error ]")
        add("  %s" % (info.get("last_error") or stats.get("last_error") or "none"))
        add("")

        add("[ CSV logging ]")
        add("  Active               : %s" % ("yes" if self.app.logger.is_logging else "no"))
        add("  File                 : %s" % (self.app.logger.path or "-"))
        add("  Rows written         : %s" % self.app.logger.rows_written)
        add("")
        add("=" * 68)
        add(driver_help.help_text_plain())
        return "\n".join(L)

    def refresh_text(self) -> None:
        self.text.setPlainText(self.build_report())

    def copy_diagnostics(self) -> None:
        self.refresh_text()
        QApplication.clipboard().setText(self.build_report())
        self.lbl_copied.setText("Diagnostics copied to clipboard")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2500, lambda: self.lbl_copied.setText(""))
