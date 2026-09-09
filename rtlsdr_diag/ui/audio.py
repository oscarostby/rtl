"""Audio playback through Qt Multimedia.

Push mode: the acquisition thread produces PCM, the GUI thread writes it into
the sink. Kept deliberately small - if there is no working output device the
rest of the application must carry on regardless.
"""
from __future__ import annotations

from PySide6.QtCore import QObject

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
    AUDIO_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - depends on the Qt build
    QAudioFormat = QAudioSink = QMediaDevices = None
    AUDIO_IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)


class AudioPlayer(QObject):
    """A mono 16-bit PCM sink you can push bytes into."""

    def __init__(self, sample_rate: int = 48_000, parent=None):
        super().__init__(parent)
        self.sample_rate = int(sample_rate)
        self._sink = None
        self._io = None
        self.last_error = AUDIO_IMPORT_ERROR or ""
        self._volume = 0.6
        self._dropped = 0
        self._written = 0

    # ------------------------------------------------------------------
    @property
    def is_available(self) -> bool:
        return QAudioSink is not None

    @property
    def is_playing(self) -> bool:
        return self._io is not None

    def device_name(self) -> str:
        if QMediaDevices is None:
            return "unavailable"
        try:
            return QMediaDevices.defaultAudioOutput().description() or "default output"
        except Exception:
            return "unknown"

    def stats(self) -> dict:
        return {"written_bytes": self._written, "dropped_blocks": self._dropped,
                "device": self.device_name(), "playing": self.is_playing}

    # ------------------------------------------------------------------
    def start(self, sample_rate: int | None = None) -> bool:
        if not self.is_available:
            self.last_error = AUDIO_IMPORT_ERROR or "Qt Multimedia is not available."
            return False
        if sample_rate:
            self.sample_rate = int(sample_rate)
        self.stop()
        try:
            fmt = QAudioFormat()
            fmt.setSampleRate(self.sample_rate)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.Int16)

            device = QMediaDevices.defaultAudioOutput()
            if device is None or device.isNull():
                self.last_error = "No audio output device was found."
                return False
            if not device.isFormatSupported(fmt):
                self.last_error = ("The default audio device does not support "
                                   "%d Hz mono 16-bit output." % self.sample_rate)
                return False

            self._sink = QAudioSink(device, fmt, self)
            # Roughly a quarter second of buffer: enough to ride out a slow
            # redraw, short enough that tuning still feels immediate.
            self._sink.setBufferSize(self.sample_rate // 2)
            self._sink.setVolume(self._volume)
            self._io = self._sink.start()
            if self._io is None:
                self.last_error = "The audio device could not be opened."
                self._sink = None
                return False
            self.last_error = ""
            self._dropped = 0
            self._written = 0
            return True
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self._sink = None
            self._io = None
            return False

    def stop(self) -> None:
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
        self._sink = None
        self._io = None

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        if self._sink is not None:
            try:
                self._sink.setVolume(self._volume)
            except Exception:
                pass

    def write(self, pcm: bytes) -> None:
        if not pcm or self._io is None:
            return
        try:
            free = self._sink.bytesFree() if self._sink is not None else len(pcm)
            if free < len(pcm):
                # The sink is behind; drop this block rather than let latency
                # grow without bound.
                self._dropped += 1
                return
            written = int(self._io.write(pcm))
            if written < 0:
                self.last_error = "The audio output rejected a PCM block."
                self._dropped += 1
                return
            self._written += written
            if written < len(pcm):
                # QIODevice writes are allowed to be partial even after a
                # bytesFree() check.  The unwritten tail is intentionally
                # discarded so tuning never accumulates stale audio latency.
                self._dropped += 1
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
