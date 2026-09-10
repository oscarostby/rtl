"""Everything the detector needs that is not a widget.

Owns the one RTL-SDR, the worker thread, the shared signal store and the band
rotation. The window talks to this; nothing else opens the device.
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal

from ..core.acquisition import AcquisitionState, AcquisitionStatus, state_for
from ..core.csvlog import CsvLogger
from ..core.detector import (MODE_SPECS, MODES, TETRA_MOBILE,
                             BandRotation)
from ..core.duplex import watch_window
from ..core.observations import ObservationLog
from ..core import provisioning
from ..core.signal_store import SignalStore
from ..sdr import demod
from ..sdr import device as dev
from ..sdr.engine import MODE_SPECTRUM, MODE_SWEEP, AcqConfig, SdrEngine
from .audio import AudioPlayer

BEEP_TONES = {"Medium": 660.0, "Strong": 880.0, "Very Strong": 1180.0}

# Tune this far off the wanted signal and shift it back in software, so the
# receiver's own DC spike does not sit in the middle of the channel.
LISTEN_OFFSET_HZ = 250e3


class AppCore(QObject):
    """Device manager, detection bus and band scheduler."""

    _start_requested = Signal(object)
    _stop_requested = Signal()
    _update_requested = Signal(dict)
    _open_requested = Signal()
    _sim_requested = Signal(bool)
    _state_requested = Signal()
    _close_requested = Signal()

    log_message = Signal(str)
    uplink_event = Signal(object)        # LiveSignalDetection, mobile band
    listening_changed = Signal(str)      # band key, or "" when it stops
    zadig_status = Signal(str, str)      # "busy" | "ready" | "failed", detail
    listen_level = Signal(float)         # dBFS in the parked channel

    def __init__(self, simulate: bool = False, parent=None):
        super().__init__(parent)
        self.signal_store = SignalStore()
        # Everything the receiver has seen this session, kept as a survey.
        self.observations = ObservationLog()
        self.acq_status = AcquisitionStatus()
        self.detector_states: dict = {}
        self.rotation = BandRotation(MODES)
        self.priority_band = MODES[0]

        self._status_listeners: list = []
        self._device_info: dict = {}
        self._error_text = ""
        self._running = False
        self._running_mode = ""
        self._band = MODES[0]
        self._simulate = simulate

        self.audio = AudioPlayer()
        self._audio_ready = False
        self.listening_band = ""
        self.listen_freq_hz = 0.0
        self._shut_down = False
        self._zadig_busy = False
        # None means "let each band use the gain that suits it"; a number is
        # the user overriding that for every band from the settings panel.
        self.gain_override = None
        # Sit on the uplink channels paired with the masts being received,
        # instead of sweeping the band and watching each channel a third of
        # the time. Set from the settings panel.
        self.pair_watch = True
        self.pair_window = None          # (start_hz, stop_hz, channels)

        # Uplink is bursty and short-lived, so it is logged the moment it is
        # seen. Nothing but time, frequency and level is recorded - no content
        # and nothing that identifies a transmitter.
        self.uplink_log = CsvLogger()
        self.log_uplink = True
        self._uplink_last: dict = {}
        self._uplink_gap_s = 30.0

        self.thread = QThread(self)
        self.thread.setObjectName("sdr-acquisition")
        self.engine = SdrEngine()
        self.engine.moveToThread(self.thread)
        self.thread.start()
        self._connect()

        # Watch for the dongle appearing or disappearing while running.
        self._watch = QTimer(self)
        self._watch.setInterval(2000)
        self._watch.timeout.connect(self._poll_device)
        self._watch.start()
        self._device_count = -1

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        e = self.engine
        self._start_requested.connect(e.start, Qt.QueuedConnection)
        self._stop_requested.connect(e.stop, Qt.QueuedConnection)
        self._update_requested.connect(e.update_settings, Qt.QueuedConnection)
        self._open_requested.connect(e.open_device, Qt.QueuedConnection)
        self._sim_requested.connect(e.set_simulation, Qt.QueuedConnection)
        self._state_requested.connect(e.emit_device_state, Qt.QueuedConnection)
        self._close_requested.connect(e.close_device, Qt.BlockingQueuedConnection)

        e.peaks_ready.connect(self._on_peaks, Qt.QueuedConnection)
        e.sweep_ready.connect(self._on_sweep_done, Qt.QueuedConnection)
        e.device_state.connect(self._on_device_state, Qt.QueuedConnection)
        e.running_changed.connect(self._on_running, Qt.QueuedConnection)
        e.error_occurred.connect(self._on_error, Qt.QueuedConnection)
        e.status_changed.connect(self.log_message.emit, Qt.QueuedConnection)
        e.audio_ready.connect(self._on_audio, Qt.QueuedConnection)
        e.audio_level.connect(self._on_audio_level, Qt.QueuedConnection)

    # ------------------------------------------------------------------
    # Start-up
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Open the device (or the simulator) and begin scanning."""
        # The dongle will not open at all until Windows binds it to WinUSB, so
        # have the tool for that ready before anyone needs to ask for it.
        QTimer.singleShot(1200, self.ensure_zadig)
        if self._simulate:
            self._sim_requested.emit(True)
        elif dev.library_available() and dev.device_count() > 0:
            self._open_requested.emit()
        else:
            self._state_requested.emit()
        QTimer.singleShot(600, self.start_scanning)

    def start_scanning(self) -> None:
        if self._running:
            return
        self._start_requested.emit(self._config_for(self._band))

    def stop_scanning(self) -> None:
        self._stop_requested.emit()

    def gain_for(self, band: str):
        """The gain to sweep this band with, snapped to what the tuner has."""
        if self.gain_override is not None:
            return self.gain_override
        want = MODE_SPECS[band].gain if band in MODE_SPECS else "auto"
        gains = [g for g in (self._device_info.get("gains_db") or [])
                 if isinstance(g, (int, float))]
        if want == "max":
            return max(gains) if gains else "auto"
        if isinstance(want, (int, float)):
            if not gains:
                return float(want)
            return min(gains, key=lambda g: abs(g - float(want)))
        return "auto"

    def apply_gain(self) -> None:
        self.engine_update({"gain": self.gain_for(self._band)})

    def sweep_range(self, band: str):
        """Where to point the receiver for this band.

        For the mobile band that is normally one window over the channels
        paired with the local masts, which are the only uplink channels that
        can carry traffic here - see core/duplex.py.
        """
        spec = MODE_SPECS[band]
        if band != TETRA_MOBILE or not self.pair_watch:
            self.pair_window = None if band == TETRA_MOBILE else self.pair_window
            return spec.start_hz, spec.stop_hz
        usable = spec.sample_rate * 0.75
        placed = watch_window(self.signal_store.items(), usable)
        self.pair_window = placed
        if placed is None:
            return spec.start_hz, spec.stop_hz      # nothing heard yet: sweep
        return placed[0], placed[1]

    def _config_for(self, band: str) -> AcqConfig:
        spec = MODE_SPECS[band]
        start, stop = self.sweep_range(band)
        return AcqConfig(
            mode=MODE_SWEEP,
            center_hz=(start + stop) / 2.0,
            sample_rate=spec.sample_rate,
            gain=self.gain_for(band),
            sweep_start_hz=start,
            sweep_stop_hz=stop,
            snr_threshold_db=spec.snr_threshold_db,
            min_bandwidth_hz=spec.min_bandwidth_hz,
            smoothing_hz=spec.smoothing_hz,
            gap_hz=spec.gap_hz,
            dwell_s=spec.dwell_s,
            psd_combine=spec.psd_combine,
            detect=True,
            label="Detector",
        )

    # ------------------------------------------------------------------
    # Band rotation
    # ------------------------------------------------------------------
    def set_priority_band(self, band: str) -> None:
        """Called when the visible page changes. Never restarts the device."""
        if band in MODES:
            self.priority_band = band

    def _on_sweep_done(self, result) -> None:
        """One band finished: retune the running sweep to the next one."""
        # A sweep already in flight when listening started still reports back.
        # Acting on it would retune the receiver off the channel being played -
        # and worse, change its sample rate underneath the demodulator.
        if not self._running or self.listening_band:
            return
        nxt = self.rotation.next_band(self.priority_band)
        if nxt == self._band:
            return
        self._band = nxt
        spec = MODE_SPECS[nxt]
        # A live settings change - the device stays open and the thread keeps
        # running; only the sweep range and detector parameters move.
        start, stop = self.sweep_range(nxt)
        self._update_requested.emit({
            "sweep_start_hz": start,
            "sweep_stop_hz": stop,
            "sample_rate": spec.sample_rate,
            "snr_threshold_db": spec.snr_threshold_db,
            "min_bandwidth_hz": spec.min_bandwidth_hz,
            "smoothing_hz": spec.smoothing_hz,
            "gap_hz": spec.gap_hz,
            "dwell_s": spec.dwell_s,
            "psd_combine": spec.psd_combine,
            "gain": self.gain_for(nxt),
        })
        self._refresh_status()

    # ------------------------------------------------------------------
    # Detections
    # ------------------------------------------------------------------
    def _on_peaks(self, peaks, label: str) -> None:
        gain = self.current_gain()
        self.signal_store.begin_pass()
        fresh = []
        for p in peaks:
            det, _ = self.signal_store.add_or_update_detection(
                p.freq_hz, p.level_dbfs, p.noise_dbfs, p.snr_db, p.bandwidth_hz,
                source="Detector", gain_db=gain)
            fresh.append(det)
        self.observations.record(fresh)
        self._check_uplink(fresh, gain)
        self.signal_store.clear_stale_detections(120.0)
        self.signal_store.notify()

    def _check_uplink(self, detections, gain) -> None:
        """Announce and record a burst in the mobile uplink sub-band.

        Bursts are grouped: the same channel keying up repeatedly inside half a
        minute is one event, so a single conversation does not fill the log.
        """
        spec = MODE_SPECS[TETRA_MOBILE]
        now = time.time()
        for det in detections:
            if not spec.accepts(det):
                continue
            key = round(det.freq_hz / 12_500.0)
            previous = self._uplink_last.get(key, 0.0)
            self._uplink_last[key] = now
            if now - previous < self._uplink_gap_s:
                continue                      # same burst, already announced
            self.uplink_event.emit(det)
            self.log_message.emit(
                "TETRA mobile activity %.4f MHz  SNR %.1f dB"
                % (det.frequency_mhz, det.snr_db))
            if self.log_uplink:
                if not self.uplink_log.is_logging:
                    try:
                        self.uplink_log.start("uplink_events.csv")
                    except OSError:
                        self.log_uplink = False
                        continue
                self.uplink_log.log(det.freq_hz, det.level_dbfs, det.noise_dbfs,
                                    det.snr_db, det.bandwidth_hz,
                                    "TETRA mobile gain=%s" % gain)
                # Written straight through: these are rare and the whole point
                # of the log is that it survives an abrupt shutdown.
                self.uplink_log.flush()

    # ------------------------------------------------------------------
    # Driver setup
    # ------------------------------------------------------------------
    def ensure_zadig(self, force: bool = False) -> None:
        """Fetch the driver tool in the background. Never installs anything."""
        if self._zadig_busy:
            return
        already = provisioning.existing_zadig()
        if already is not None and not force:
            self.zadig_status.emit("ready", str(already))
            return
        self._zadig_busy = True
        threading.Thread(target=self._zadig_worker, args=(force,),
                         daemon=True, name="zadig-download").start()

    def _zadig_worker(self, force: bool) -> None:
        try:
            path = provisioning.ensure_zadig(
                lambda message: self.zadig_status.emit("busy", message), force)
            self.zadig_status.emit("ready", str(path))
        except provisioning.ProvisionError as exc:
            self.zadig_status.emit("failed", str(exc))
        except Exception as exc:                      # never kill the thread
            self.zadig_status.emit("failed", "%s: %s" % (type(exc).__name__, exc))
        finally:
            self._zadig_busy = False

    def launch_zadig(self) -> str:
        return provisioning.launch_zadig()

    # ------------------------------------------------------------------
    # Listening
    # ------------------------------------------------------------------
    def is_listening(self, band: str = "") -> bool:
        return bool(self.listening_band) and (not band
                                              or self.listening_band == band)

    def start_listening(self, band: str, freq_hz: float) -> str:
        """Park on one frequency and play it. Returns "" or a reason it cannot.

        Listening stops the sweep: there is one receiver, and it cannot be
        both stepping across a band and sitting on a channel.
        """
        spec = MODE_SPECS.get(band)
        if spec is None or not spec.audio_mode:
            return spec.audio_note if spec else "Unknown band."
        if not self.audio.is_available:
            return "No audio output is available: %s" % self.audio.last_error
        if not self._audio_ready:
            self._audio_ready = self.audio.start(48_000)
            if not self._audio_ready:
                return "Could not open the audio output."

        mode = spec.audio_mode
        self.listening_band = band
        self.listen_freq_hz = float(freq_hz)
        self._stop_requested.emit()
        self._start_requested.emit(AcqConfig(
            mode=MODE_SPECTRUM,
            center_hz=float(freq_hz) - LISTEN_OFFSET_HZ,
            sample_rate=demod.MODE_SAMPLE_RATE[mode],
            gain=self.gain_for(band), fft_size=4096, averages=1, detect=False,
            audio=True, audio_mode=mode, audio_offset_hz=LISTEN_OFFSET_HZ,
            squelch_db=-200.0, volume=1.0, label="Listen"))
        # Worth saying plainly: there is one receiver, so while it sits on this
        # channel nothing is watching the rest of the band - no detections, and
        # no uplink alerts.
        self.log_message.emit(
            "Listening on %.4f MHz (%s) - scanning and uplink alerts are "
            "paused until you stop." % (freq_hz / 1e6, mode))
        self.listening_changed.emit(band)
        return ""

    def stop_listening(self) -> None:
        if not self.listening_band:
            return
        self.listening_band = ""
        self._stop_requested.emit()
        self.listening_changed.emit("")
        # Let the engine settle out of the audio stream before the sweep
        # reclaims the receiver.
        QTimer.singleShot(300, self.start_scanning)

    def _on_audio(self, pcm: bytes, rate: int) -> None:
        if self.listening_band and pcm:
            self.audio.write(pcm)

    def _on_audio_level(self, level_db: float, squelched: bool) -> None:
        if self.listening_band:
            self.listen_level.emit(float(level_db))

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def register_status_listener(self, callback) -> None:
        if callback not in self._status_listeners:
            self._status_listeners.append(callback)
            callback(self.acq_status)

    def acquisition_status(self) -> AcquisitionStatus:
        return self.acq_status

    def _refresh_status(self) -> None:
        info = self._device_info
        st = self.acq_status
        st.state = state_for(bool(info.get("connected")), self._running,
                             self._running_mode, self._error_text)
        st.owner = MODE_SPECS[self._band].title if self._running else ""
        st.simulated = bool(info.get("simulated"))
        st.detail = self._error_text
        st.center_hz = float(info.get("center_freq_hz") or 0.0)
        st.sample_rate = float(info.get("sample_rate_hz") or 0.0)
        st.gain_db = info.get("gain_db", "auto")
        try:
            st.ppm = int(info.get("ppm") or 0)
        except (TypeError, ValueError):
            st.ppm = 0
        spec = MODE_SPECS[self._band]
        st.start_hz, st.stop_hz = spec.start_hz, spec.stop_hz
        for cb in self._status_listeners:
            try:
                cb(st)
            except Exception:
                pass

    def _on_device_state(self, info: dict) -> None:
        self._device_info = dict(info)
        if info.get("connected"):
            self._error_text = ""
        self._refresh_status()

    def _on_running(self, running: bool) -> None:
        self._running = bool(running)
        self._running_mode = "sweep" if running else ""
        self._refresh_status()

    def _on_error(self, message: str) -> None:
        self._error_text = message.splitlines()[0][:70]
        self._refresh_status()
        self.log_message.emit(message.splitlines()[0])

    def _poll_device(self) -> None:
        if self._device_info.get("simulated"):
            return
        try:
            count = dev.device_count()
        except Exception:
            count = 0
        if count == self._device_count:
            return
        previous, self._device_count = self._device_count, count
        if previous < 0:
            return
        if count > previous and not self._device_info.get("connected"):
            self._open_requested.emit()
            QTimer.singleShot(800, self.start_scanning)
        self._state_requested.emit()

    # ------------------------------------------------------------------
    # Helpers used by the window
    # ------------------------------------------------------------------
    def engine_update(self, changes: dict) -> None:
        if changes:
            self._update_requested.emit(dict(changes))

    def current_gain(self):
        return self._device_info.get("gain_db", "auto")

    def log(self, message: str) -> None:
        self.log_message.emit(message)

    def beep(self, strength: str) -> None:
        """Short tone whose pitch rises with strength."""
        if not self.audio.is_available:
            return
        if not self._audio_ready:
            self._audio_ready = self.audio.start(48_000)
            if not self._audio_ready:
                return
        freq = BEEP_TONES.get(strength, 660.0)
        n = int(48_000 * 0.06)
        t = np.arange(n) / 48_000.0
        envelope = np.minimum(1.0, np.minimum(t * 200.0, (t[-1] - t) * 200.0))
        tone = 0.25 * envelope * np.sin(2.0 * math.pi * freq * t)
        self.audio.write((np.clip(tone, -1.0, 1.0) * 32767.0
                          ).astype("<i2").tobytes())

    def shutdown(self) -> None:
        # Idempotent on purpose. The window shuts the core down from its close
        # event, and anything else that quits the program is entitled to call
        # this too; a second pass used to deadlock on the blocking close below
        # and leave the process alive still holding the receiver.
        if self._shut_down:
            return
        self._shut_down = True
        self._watch.stop()
        self.listening_band = ""
        try:
            self.uplink_log.stop()
        except Exception:
            pass
        try:
            self.audio.stop()
        except Exception:
            pass
        self._stop_requested.emit()
        # A blocking call reaches the worker through its event loop, so it can
        # only be made while that loop is still running.
        if self.thread.isRunning():
            try:
                self._close_requested.emit()
            except Exception:
                pass
        self.thread.quit()
        if not self.thread.wait(3000):
            self.thread.terminate()
            self.thread.wait(1000)
