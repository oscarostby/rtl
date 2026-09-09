"""Dashboard: device status at a glance, hardware test and antenna test."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QTextEdit, QVBoxLayout, QWidget)

from ..config import LEGAL_NOTICE
from ..sdr.engine import MODE_SPECTRUM, AcqConfig
from . import driver_help, theme, widgets
from .tables import DetectionTable


class DashboardTab(QWidget):
    tab_label = "Dashboard monitor"

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(self.scroll)

        body = QWidget()
        self.scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        # --- big status ------------------------------------------------------
        self.pill = widgets.StatusPill("CHECKING FOR RTL-SDR...")
        root.addWidget(self.pill)

        # --- action buttons --------------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_test = QPushButton("Run Hardware Test")
        self.btn_test.setProperty("accent", True)
        self.btn_test.clicked.connect(self.app.run_hardware_test)
        self.btn_antenna = QPushButton("Antenna Connected?")
        self.btn_antenna.clicked.connect(self.app.run_antenna_test)
        self.btn_refresh = QPushButton("Rescan USB")
        self.btn_refresh.clicked.connect(self.app.rescan_devices)
        self.btn_monitor = QPushButton("Start Monitoring")
        self.btn_monitor.clicked.connect(self._toggle_monitor)
        self.btn_driver = QPushButton("Driver Help")
        self.btn_driver.clicked.connect(lambda: driver_help.show_driver_help(self))
        for b in (self.btn_test, self.btn_antenna, self.btn_monitor,
                  self.btn_refresh, self.btn_driver):
            actions.addWidget(b)
        actions.addStretch(1)
        root.addLayout(actions)

        # --- device information ---------------------------------------------
        dev_box, dev_form = widgets.form_group("USB receiver")
        self.lbl_count = QLabel("--")
        self.lbl_name = QLabel("--")
        self.lbl_tuner = QLabel("--")
        self.lbl_serial = QLabel("--")
        self.lbl_conn = QLabel("--")
        self.lbl_lib = QLabel("--")
        for lbl in (self.lbl_count, self.lbl_name, self.lbl_tuner,
                    self.lbl_serial, self.lbl_conn, self.lbl_lib):
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lbl.setWordWrap(True)
        dev_form.addRow("Devices found", self.lbl_count)
        dev_form.addRow("Device name", self.lbl_name)
        dev_form.addRow("Tuner", self.lbl_tuner)
        dev_form.addRow("Serial", self.lbl_serial)
        dev_form.addRow("Connection", self.lbl_conn)
        dev_form.addRow("Driver library", self.lbl_lib)
        root.addWidget(dev_box)

        # --- live metrics ----------------------------------------------------
        metrics_box = widgets.group("Live receiver state")
        grid = QGridLayout()
        grid.setSpacing(10)
        self.card_freq = widgets.MetricCard("Current frequency", "--", "MHz")
        self.card_level = widgets.MetricCard("Peak signal level", "--", "dBFS")
        self.card_noise = widgets.MetricCard("Noise floor", "--", "dBFS")
        self.card_snr = widgets.MetricCard("Peak SNR", "--", "dB")
        self.card_signals = widgets.MetricCard("Detected signals", "0", "carriers")
        self.card_rate = widgets.MetricCard("Sample rate", "--", "MS/s")
        for i, card in enumerate((self.card_freq, self.card_level, self.card_noise,
                                  self.card_snr, self.card_signals, self.card_rate)):
            grid.addWidget(card, i // 3, i % 3)
        inner = QWidget()
        inner.setLayout(grid)
        metrics_box.layout().addWidget(inner)
        root.addWidget(metrics_box)

        # --- detections ------------------------------------------------------
        det_box = widgets.group("Detected signals (RF energy only)")
        self.table = DetectionTable(compact=True)
        self.table.setMinimumHeight(160)
        self.table.tune_requested.connect(self.app.tune_to)
        det_box.layout().addWidget(self.table)
        det_box.layout().addWidget(widgets.dim_label(LEGAL_NOTICE, italic=True))
        root.addWidget(det_box)

        # --- test output -----------------------------------------------------
        self.test_box = widgets.group("Test results")
        self.test_output = QTextEdit()
        self.test_output.setReadOnly(True)
        self.test_output.setMinimumHeight(200)
        self.test_output.setPlainText(
            "No test has been run yet.\n\n"
            "Press \"Run Hardware Test\" to open the receiver, tune it, capture IQ "
            "samples and verify that data is really arriving.\n"
            "Press \"Antenna Connected?\" for a heuristic check of the antenna path.")
        self.test_box.layout().addWidget(self.test_output)
        root.addWidget(self.test_box)

        # --- driver help (only shown when the device will not open) ----------
        self.help_widget = driver_help.DriverHelpWidget(compact=True)
        self.help_widget.setVisible(False)
        root.addWidget(self.help_widget)
        root.addStretch(1)

    # ------------------------------------------------------------------
    def _toggle_monitor(self) -> None:
        if self.app.is_running_here(self.tab_label):
            self.app.engine_stop()
        else:
            cfg = AcqConfig(mode=MODE_SPECTRUM, center_hz=100.0e6,
                            sample_rate=2.048e6, detect=True,
                            label=self.tab_label)
            self.app.engine_start(cfg)

    def set_running(self, running: bool) -> None:
        self.btn_monitor.setText("Stop Monitoring" if running else "Start Monitoring")

    # ------------------------------------------------------------------
    def update_device_state(self, info: dict) -> None:
        connected = bool(info.get("connected"))
        count = int(info.get("usb_device_count") or 0)
        simulated = bool(info.get("simulated"))

        if simulated:
            self.pill.set_state("warn", "SIMULATION MODE",
                                "No hardware in use - synthetic RF for UI testing")
        elif connected:
            self.pill.set_state("good", "RTL-SDR CONNECTED",
                                info.get("name", "") or "Device open")
        elif count > 0:
            self.pill.set_state("warn", "RTL-SDR FOUND BUT NOT OPEN",
                                info.get("last_error", "") or "Press Run Hardware Test")
        else:
            self.pill.set_state("bad", "RTL-SDR NOT FOUND",
                                "No RTL-SDR detected on USB")

        self.lbl_count.setText(str(count))
        self.lbl_name.setText(info.get("name") or "--")
        self.lbl_tuner.setText(info.get("tuner") or "--")
        self.lbl_serial.setText(info.get("serial") or "--")
        state = ("Open (simulated)" if simulated and connected else
                 "Open" if connected else
                 "Detected, not open" if count else "Not connected")
        color = theme.GOOD if connected else (theme.WARN if count else theme.BAD)
        self.lbl_conn.setText(state)
        self.lbl_conn.setStyleSheet("color: %s; font-weight: 600;" % color)
        self.lbl_lib.setText(self.app.library_status_text())

        needs_help = (not simulated) and (not connected)
        self.help_widget.setVisible(needs_help)

    def update_frame(self, frame) -> None:
        peak = float(np.max(frame.power_db)) if frame.power_db.size else float("nan")
        nf = frame.noise_floor_db
        self.card_freq.set_value("%.4f" % (frame.center_hz / 1e6))
        self.card_level.set_value("%.1f" % peak)
        self.card_noise.set_value("%.1f" % nf)
        snr = peak - nf
        color = theme.GOOD if snr >= 20 else (theme.WARN if snr >= 8 else theme.TEXT)
        self.card_snr.set_value("%.1f" % snr, color)
        self.card_rate.set_value("%.3f" % (frame.sample_rate / 1e6))

    def update_detections(self, detections) -> None:
        self.card_signals.set_value(str(len(detections)))
        self.table.update_rows(detections)

    def show_test_report(self, report: dict) -> None:
        kind = report.get("kind", "test")
        lines = []
        if kind == "antenna":
            lines.append("ANTENNA CHECK: %s" % report.get("verdict", "?"))
            lines.append("Confidence: %s" % report.get("confidence", "?"))
        else:
            lines.append("HARDWARE TEST: %s" % ("PASSED" if report.get("ok") else "FAILED"))
        lines.append("-" * 72)
        for step in report.get("steps", []):
            mark = "[ OK ]" if step.get("ok") else "[FAIL]"
            lines.append("%s  %s" % (mark, step.get("name", "")))
            if step.get("detail"):
                lines.append("        %s" % step["detail"])
        lines.append("-" * 72)
        lines.append(report.get("summary", ""))
        if report.get("disclaimer"):
            lines.append("")
            lines.append("NOTE: " + report["disclaimer"])
        metrics = report.get("metrics") or {}
        if metrics and kind != "antenna":
            lines.append("")
            lines.append("Measured values:")
            for key in ("device_count", "sample_rate_hz", "samples_received",
                        "capture_seconds", "effective_rate_hz", "average_power_dbfs",
                        "noise_floor_dbfs", "peak_dbfs", "peak_to_noise_db",
                        "dc_offset", "clipping_pct", "std", "unique_levels"):
                if key in metrics:
                    lines.append("    %-22s %s" % (key, metrics[key]))
        lines.append("")
        lines.append("Completed in %.2f s" % report.get("duration_s", 0.0))
        self.test_output.setPlainText("\n".join(lines))
        # The results sit below the fold on smaller windows - bring them up.
        self.scroll.ensureWidgetVisible(self.test_box)

    def show_test_progress(self, message: str) -> None:
        self.test_output.setPlainText("Running...\n\n%s" % message)
