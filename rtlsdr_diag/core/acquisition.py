"""One shared description of what the receiver is doing right now.

Before this existed the interface could say "Device: connected", "Idle" and
"Acquisition stopped" in three different corners and leave you to work out
which mattered. There is now a single state, one owner, and one line of text
that every page shows.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AcquisitionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED_IDLE = "CONNECTED_IDLE"
    STARTING = "STARTING"
    SCANNING = "SCANNING"
    TUNED = "TUNED"
    ERROR = "ERROR"


# Which states mean the receiver is actively producing samples.
RUNNING_STATES = {AcquisitionState.STARTING, AcquisitionState.SCANNING,
                  AcquisitionState.TUNED}


@dataclass
class AcquisitionStatus:
    """The whole picture: state, who owns the device, and what it is doing."""

    state: AcquisitionState = AcquisitionState.DISCONNECTED
    owner: str = ""                  # tab label that started acquisition
    center_hz: float = 0.0
    start_hz: float = 0.0
    stop_hz: float = 0.0
    sample_rate: float = 0.0
    gain_db: object = "auto"
    ppm: int = 0
    simulated: bool = False
    detail: str = ""                 # error text, or extra context

    # -- derived -----------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self.state in RUNNING_STATES

    @property
    def is_connected(self) -> bool:
        return self.state not in (AcquisitionState.DISCONNECTED,
                                  AcquisitionState.ERROR)

    def headline(self) -> str:
        """The single line shown at the top of every page."""
        prefix = "SIM" if self.simulated else "SDR"
        s = self.state
        if s is AcquisitionState.DISCONNECTED:
            return "%s NOT CONNECTED" % prefix
        if s is AcquisitionState.ERROR:
            return "%s ERROR - %s" % (prefix, self.detail or "DEVICE LOST")
        if s is AcquisitionState.CONNECTED_IDLE:
            return "%s CONNECTED - IDLE" % prefix
        if s is AcquisitionState.STARTING:
            return "%s STARTING..." % prefix
        if s is AcquisitionState.SCANNING:
            if self.stop_hz > self.start_hz > 0:
                return "%s SCANNING %.3f-%.3f MHz" % (
                    prefix, self.start_hz / 1e6, self.stop_hz / 1e6)
            return "%s SCANNING" % prefix
        if s is AcquisitionState.TUNED:
            return "%s TUNED %.4f MHz" % (prefix, self.center_hz / 1e6)
        return "%s %s" % (prefix, s.value)

    def owner_text(self) -> str:
        if not self.is_running or not self.owner:
            return ""
        return "LIVE DATA FROM: %s" % self.owner

    def colour_key(self) -> str:
        """'good' | 'warn' | 'bad' | 'dim', for the theme to map to a colour."""
        s = self.state
        if s in (AcquisitionState.DISCONNECTED, AcquisitionState.ERROR):
            return "bad"
        if self.simulated:
            return "warn"
        if s in RUNNING_STATES:
            return "good"
        return "dim"

    def settings_line(self) -> str:
        bits = []
        if self.sample_rate:
            bits.append("%.3f MS/s" % (self.sample_rate / 1e6))
        gain = self.gain_db
        bits.append("gain AUTO" if isinstance(gain, str)
                    else "gain %.1f dB" % float(gain))
        bits.append("%+d ppm" % int(self.ppm))
        return "   ".join(bits)


def state_for(connected: bool, running: bool, mode: str = "",
              error: str = "") -> AcquisitionState:
    """Work out the state from the engine's raw flags."""
    if error:
        return AcquisitionState.ERROR
    if not connected:
        return AcquisitionState.DISCONNECTED
    if not running:
        return AcquisitionState.CONNECTED_IDLE
    return (AcquisitionState.SCANNING if mode == "sweep"
            else AcquisitionState.TUNED)
