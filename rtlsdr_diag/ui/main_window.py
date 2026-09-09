"""Main window: owns the worker thread and routes signals to the tabs."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QLabel, QMainWindow, QMessageBox,
                               QStatusBar, QTabWidget, QWidget)

from .. import APP_NAME, __version__
from ..core.acquisition import AcquisitionState, AcquisitionStatus, state_for
from ..core.csvlog import CsvLogger
from ..core.signal_store import SignalStore
from ..sdr import device as dev
from ..sdr.engine import MODE_SPECTRUM, AcqConfig, SdrEngine
from . import driver_help, theme
from .tab_dashboard import DashboardTab
from .tab_diagnostics import DiagnosticsTab
from .tab_listen import ListenTab
from .tab_meter import MeterTab
from .tab_radar import RadarTab
from .tab_smart import SmartScannerTab
from .tab_scanner import ScannerTab
from .tab_spectrum import SpectrumTab
from .tab_tetra import TetraTab
from .tab_waterfall import WaterfallTab


class MainWindow(QMainWindow):
    # Requests sent to the engine thread (queued connections).
    _start_requested = Signal(object)
    _stop_requested = Signal()
    _update_requested = Signal(dict)
    _open_requested = Signal()
    _hw_test_requested = Signal()
    _ant_test_requested = Signal()
    _sim_requested = Signal(bool)
    _sim_ant_requested = Signal(bool)
    _state_requested = Signal()
    _close_requested = Signal()
    _ack_frame = Signal()
    _reset_audio = Signal()

    def __init__(self, start_simulated: bool = False):
        super().__init__()
        self.setWindowTitle("%s  v%s" % (APP_NAME, __version__))
        self.resize(1440, 920)
        self.setMinimumSize(1120, 720)

        self.logger = CsvLogger()
        # One store for every live detection, and one description of what the
        # receiver is doing. Every page reads these rather than keeping its own.
        self.signal_store = SignalStore()
        self.acq_status = AcquisitionStatus()
        self._last_error_text = ""
        self.engine_running = False
        self.running_label = ""
        self._pending_label = ""
        self._last_device_info: dict = {}

        self._driver_help_shown = False
        self._last_center_hz: float | None = None
        self._last_peak_dbfs: float | None = None
        self._last_noise_dbfs: float | None = None
        self._status_listeners: list = []
        self._running_mode = ""
        self._running_start_hz = 0.0
        self._running_stop_hz = 0.0

        # --- worker thread -----------------------------------------------------
        self.thread = QThread(self)
        self.thread.setObjectName("sdr-acquisition")
        self.engine = SdrEngine()
        self.engine.moveToThread(self.thread)
        self.thread.start()

        # --- tabs ---------------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.dashboard_tab = DashboardTab(self)
        self.spectrum_tab = SpectrumTab(self)
        self.waterfall_tab = WaterfallTab(self)
        self.tetra_tab = TetraTab(self)
        self.meter_tab = MeterTab(self)
        self.scanner_tab = ScannerTab(self)
        self.smart_tab = SmartScannerTab(self)
        self.radar_tab = RadarTab(self)
        self.listen_tab = ListenTab(self)
        self.diagnostics_tab = DiagnosticsTab(self)
        for widget, title in (
            (self.dashboard_tab, "Dashboard"),
            (self.spectrum_tab, "Spectrum"),
            (self.waterfall_tab, "Waterfall"),
            (self.tetra_tab, "TETRA RF Check"),
            (self.meter_tab, "Signal Meter"),
            (self.scanner_tab, "Scanner"),
            (self.smart_tab, "Smart Scanner"),
            (self.radar_tab, "RF Map"),
            (self.listen_tab, "Listen"),
            (self.diagnostics_tab, "Diagnostics"),
        ):
            self.tabs.addTab(widget, title)
        self.setCentralWidget(self.tabs)

        self._build_status_bar()
        self._build_menu()
        self._connect_engine()

        # --- background device watchdog ----------------------------------------
        self._watch_count = -1
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(2000)
        self.watchdog.timeout.connect(self._poll_devices)
        self.watchdog.start()

        QTimer.singleShot(150, lambda: self._startup(start_simulated))

    # ------------------------------------------------------------------
    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self.status_device = QLabel("Device: checking...")
        self.status_mode = QLabel("Idle")
        self.status_log = QLabel("Logging: off")
        self.status_msg = QLabel("")
        for w in (self.status_device, self.status_mode, self.status_log):
            w.setStyleSheet("padding: 0 12px; color: %s;" % theme.TEXT_DIM)
        bar.addWidget(self.status_msg, 1)
        bar.addPermanentWidget(self.status_device)
        bar.addPermanentWidget(self.status_mode)
        bar.addPermanentWidget(self.status_log)

    def _build_menu(self) -> None:
        menu = self.menuBar()
        m_dev = menu.addMenu("&Device")
        act = QAction("Rescan USB", self)
        act.setShortcut(QKeySequence("F5"))
        act.triggered.connect(self.rescan_devices)
        m_dev.addAction(act)
        act = QAction("Reopen device", self)
        act.triggered.connect(self.reopen_device)
        m_dev.addAction(act)
        m_dev.addSeparator()
        act = QAction("Run Hardware Test", self)
        act.triggered.connect(self.run_hardware_test)
        m_dev.addAction(act)
        act = QAction("Antenna Connected?", self)
        act.triggered.connect(self.run_antenna_test)
        m_dev.addAction(act)
        m_dev.addSeparator()
        self.act_sim = QAction("Simulation mode", self, checkable=True)
        self.act_sim.triggered.connect(self.set_simulation)
        m_dev.addAction(self.act_sim)
        m_dev.addSeparator()
        act = QAction("Quit", self)
        act.setShortcut(QKeySequence.Quit)
        act.triggered.connect(self.close)
        m_dev.addAction(act)

        m_log = menu.addMenu("&Logging")
        act = QAction("Start logging to CSV...", self)
        act.triggered.connect(lambda: self.start_logging("Manual", ask=True))
        m_log.addAction(act)
        act = QAction("Stop logging", self)
        act.triggered.connect(self.stop_logging)
        m_log.addAction(act)

        m_help = menu.addMenu("&Help")
        act = QAction("Windows driver help (Zadig)", self)
        act.triggered.connect(lambda: driver_help.show_driver_help(self))
        m_help.addAction(act)
        act = QAction("About", self)
        act.triggered.connect(self._about)
        m_help.addAction(act)

    def _connect_engine(self) -> None:
        e = self.engine
        self._start_requested.connect(e.start, Qt.QueuedConnection)
        self._stop_requested.connect(e.stop, Qt.QueuedConnection)
        self._update_requested.connect(e.update_settings, Qt.QueuedConnection)
        self._open_requested.connect(e.open_device, Qt.QueuedConnection)
        self._hw_test_requested.connect(e.run_hardware_test, Qt.QueuedConnection)
        self._ant_test_requested.connect(e.run_antenna_test, Qt.QueuedConnection)
        self._sim_requested.connect(e.set_simulation, Qt.QueuedConnection)
        self._sim_ant_requested.connect(e.set_sim_antenna, Qt.QueuedConnection)
        self._state_requested.connect(e.emit_device_state, Qt.QueuedConnection)
        self._close_requested.connect(e.close_device, Qt.BlockingQueuedConnection)
        self._ack_frame.connect(e.ack_frame, Qt.QueuedConnection)

        e.frame_ready.connect(self._on_frame, Qt.QueuedConnection)
        e.sweep_ready.connect(self._on_sweep, Qt.QueuedConnection)
        e.peaks_ready.connect(self._on_peaks, Qt.QueuedConnection)
        e.device_state.connect(self._on_device_state, Qt.QueuedConnection)
        e.stats_updated.connect(self.diagnostics_tab.update_stats, Qt.QueuedConnection)
        e.status_changed.connect(self._on_status, Qt.QueuedConnection)
        e.error_occurred.connect(self._on_error, Qt.QueuedConnection)
        e.running_changed.connect(self._on_running, Qt.QueuedConnection)
        e.test_progress.connect(self.dashboard_tab.show_test_progress, Qt.QueuedConnection)
        e.test_finished.connect(self._on_test_finished, Qt.QueuedConnection)
        e.audio_ready.connect(self.listen_tab.on_audio, Qt.QueuedConnection)
        e.audio_level.connect(self.listen_tab.on_audio_level, Qt.QueuedConnection)
        self._reset_audio.connect(e.reset_audio, Qt.QueuedConnection)

    # ------------------------------------------------------------------
    def _startup(self, start_simulated: bool) -> None:
        if start_simulated:
            self.set_simulation(True)
            self.status_msg.setText("Started in simulation mode.")
            return
        if not dev.library_available():
            self.status_msg.setText(
                "librtlsdr could not be loaded - see the Diagnostics tab.")
            self._state_requested.emit()
            QTimer.singleShot(400, self._offer_simulation)
            return
        if dev.device_count() == 0:
            self.status_msg.setText("No RTL-SDR found on USB.")
            self._state_requested.emit()
            QTimer.singleShot(400, self._offer_simulation)
            return
        self._open_requested.emit()

    def _offer_simulation(self) -> None:
        if self._driver_help_shown:
            return
        self._driver_help_shown = True
        box = QMessageBox(self)
        box.setWindowTitle("No RTL-SDR available")
        box.setIcon(QMessageBox.Warning)
        reason = ("The librtlsdr driver library could not be loaded."
                  if not dev.library_available() else
                  "No RTL-SDR device was found on USB.")
        box.setText(reason)
        box.setInformativeText(
            "You can still explore the whole interface using simulation mode, "
            "which generates synthetic RF - no hardware and no real reception.\n\n"
            "To use the real receiver, plug it in and install the WinUSB driver "
            "with Zadig (Help > Windows driver help).")
        sim_btn = box.addButton("Start simulation mode", QMessageBox.AcceptRole)
        help_btn = box.addButton("Driver help", QMessageBox.HelpRole)
        box.addButton("Continue without device", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is sim_btn:
            self.set_simulation(True)
        elif clicked is help_btn:
            driver_help.show_driver_help(self)

    def _poll_devices(self) -> None:
        if self._last_device_info.get("simulated"):
            return
        try:
            count = dev.device_count()
        except Exception:
            count = 0
        if count == self._watch_count:
            return
        previous = self._watch_count
        self._watch_count = count
        if previous < 0:
            return
        if count > previous:
            self.status_msg.setText("RTL-SDR connected.")
            if not self._last_device_info.get("connected"):
                self._open_requested.emit()
        else:
            self.status_msg.setText("RTL-SDR disconnected.")
        self._state_requested.emit()

    # ------------------------------------------------------------------
    # Engine control API used by the tabs
    # ------------------------------------------------------------------
    def engine_start(self, cfg: AcqConfig) -> None:
        self._pending_label = cfg.label
        self._running_mode = cfg.mode
        self._running_start_hz = cfg.sweep_start_hz if cfg.mode == "sweep" else 0.0
        self._running_stop_hz = cfg.sweep_stop_hz if cfg.mode == "sweep" else 0.0
        self._last_error_text = ""
        self._start_requested.emit(cfg)

    def is_running_here(self, label: str) -> bool:
        """True when the currently running acquisition belongs to `label`.

        Only one receiver exists, so tabs take turns. A tab's Start button
        starts its own job unless that job is already the one running, in
        which case it stops it.
        """
        return self.engine_running and self.running_label == label

    def engine_stop(self) -> None:
        self._stop_requested.emit()

    def engine_update(self, changes: dict) -> None:
        if changes:
            self._update_requested.emit(dict(changes))

    def rescan_devices(self) -> None:
        self._watch_count = dev.device_count()
        self.status_msg.setText("Rescanned USB: %d device(s)." % self._watch_count)
        self._state_requested.emit()

    def reopen_device(self) -> None:
        self._open_requested.emit()

    def run_hardware_test(self) -> None:
        self.tabs.setCurrentWidget(self.dashboard_tab)
        self.dashboard_tab.show_test_progress("Starting hardware test...")
        self._hw_test_requested.emit()

    def run_antenna_test(self) -> None:
        self.tabs.setCurrentWidget(self.dashboard_tab)
        self.dashboard_tab.show_test_progress("Starting antenna check...")
        self._ant_test_requested.emit()

    @Slot(bool)
    def set_simulation(self, enabled: bool) -> None:
        self.act_sim.setChecked(bool(enabled))
        self._sim_requested.emit(bool(enabled))

    @Slot(bool)
    def set_sim_antenna(self, connected: bool) -> None:
        self._sim_ant_requested.emit(bool(connected))

    def tune_to(self, freq_hz: float) -> None:
        """Point the Spectrum and Meter tabs at a frequency."""
        mhz = freq_hz / 1e6
        self.spectrum_tab.set_center_mhz(mhz)
        self.meter_tab.set_center_mhz(mhz)
        self.tabs.setCurrentWidget(self.spectrum_tab)
        self.engine_start(self.spectrum_tab.config())
        self.status_msg.setText("Tuned to %.4f MHz." % mhz)

    def current_center_hz(self) -> float | None:
        """Centre frequency of the most recent frame, if any."""
        return self._last_center_hz

    def current_peak_dbfs(self) -> float | None:
        """Peak level in the most recent frame, for bearing/rose recording."""
        return self._last_peak_dbfs

    def reset_audio(self) -> None:
        """Drop demodulator state, so a mode or rate change starts clean."""
        self._reset_audio.emit()

    def current_gain(self):
        """Gain in force right now, stored alongside every measurement.

        Levels taken at different gains are not comparable, so the value has to
        travel with the measurement rather than be assumed.
        """
        return self._last_device_info.get("gain_db", "auto")

    def current_noise_dbfs(self) -> float | None:
        """Noise floor of the most recent frame."""
        return self._last_noise_dbfs

    def _refresh_acquisition_status(self) -> None:
        """Recompute the shared status and push it to every listener."""
        info = self._last_device_info
        connected = bool(info.get("connected"))
        simulated = bool(info.get("simulated"))
        cfg_mode = self._running_mode
        state = state_for(connected, self.engine_running, cfg_mode,
                          self._last_error_text)
        st = self.acq_status
        st.state = state
        st.owner = self.running_label if self.engine_running else ""
        st.simulated = simulated
        st.detail = self._last_error_text
        st.center_hz = float(info.get("center_freq_hz") or 0.0)
        st.sample_rate = float(info.get("sample_rate_hz") or 0.0)
        st.gain_db = info.get("gain_db", "auto")
        try:
            st.ppm = int(info.get("ppm") or 0)
        except (TypeError, ValueError):
            st.ppm = 0
        st.start_hz = self._running_start_hz
        st.stop_hz = self._running_stop_hz
        for widget in self._status_listeners:
            try:
                widget(st)
            except Exception:
                pass

    def register_status_listener(self, callback) -> None:
        """Pages call this to be told whenever the acquisition state changes."""
        if callback not in self._status_listeners:
            self._status_listeners.append(callback)
            callback(self.acq_status)

    def acquisition_status(self) -> AcquisitionStatus:
        return self.acq_status

    def start_default_scan(self) -> None:
        """Used by pages that need live data but do not own acquisition."""
        self.tabs.setCurrentWidget(self.scanner_tab)
        if not self.engine_running:
            self.scanner_tab._toggle()

    def library_status_text(self) -> str:
        return dev.library_status()

    def report_detections(self, detections, source: str) -> None:
        """A page has refreshed the shared store; fan the result out."""
        self.dashboard_tab.update_detections(detections)
        self.signal_store.notify()

    # -- CSV logging ---------------------------------------------------------
    def start_logging(self, source: str = "", ask: bool = False) -> None:
        default = str(Path.cwd() / self.logger.default_filename())
        if ask:
            path, _ = QFileDialog.getSaveFileName(
                self, "Log detections to CSV", default, "CSV files (*.csv)")
            if not path:
                return
        else:
            path = default
        try:
            p = self.logger.start(path)
        except OSError as exc:
            QMessageBox.warning(self, "Logging failed", str(exc))
            return
        self.status_log.setText("Logging: %s" % p.name)
        self.status_msg.setText("Logging detections to %s" % p)
        self._sync_logging_buttons(True)

    def stop_logging(self) -> None:
        if not self.logger.is_logging:
            return
        path = self.logger.path
        rows = self.logger.rows_written
        self.logger.stop()
        self.status_log.setText("Logging: off")
        self.status_msg.setText("Logging stopped - %d rows written to %s" % (rows, path))
        self._sync_logging_buttons(False)

    def _sync_logging_buttons(self, active: bool) -> None:
        for tab in (self.tetra_tab, self.scanner_tab, self.meter_tab):
            tab.set_logging(active)

    # ------------------------------------------------------------------
    # Engine callbacks
    # ------------------------------------------------------------------
    def _on_frame(self, frame) -> None:
        try:
            self._draw_frame(frame)
        finally:
            # Tell the engine we are ready for the next one (back-pressure).
            self._ack_frame.emit()

    def _draw_frame(self, frame) -> None:
        import numpy as np
        self._last_center_hz = float(frame.center_hz)
        self._last_peak_dbfs = (float(np.max(frame.power_db))
                                if frame.power_db.size else None)
        self._last_noise_dbfs = float(frame.noise_floor_db)
        self.dashboard_tab.update_frame(frame)
        current = self.tabs.currentWidget()
        if current is self.spectrum_tab:
            self.spectrum_tab.update_frame(frame)
        elif current is self.waterfall_tab:
            self.waterfall_tab.update_frame(frame)
        elif current is self.meter_tab:
            self.meter_tab.update_frame(frame)
        elif current is self.tetra_tab:
            self.tetra_tab.update_frame(frame)
        elif current is self.scanner_tab:
            self.scanner_tab.update_frame(frame)
        elif current is self.radar_tab:
            self.radar_tab.update_frame(frame)
        else:
            # Keep the waterfall filling in even when it is not visible.
            self.waterfall_tab.update_frame(frame)
        if current is not self.radar_tab:
            # The radar correlates sites against whatever is tuned, so it needs
            # frames even while another tab is on screen.
            self.radar_tab.update_frame(frame)
        self.status_mode.setText("%s  %.4f MHz  %.3f MS/s"
                                 % (frame.label or frame.mode,
                                    frame.center_hz / 1e6, frame.sample_rate / 1e6))

    def _on_sweep(self, result) -> None:
        self.tetra_tab.update_sweep(result)
        self.scanner_tab.update_sweep(result)

    def _on_peaks(self, peaks, label: str) -> None:
        if label in ("TETRA RF Check", "Scanner"):
            self.tetra_tab.update_peaks(peaks, label)
            self.scanner_tab.update_peaks(peaks, label)
            return
        # Spectrum / dashboard style detections
        self.spectrum_tab.update_peaks(peaks)
        logging_on = self.logger.is_logging
        gain = self._last_device_info.get("gain_db", "auto")
        for p in peaks:
            self.signal_store.add_or_update_detection(
                p.freq_hz, p.level_dbfs, p.noise_dbfs, p.snr_db, p.bandwidth_hz,
                source=label or "Spectrum", gain_db=gain)
            if logging_on:
                self.logger.log(p.freq_hz, p.level_dbfs, p.noise_dbfs, p.snr_db,
                                p.bandwidth_hz, label or "Spectrum")
        self.signal_store.clear_stale_detections(300.0)
        confirmed = self.signal_store.confirmed_items(2)
        self.dashboard_tab.update_detections(confirmed)
        self.signal_store.notify()

    def _on_device_state(self, info: dict) -> None:
        self._last_device_info = dict(info)
        if info.get("connected"):
            self._last_error_text = ""
        self.dashboard_tab.update_device_state(info)
        self.diagnostics_tab.update_device_state(info)
        if info.get("simulated"):
            text = "Device: SIMULATED"
            color = theme.WARN
        elif info.get("connected"):
            text = "Device: connected"
            color = theme.GOOD
        elif info.get("usb_device_count"):
            text = "Device: found, not open"
            color = theme.WARN
        else:
            text = "Device: not found"
            color = theme.BAD
        self.status_device.setText(text)
        self.status_device.setStyleSheet("padding: 0 12px; color: %s; font-weight: 600;"
                                         % color)
        self._refresh_acquisition_status()

    def _on_status(self, message: str) -> None:
        self.status_msg.setText(message)

    def _on_error(self, message: str) -> None:
        self._last_error_text = message.splitlines()[0][:70]
        self._refresh_acquisition_status()
        self.status_msg.setText(message.splitlines()[0])
        first = message.splitlines()[0].lower()
        if "zadig" in message.lower() or "could not open" in first:
            if not self._driver_help_shown:
                self._driver_help_shown = True
                QMessageBox.warning(self, "RTL-SDR error", message)
        else:
            QMessageBox.warning(self, "RTL-SDR error", message)

    def _on_running(self, running: bool) -> None:
        self.engine_running = bool(running)
        self.running_label = self._pending_label if running else ""
        if not running:
            self._running_mode = ""
        for tab in (self.dashboard_tab, self.spectrum_tab, self.waterfall_tab,
                    self.tetra_tab, self.meter_tab, self.scanner_tab,
                    self.radar_tab, self.listen_tab, self.smart_tab):
            tab.set_running(bool(running) and self.running_label == tab.tab_label)
        if not running:
            self.status_mode.setText("Idle")
        self._refresh_acquisition_status()

    def _on_test_finished(self, report: dict) -> None:
        self.dashboard_tab.show_test_report(report)
        ok = report.get("ok")
        if report.get("kind") == "antenna":
            self.status_msg.setText("Antenna check: %s" % report.get("verdict", "?"))
        else:
            self.status_msg.setText("Hardware test %s" % ("passed" if ok else "failed"))
            if not ok:
                self.tabs.setCurrentWidget(self.dashboard_tab)

    def _about(self) -> None:
        QMessageBox.about(
            self, "About",
            "<b>%s</b><br>version %s<br><br>"
            "Receiver diagnostics, spectrum monitoring and analog radio listening "
            "for the RTL-SDR Blog V4.<br><br>"
            "Spectrum and TETRA views report RF energy only. The Listen tab can "
            "demodulate analog WFM, NFM and AM; it does not decode DAB+ or digital "
            "voice, decrypt protected traffic, or identify networks or users."
            % (APP_NAME, __version__))

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        self.watchdog.stop()
        try:
            self.listen_tab.player.stop()
        except Exception:
            pass
        try:
            self.logger.stop()
        except Exception:
            pass
        self._stop_requested.emit()
        # Close the device on the worker thread and wait for it to finish, so the
        # USB handle is released before the thread goes away.
        try:
            self._close_requested.emit()
        except Exception:
            pass
        self.thread.quit()
        if not self.thread.wait(3000):
            self.thread.terminate()
            self.thread.wait(1000)
        super().closeEvent(event)
