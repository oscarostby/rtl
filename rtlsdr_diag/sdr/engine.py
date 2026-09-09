"""Acquisition engine: owns the SDR device and runs on a dedicated QThread.

The GUI never touches the device directly. It sends requests through queued
signals and receives frames back the same way, so the UI thread never blocks on
USB traffic.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..config import (DEFAULT_AVERAGES, DEFAULT_CENTER_FREQ, DEFAULT_FFT_SIZE,
                      DEFAULT_GAIN, DEFAULT_MIN_BW_HZ, DEFAULT_PPM,
                      DEFAULT_SAMPLE_RATE, DEFAULT_SNR_THRESHOLD_DB)
from . import demod, dsp
from .device import RtlSdrSource, SdrError, SdrSource, device_count, library_available
from .simulator import SimulatedSource

MODE_SPECTRUM = "spectrum"
MODE_SWEEP = "sweep"

USABLE_FRACTION = 0.75      # portion of each sweep step we keep (avoids edges)

# How much audio to demodulate per read. Long enough to be efficient,
# short enough that tuning changes are heard almost at once.
AUDIO_BLOCK_SECONDS = 0.05

# Minimum wall-clock time between emitted frames. Keeps the GUI event loop
# breathing when acquisition is much faster than rendering.
FRAME_INTERVAL = 1.0 / 25.0
SWEEP_FRAME_INTERVAL = 1.0 / 50.0

# How many emitted frames may be waiting in the GUI's event queue before
# the engine stops emitting. Without this back-pressure a fast source
# (high sample rate, or the simulator) queues frames faster than the GUI
# can draw them and the interface stops responding.
MAX_PENDING_FRAMES = 2


@dataclass
class AcqConfig:
    mode: str = MODE_SPECTRUM
    center_hz: float = DEFAULT_CENTER_FREQ
    sample_rate: float = DEFAULT_SAMPLE_RATE
    gain: object = DEFAULT_GAIN          # "auto" or float dB
    ppm: int = DEFAULT_PPM
    fft_size: int = DEFAULT_FFT_SIZE
    averages: int = DEFAULT_AVERAGES
    sweep_start_hz: float = 380.0e6
    sweep_stop_hz: float = 400.0e6
    snr_threshold_db: float = DEFAULT_SNR_THRESHOLD_DB
    min_bandwidth_hz: float = DEFAULT_MIN_BW_HZ
    smoothing_hz: float = 2000.0
    gap_hz: float = 3000.0
    detect: bool = False
    label: str = ""
    # How long to sit on each sweep step. 0 means "as long as fft_size times
    # averages happens to take". A bursty band needs this set long enough to
    # contain a whole frame of whatever is transmitting, or a step can land in
    # the silence between two bursts and see nothing at all.
    dwell_s: float = 0.0
    psd_combine: str = "mean"
    # Audio demodulation
    audio: bool = False
    audio_mode: str = demod.MODE_WFM
    audio_offset_hz: float = 0.0
    squelch_db: float = -200.0
    volume: float = 1.0
    # 0..1, how strong the app is showing this signal to be. Drives the speed
    # and pitch of the envelope sonification.
    tone_strength: float = 0.0


@dataclass
class SpectrumFrame:
    freqs: np.ndarray
    power_db: np.ndarray
    center_hz: float
    sample_rate: float
    noise_floor_db: float
    timestamp: float
    samples: int
    mode: str
    label: str = ""
    iq_stats: dict = field(default_factory=dict)


@dataclass
class SweepResult:
    freqs: np.ndarray
    power_db: np.ndarray
    noise_floor_db: float
    peaks: list
    start_hz: float
    stop_hz: float
    duration_s: float
    label: str = ""


class SdrEngine(QObject):
    """Lives on a worker thread. All public slots are queued-connection safe."""

    frame_ready = Signal(object)         # SpectrumFrame
    sweep_ready = Signal(object)         # SweepResult
    peaks_ready = Signal(object, str)    # list[dsp.RawPeak], label
    status_changed = Signal(str)
    error_occurred = Signal(str)
    device_state = Signal(dict)          # describe() + {'connected': bool}
    stats_updated = Signal(dict)
    test_progress = Signal(str)
    test_finished = Signal(dict)
    running_changed = Signal(bool)
    audio_ready = Signal(bytes, int)     # PCM16 mono, sample rate
    audio_level = Signal(float, bool)    # dBFS in the channel, squelched?

    def __init__(self) -> None:
        super().__init__()
        self._source: SdrSource | None = None
        self._cfg = AcqConfig()
        self._running = False
        self._busy_test = False
        self._simulate = False
        self._sim_antenna = True
        self._device_index = 0

        # sweep state
        self._sweep_centers: list[float] = []
        self._sweep_idx = 0
        self._sweep_freqs: list[np.ndarray] = []
        self._sweep_powers: list[np.ndarray] = []
        self._sweep_t0 = 0.0

        # statistics
        self._frames = 0
        self._total_samples = 0
        self._dropped_estimate = 0
        self._last_read_end = 0.0
        self._fps_t0 = time.time()
        self._fps_frames = 0
        self._fps = 0.0
        self._skipped_frames = 0
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._demod: demod.Demodulator | None = None
        self._step_pending = False
        self.last_error = ""

    # ------------------------------------------------------------------
    # Device lifecycle
    # ------------------------------------------------------------------
    def _make_source(self) -> SdrSource:
        if self._simulate:
            src = SimulatedSource(antenna_connected=self._sim_antenna)
            return src
        return RtlSdrSource(device_index=self._device_index)

    @Slot(bool)
    def set_simulation(self, enabled: bool) -> None:
        if enabled == self._simulate:
            return
        was_running = self._running
        self.stop()
        self._close_source()
        self._simulate = bool(enabled)
        self.status_changed.emit(
            "Simulation mode ON - no real RF is being received."
            if enabled else "Simulation mode OFF."
        )
        if enabled:
            # Open straight away so the shared state reads CONNECTED - IDLE
            # rather than DISCONNECTED; otherwise every page correctly refuses
            # to start acquisition on a device that looks absent.
            self.open_device()
        self.emit_device_state()
        if was_running:
            self.start(self._cfg)

    @Slot(bool)
    def set_sim_antenna(self, connected: bool) -> None:
        self._sim_antenna = bool(connected)
        if isinstance(self._source, SimulatedSource):
            self._source.antenna_connected = self._sim_antenna

    @Slot(int)
    def set_device_index(self, index: int) -> None:
        self._device_index = int(index)

    @Slot()
    def open_device(self) -> bool:
        if self._source is not None and self._source.is_open:
            return True
        try:
            self._source = self._make_source()
            self._source.open()
            self._apply_all_settings()
            self.status_changed.emit("Device opened.")
            self.emit_device_state()
            return True
        except SdrError as exc:
            self.last_error = str(exc)
            self._source = None
            self.error_occurred.emit(str(exc))
            self.emit_device_state()
            return False
        except Exception as exc:  # pragma: no cover - unexpected driver faults
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self._source = None
            self.error_occurred.emit(self.last_error)
            self.emit_device_state()
            return False

    def _close_source(self) -> None:
        if self._source is not None:
            try:
                self._source.close()
            except Exception:
                pass
        self._source = None

    @Slot()
    def close_device(self) -> None:
        self.stop()
        self._close_source()
        self.emit_device_state()

    @Slot()
    def emit_device_state(self) -> None:
        info: dict = {}
        if self._source is not None:
            try:
                info = dict(self._source.describe())
            except Exception:
                info = {}
        info["simulated"] = self._simulate
        info["connected"] = bool(self._source is not None and self._source.is_open)
        if not self._simulate:
            info["usb_device_count"] = device_count()
            info["library_ok"] = library_available()
        else:
            info["usb_device_count"] = 1
            info["library_ok"] = True
        info.setdefault("last_error", self.last_error)
        info["running"] = self._running
        self.device_state.emit(info)

    def _apply_all_settings(self) -> None:
        s = self._source
        if s is None:
            return
        s.set_sample_rate(self._cfg.sample_rate)
        s.set_ppm(self._cfg.ppm)
        s.set_gain(self._cfg.gain)
        s.set_center_freq(self._cfg.center_hz)

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------
    @Slot(object)
    def start(self, cfg: AcqConfig) -> None:
        if self._busy_test:
            self.error_occurred.emit("A hardware test is running; try again in a moment.")
            return
        self._cfg = replace(cfg)
        # A start request is a new stream (possibly at another frequency or
        # sample rate), so discriminator/filter history from the old stream is
        # no longer valid.
        self._demod = None
        if self._source is None or not self._source.is_open:
            if not self.open_device():
                return
        try:
            self._apply_all_settings()
        except SdrError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._frames = 0
        self._total_samples = 0
        self._dropped_estimate = 0
        self._fps_t0 = time.time()
        self._fps_frames = 0
        self._last_read_end = 0.0
        self._skipped_frames = 0
        with self._pending_lock:
            self._pending = 0
        if self._cfg.mode == MODE_SWEEP:
            self._prepare_sweep()
        self._running = True
        self.running_changed.emit(True)
        self.status_changed.emit("Acquisition started: %s" % (self._cfg.label or self._cfg.mode))
        self.emit_device_state()
        self._schedule_step(0)

    @Slot()
    def stop(self) -> None:
        if self._running:
            self._running = False
            self.running_changed.emit(False)
            self.status_changed.emit("Acquisition stopped.")
            self.emit_device_state()

    @Slot(dict)
    def update_settings(self, changes: dict) -> None:
        """Apply live control changes without restarting acquisition."""
        cfg = self._cfg
        for key, value in changes.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        if cfg.audio and any(key in changes for key in (
                "center_hz", "sample_rate", "audio_mode", "audio_offset_hz")):
            # Retuning makes the next IQ sample discontinuous with the previous
            # one.  Do not feed that boundary through the FM discriminator.
            self._demod = None
        s = self._source
        if s is None or not s.is_open:
            return
        try:
            if "sample_rate" in changes:
                s.set_sample_rate(cfg.sample_rate)
            if "ppm" in changes:
                s.set_ppm(cfg.ppm)
            if "gain" in changes:
                s.set_gain(cfg.gain)
            if "center_hz" in changes and cfg.mode == MODE_SPECTRUM:
                s.set_center_freq(cfg.center_hz)
            if cfg.mode == MODE_SWEEP and (
                    "sweep_start_hz" in changes or "sweep_stop_hz" in changes
                    or "sample_rate" in changes):
                self._prepare_sweep()
        except SdrError as exc:
            self.error_occurred.emit(str(exc))

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Acquisition steps
    # ------------------------------------------------------------------
    def _block_size(self) -> int:
        cfg = self._cfg
        if cfg.audio:
            # Audio has to be gapless, so the block is chosen by duration
            # rather than by FFT size: read about 50 ms at a time and let the
            # device's own timing pace the loop.
            n = int(cfg.sample_rate * AUDIO_BLOCK_SECONDS)
            n = max(n, cfg.fft_size)
        elif cfg.dwell_s > 0.0:
            n = int(cfg.sample_rate * cfg.dwell_s)
            n = max(n, cfg.fft_size)
        else:
            n = cfg.fft_size * max(1, cfg.averages)
        n = min(n, 1 << 20)
        return int(((n + 511) // 512) * 512)

    def _read(self, num_samples: int) -> np.ndarray | None:
        s = self._source
        if s is None:
            return None
        t0 = time.time()
        try:
            iq = s.read(num_samples)
        except SdrError as exc:
            self.last_error = str(exc)
            self._running = False
            self.running_changed.emit(False)
            self._close_source()
            self.error_occurred.emit(
                "%s\nAcquisition stopped. Reconnect the device and press Start again."
                % exc)
            self.emit_device_state()
            return None
        except Exception as exc:  # pragma: no cover
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self._running = False
            self.running_changed.emit(False)
            self._close_source()
            self.error_occurred.emit(self.last_error)
            self.emit_device_state()
            return None
        now = time.time()
        # Estimate dropped samples from the gap between consecutive reads.
        if self._last_read_end > 0.0:
            gap = t0 - self._last_read_end
            if gap > 0:
                self._dropped_estimate += int(max(0.0, gap) * self._cfg.sample_rate)
        self._last_read_end = now
        self._total_samples += int(iq.size)
        return iq

    def _emit_frame(self, iq: np.ndarray, center: float, with_stats: bool = False) -> np.ndarray:
        cfg = self._cfg
        # With a timed dwell every segment of the block is wanted, otherwise
        # the extra samples read for it would simply be discarded.
        segments = (max(1, len(iq) // max(1, cfg.fft_size)) if cfg.dwell_s > 0.0
                    else max(1, cfg.averages))
        power = dsp.psd_dbfs(iq, cfg.fft_size, max_segments=segments,
                             combine=cfg.psd_combine)
        freqs = dsp.freq_axis(center, cfg.sample_rate, power.size)
        nf = dsp.noise_floor_db(power)
        with self._pending_lock:
            backlogged = self._pending >= MAX_PENDING_FRAMES
            if not backlogged:
                self._pending += 1
        if backlogged:
            # The GUI has not finished the previous frames yet. Drop this one
            # rather than queueing it - the analysis above still happened, so
            # sweeps and detection are unaffected.
            self._skipped_frames += 1
            return power

        frame = SpectrumFrame(
            freqs=freqs, power_db=power, center_hz=center,
            sample_rate=cfg.sample_rate, noise_floor_db=nf,
            timestamp=time.time(), samples=int(iq.size), mode=cfg.mode,
            label=cfg.label,
            iq_stats=dsp.stream_health(iq) if with_stats else {},
        )
        self.frame_ready.emit(frame)
        self._frames += 1
        self._fps_frames += 1
        dt = time.time() - self._fps_t0
        if dt >= 1.0:
            self._fps = self._fps_frames / dt
            self._fps_t0 = time.time()
            self._fps_frames = 0
            self.stats_updated.emit(self.stats())
        return power

    @Slot()
    def ack_frame(self) -> None:
        """Called by the GUI once it has finished drawing a frame."""
        with self._pending_lock:
            self._pending = max(0, self._pending - 1)

    def _emit_audio(self, iq: np.ndarray) -> None:
        cfg = self._cfg
        if self._demod is None:
            self._demod = demod.Demodulator(cfg.audio_mode, cfg.sample_rate,
                                            cfg.audio_offset_hz)
        else:
            self._demod.configure(cfg.audio_mode, cfg.sample_rate,
                                  cfg.audio_offset_hz)
        self._demod.squelch_db = cfg.squelch_db
        self._demod.strength = float(cfg.tone_strength)
        self._demod.gain = cfg.volume
        try:
            audio = self._demod.process(iq)
        except Exception as exc:      # never let a DSP fault kill acquisition
            self.last_error = "demodulation failed: %s" % exc
            return
        if audio.size:
            self.audio_ready.emit(demod.to_pcm16(audio), self._demod.audio_rate)
        self.audio_level.emit(self._demod.last_level_db,
                              self._demod.is_muted_by_squelch)

    @Slot()
    def reset_audio(self) -> None:
        self._demod = None

    def stats(self) -> dict:
        return {
            "frames": self._frames,
            "frames_skipped": self._skipped_frames,
            "last_sample_time": (
                time.strftime("%H:%M:%S", time.localtime(self._last_read_end))
                if self._last_read_end else "-"),
            "fps": round(self._fps, 1),
            "total_samples": self._total_samples,
            "dropped_estimate": self._dropped_estimate,
            "block_size": self._block_size(),
            "running": self._running,
            "mode": self._cfg.mode,
            "last_error": self.last_error,
        }

    def _prepare_sweep(self) -> None:
        cfg = self._cfg
        start = min(cfg.sweep_start_hz, cfg.sweep_stop_hz)
        stop = max(cfg.sweep_start_hz, cfg.sweep_stop_hz)
        step = cfg.sample_rate * USABLE_FRACTION
        if stop - start <= cfg.sample_rate:
            centers = [(start + stop) / 2.0]
        else:
            n = int(np.ceil((stop - start) / step))
            centers = [start + step * (i + 0.5) for i in range(n)]
        self._sweep_centers = centers
        self._sweep_idx = 0
        self._sweep_freqs = []
        self._sweep_powers = []
        self._sweep_t0 = time.time()

    def _schedule_step(self, delay_ms: int) -> None:
        """Keep exactly one acquisition callback queued at a time."""
        if self._step_pending:
            return
        self._step_pending = True
        QTimer.singleShot(max(0, int(delay_ms)), self._step)

    def _step(self) -> None:
        self._step_pending = False
        if not self._running:
            return
        t0 = time.perf_counter()
        try:
            if self._cfg.mode == MODE_SWEEP:
                self._step_sweep()
            else:
                self._step_spectrum()
        except Exception as exc:  # pragma: no cover - never kill the thread
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self.error_occurred.emit("Acquisition error: %s" % self.last_error)
            self._running = False
            self.running_changed.emit(False)
            return
        if not self._running:
            return
        # Pace the loop. Real USB reads are slow enough on their own, but the
        # simulator (and very small blocks) can otherwise produce frames far
        # faster than the GUI can draw them and starve its event loop.
        if self._cfg.audio:
            # Never idle while producing audio - the synchronous USB read is
            # already real-time paced, and any added delay becomes a dropout.
            delay_ms = 0
        else:
            target = (SWEEP_FRAME_INTERVAL if self._cfg.mode == MODE_SWEEP
                      else FRAME_INTERVAL)
            delay_ms = int(max(0.0, target - (time.perf_counter() - t0)) * 1000.0)
        self._schedule_step(delay_ms)

    def _step_spectrum(self) -> None:
        cfg = self._cfg
        iq = self._read(self._block_size())
        if iq is None:
            return
        if cfg.audio:
            self._emit_audio(iq)
        power = self._emit_frame(iq, cfg.center_hz, with_stats=True)
        if cfg.detect:
            freqs = dsp.freq_axis(cfg.center_hz, cfg.sample_rate, power.size)
            peaks = dsp.find_carriers(
                freqs, power, cfg.snr_threshold_db, cfg.min_bandwidth_hz,
                dc_guard_hz=cfg.sample_rate * 0.004, dc_center_hz=cfg.center_hz,
                smoothing_hz=cfg.smoothing_hz, gap_hz=cfg.gap_hz)
            self.peaks_ready.emit(peaks, cfg.label)

    def _step_sweep(self) -> None:
        cfg = self._cfg
        if not self._sweep_centers:
            self._prepare_sweep()
        center = self._sweep_centers[self._sweep_idx]
        s = self._source
        if s is None:
            return
        try:
            s.set_center_freq(center)
        except SdrError as exc:
            self.error_occurred.emit(str(exc))
            self._running = False
            self.running_changed.emit(False)
            return
        settle = self._read(max(4096, cfg.fft_size))     # discard retune transient
        if settle is None:
            return
        iq = self._read(self._block_size())
        if iq is None:
            return
        power = self._emit_frame(iq, center)
        freqs = dsp.freq_axis(center, cfg.sample_rate, power.size)

        # Keep only the usable centre portion of the step.
        half = cfg.sample_rate * USABLE_FRACTION / 2.0
        mask = np.abs(freqs - center) <= half
        self._sweep_freqs.append(freqs[mask])
        self._sweep_powers.append(power[mask])

        self._sweep_idx += 1
        if self._sweep_idx >= len(self._sweep_centers):
            self._finish_sweep()

    def _finish_sweep(self) -> None:
        cfg = self._cfg
        if not self._sweep_freqs:
            self._prepare_sweep()
            return
        freqs = np.concatenate(self._sweep_freqs)
        power = np.concatenate(self._sweep_powers)
        order = np.argsort(freqs)
        freqs, power = freqs[order], power[order]
        nf = dsp.noise_floor_db(power)
        peaks = dsp.find_carriers(freqs, power, cfg.snr_threshold_db,
                                  cfg.min_bandwidth_hz, max_carriers=200,
                                  smoothing_hz=cfg.smoothing_hz,
                                  gap_hz=cfg.gap_hz)
        result = SweepResult(
            freqs=freqs, power_db=power, noise_floor_db=nf, peaks=peaks,
            start_hz=float(freqs[0]), stop_hz=float(freqs[-1]),
            duration_s=time.time() - self._sweep_t0, label=cfg.label,
        )
        self.sweep_ready.emit(result)
        self.peaks_ready.emit(peaks, cfg.label)
        self._prepare_sweep()          # loop the sweep continuously

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------
    @Slot()
    def run_hardware_test(self) -> None:
        from .hardware_test import run_hardware_test
        was_running = self._running
        self.stop()
        self._busy_test = True
        try:
            report = run_hardware_test(
                self, progress=self.test_progress.emit)
        except Exception as exc:  # pragma: no cover
            report = {"ok": False, "steps": [], "summary": "Test crashed: %s" % exc}
        finally:
            self._busy_test = False
        report["kind"] = "hardware"
        self.test_finished.emit(report)
        self.emit_device_state()
        if was_running:
            self.start(self._cfg)

    @Slot()
    def run_antenna_test(self) -> None:
        from .antenna_test import run_antenna_test
        was_running = self._running
        self.stop()
        self._busy_test = True
        try:
            report = run_antenna_test(self, progress=self.test_progress.emit)
        except Exception as exc:  # pragma: no cover
            report = {"ok": False, "verdict": "Unable to determine",
                      "steps": [], "summary": "Test crashed: %s" % exc}
        finally:
            self._busy_test = False
        report["kind"] = "antenna"
        self.test_finished.emit(report)
        self.emit_device_state()
        if was_running:
            self.start(self._cfg)

    # Helpers used by the test modules (they run inside the worker thread).
    def _test_source(self):
        if self._source is None or not self._source.is_open:
            if not self.open_device():
                return None
        return self._source
