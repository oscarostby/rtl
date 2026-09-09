"""Saying what the detector found, out loud.

A meter and a beep both need interpreting. Words do not, which matters when the
listener is driving and must not look at the screen.

What is said is deliberately narrow: the kind of transmitter (a mast or a
mobile terminal), how strong it is, and its frequency. That is what the
detector measures. It never claims to know who is transmitting or what is being
said, because it does not.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject

MIN_GAP_S = 6.0


class Announcer(QObject):
    """A throttled voice. Silent, and harmless, if the system has none."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = True
        self.last_error = ""
        self._tts = None
        self._said: dict[str, float] = {}
        try:
            from PySide6.QtTextToSpeech import QTextToSpeech
            engines = QTextToSpeech.availableEngines()
            if not engines:
                self.last_error = "no speech engine on this system"
                return
            self._tts = QTextToSpeech(self)
            self._tts.setRate(0.15)       # a little brisk, still clear
            self._tts.setVolume(1.0)
        except Exception as exc:          # no Qt speech module, no voices
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self._tts = None

    @property
    def is_available(self) -> bool:
        return self._tts is not None

    def voice_name(self) -> str:
        if self._tts is None:
            return ""
        try:
            return self._tts.voice().name()
        except Exception:
            return ""

    def say(self, kind: str, text: str, min_gap: float = MIN_GAP_S,
            now: float | None = None) -> bool:
        """Speak, unless this kind of thing was said a moment ago.

        Returns whether it spoke, which is what the tests check.
        """
        if not self.enabled or self._tts is None:
            return False
        now = time.time() if now is None else now
        if now - self._said.get(kind, 0.0) < min_gap:
            return False
        self._said[kind] = now
        try:
            self._tts.say(text)
        except Exception as exc:
            self.last_error = str(exc)
            return False
        return True

    def stop(self) -> None:
        if self._tts is not None:
            try:
                self._tts.stop()
            except Exception:
                pass


def spell_frequency(freq_hz: float) -> str:
    """"391.4125 MHz" as something a voice can read: "391 point 4"."""
    mhz = freq_hz / 1e6
    whole = int(mhz)
    tenths = int(round((mhz - whole) * 10.0))
    if tenths == 10:
        whole, tenths = whole + 1, 0
    return "%d point %d" % (whole, tenths)
