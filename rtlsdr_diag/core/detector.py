"""Detector modes, candidate selection and the multi-band scan rotation.

There is one radio and three bands, so the scan visits them in turn. The band
the user is looking at gets half the sweeps and the other two share the rest,
which keeps the visible page responsive while the other two stay warm. Changing
page only reprioritises this rotation - the receiver is never reopened.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import TETRA_DOWNLINK_BAND, TETRA_UPLINK_BAND
from .models import (CLASS_AMATEUR, CLASS_DAB, CLASS_FM, CLASS_ISM,
                     CLASS_TETRA, CLASS_UNKNOWN)
from .proximity import proximity_fraction, proximity_label, trend
from .smoothing import MEDIUM, LevelSmoother

FM = "FM"
DAB = "DAB"
TETRA_MAST = "TETRA_MAST"
TETRA_MOBILE = "TETRA_MOBILE"
MODES = [FM, DAB, TETRA_MAST, TETRA_MOBILE]

# Older name, kept pointing at the downlink page.
TETRA = TETRA_MAST

# How much stronger a rival must be, and for how long, before the display
# switches to it. Without this the readout flickers between two signals of
# almost equal strength.
SWITCH_MARGIN_DB = 3.0
SWITCH_DWELL_S = 2.0

# A candidate goes stale if it has not been seen for this long.
CANDIDATE_TIMEOUT_S = 12.0

METER_SEGMENTS = 12


@dataclass(frozen=True)
class ModeSpec:
    """A band, the classes that belong to it, and how to detect in it."""

    key: str
    title: str
    start_hz: float
    stop_hz: float
    sample_rate: float
    snr_threshold_db: float
    min_bandwidth_hz: float
    smoothing_hz: float
    gap_hz: float
    classes: tuple
    caption: str = ""
    bursty: bool = False
    # How this band can be listened to. "WFM" is real demodulated audio; "ENV"
    # is envelope audification, which plays the rhythm of a transmission and
    # cannot recover its content (see sdr/demod.py). Empty means the band
    # carries digital data this program does not decode, so there is nothing
    # to play - audio_note says why.
    audio_mode: str = ""
    audio_note: str = ""
    # Tuner gain for this band: a number in dB, "max", or "auto" for the
    # tuner's own AGC. One setting does not fit every band - measured here,
    # FM needs everything the tuner has before a station resolves at all,
    # while the same gain on the TETRA downlink overloads the front end and
    # invents carriers that are not there.
    gain: object = "auto"
    # How to read this band's name out loud. The titles are set in capitals
    # for the screen, which a voice reads letter by letter.
    spoken: str = ""
    # Seconds to sit on each sweep step, and how to combine the block. Left at
    # 0/"mean" a step lasts however long fft_size*averages takes - about 16 ms,
    # which is shorter than the 42 ms of silence between one TDMA burst and the
    # next, so a step can land in the gap and see nothing.
    dwell_s: float = 0.0
    psd_combine: str = "mean"

    def range_text(self) -> str:
        return "%.1f - %.1f MHz" % (self.start_hz / 1e6, self.stop_hz / 1e6)

    def accepts(self, detection) -> bool:
        """Class *and* frequency must match.

        TETRA is frequency-duplex: handsets transmit in one sub-band and base
        stations in another, so the frequency alone separates a mast from a
        mobile. Nothing else about the signal is examined, and nothing here
        identifies who is transmitting.
        """
        return (detection.signal_class in self.classes
                and self.contains(detection.freq_hz))

    def contains(self, freq_hz: float) -> bool:
        """Half-open: [start, stop).

        The uplink half ends where the downlink half begins, so an inclusive
        top edge would put a carrier exactly on the seam in both bands at once.
        """
        return self.start_hz <= freq_hz < self.stop_hz


MODE_SPECS = {
    FM: ModeSpec(
        FM, "FM", 87.5e6, 108.0e6, 2.4e6, 10.0,
        min_bandwidth_hz=60e3, smoothing_hz=15e3, gap_hz=40e3,
        classes=(CLASS_FM,), caption="broadcast", audio_mode="WFM",
        gain="max", spoken="F M"),
    DAB: ModeSpec(
        DAB, "DAB", 174.0e6, 240.0e6, 2.4e6, 10.0,
        min_bandwidth_hz=500e3, smoothing_hz=100e3, gap_hz=300e3,
        classes=(CLASS_DAB,), caption="broadcast ensemble",
        audio_note="DAB is a digital multiplex - playing it needs a full "
                   "COFDM and AAC decoder, which this program does not have.",
        gain=40.0, spoken="D A B"),
    # Downlink: base stations. Continuous and high power from a mast, which
    # is why it dominates indoors and is nearly always present.
    # 390-395 MHz is the emergency-services downlink; the window is carried up
    # to 400 because other licensed TETRA infrastructure sits above it, and a
    # continuous high-power carrier there is still a mast, not a handset.
    TETRA_MAST: ModeSpec(
        TETRA_MAST, "TETRA MAST", TETRA_DOWNLINK_BAND[0], TETRA_DOWNLINK_BAND[1],
        2.048e6, 8.0, min_bandwidth_hz=10e3, smoothing_hz=2e3, gap_hz=5e3,
        classes=(CLASS_TETRA,),
        caption="base station downlink - continuous", audio_mode="ENV",
        audio_note="Plays the rhythm of the transmission, not its content.",
        gain="auto", spoken="TETRA mast"),
    # Uplink: handsets and vehicle radios. A couple of watts, and only while
    # someone is actually transmitting, so this is bursty and short range.
    TETRA_MOBILE: ModeSpec(
        TETRA_MOBILE, "TETRA MOBILE", TETRA_UPLINK_BAND[0], TETRA_UPLINK_BAND[1],
        # 2.4 MS/s rather than the mast band's 2.048: the receiver can only sit
        # on 75% of its sample rate at once, and the strong uplink partners at
        # one real site spanned 1.61 MHz - just wider than 2.048 allows, so a
        # single window had to drop one of them.
        2.4e6, 8.0, min_bandwidth_hz=10e3, smoothing_hz=2e3, gap_hz=5e3,
        classes=(CLASS_TETRA,),
        caption="terminal uplink - a radio transmitting near you",
        bursty=True, audio_mode="ENV",
        audio_note="Plays the rhythm of the transmission, not its content.",
        gain="max", spoken="TETRA mobile radio",
        # One TETRA frame is 56.7 ms and a terminal occupies one slot in four.
        # 70 ms guarantees a whole frame is inside the step, and keeping each
        # bin's loudest segment measures the burst instead of averaging it
        # against the three quarters of silence around it.
        dwell_s=0.070, psd_combine="max"),
}


class DetectorState:
    """What one page is showing: its candidate, meter, trend and lock."""

    def __init__(self, spec: ModeSpec):
        self.spec = spec
        self.current = None            # LiveSignalDetection
        self.locked_freq_hz: float | None = None
        self.meter = LevelSmoother(MEDIUM)
        self._rival = None
        self._rival_since = 0.0
        self.last_update = 0.0
        # Uplink is bursty, so "quiet now, last heard 40 s ago" beats a blank
        # screen. Only the time and frequency are kept - never an identity.
        self.last_activity = 0.0
        self.last_activity_freq_hz = 0.0
        # While listening the sweep is parked, so no detections arrive and the
        # meter is driven by the level in the audio channel instead.
        self.listening = False
        # What the receiver is actually pointed at, when that is narrower than
        # the whole band. Set by the window; shown when the page is quiet.
        self.watch_note = ""
        self.listen_snr_db = 0.0
        self.listen_noise_dbfs = -80.0

    def push_listen_level(self, level_dbfs: float,
                          now: float | None = None) -> None:
        if now is None:
            now = time.time()
        self.listen_snr_db = max(0.0, level_dbfs - self.listen_noise_dbfs)
        self.meter.update(self.listen_snr_db, now)
        self.last_update = now
        if self.listen_snr_db > 3.0:
            self.last_activity = now
            if self.current is not None:
                self.last_activity_freq_hz = self.current.freq_hz

    def shown_snr_db(self) -> float:
        """SNR to display: live from the audio channel while parked."""
        if self.listening:
            return self.listen_snr_db
        return self.current.snr_db if self.current is not None else 0.0

    # -- lock --------------------------------------------------------------
    @property
    def is_locked(self) -> bool:
        return self.locked_freq_hz is not None

    def toggle_lock(self) -> bool:
        if self.is_locked:
            self.locked_freq_hz = None
        elif self.current is not None:
            self.locked_freq_hz = self.current.freq_hz
        return self.is_locked

    # -- candidate selection ----------------------------------------------
    def candidates(self, detections, now: float | None = None) -> list:
        if now is None:
            now = time.time()
        return [d for d in detections
                if self.spec.accepts(d)
                and (now - d.last_seen) <= CANDIDATE_TIMEOUT_S]

    def update(self, detections, now: float | None = None):
        """Pick what to display, with hysteresis, and advance the meter."""
        if now is None:
            now = time.time()
        self.last_update = now
        pool = self.candidates(detections, now)

        if self.is_locked:
            # Follow the locked frequency and nothing else.
            match = None
            for d in pool:
                if abs(d.freq_hz - self.locked_freq_hz) <= 30e3:
                    if match is None or d.snr_db > match.snr_db:
                        match = d
            self.current = match
            self._rival = None
            self._push_meter(now)
            self._note_activity(now)
            return self.current

        if not pool:
            self.current = None
            self._rival = None
            self._push_meter(now)
            return None

        best = max(pool, key=lambda d: d.snr_db)
        still_present = any(d is self.current for d in pool)
        if self.current is None or not still_present:
            self.current = best
            self._rival = None
            self._push_meter(now)
            self._note_activity(now)
            return self.current

        if best is self.current:
            self._rival = None
        elif best.snr_db >= self.current.snr_db + SWITCH_MARGIN_DB:
            # Clearly stronger - but it has to stay that way for a while.
            if self._rival is not best:
                self._rival = best
                self._rival_since = now
            elif now - self._rival_since >= SWITCH_DWELL_S:
                self.current = best
                self._rival = None
        else:
            self._rival = None

        self._push_meter(now)
        self._note_activity(now)
        return self.current

    def _note_activity(self, now: float) -> None:
        if self.current is not None:
            self.last_activity = now
            self.last_activity_freq_hz = self.current.freq_hz

    def seconds_since_activity(self, now: float | None = None) -> float:
        if not self.last_activity:
            return float("inf")
        return (time.time() if now is None else now) - self.last_activity

    # -- display values ----------------------------------------------------
    def _push_meter(self, now: float) -> None:
        if self.current is None:
            # Fall away smoothly rather than snapping to nothing.
            self.meter.update(-5.0, now)
        else:
            self.meter.update(self.current.snr_db, now)

    @property
    def meter_snr_db(self) -> float:
        v = self.meter.value
        return 0.0 if v != v else max(0.0, v)

    def strength_fraction(self) -> float:
        """0..1, the same number the meter is drawn from."""
        return proximity_fraction(self.meter_snr_db)

    def segments(self, total: int = METER_SEGMENTS) -> int:
        return int(round(proximity_fraction(self.meter_snr_db) * total))

    def strength_label(self) -> str:
        return proximity_label(self.meter_snr_db)

    def trend(self, window_s: float = 15.0):
        if self.current is None:
            return trend([], window_s)
        return trend(self.current.history, window_s)

    def channel_name(self) -> str:
        """Nearest 25 kHz TETRA channel - arithmetic on the published raster."""
        if self.current is None or self.spec.key not in (TETRA_MAST,
                                                         TETRA_MOBILE):
            return ""
        from ..config import TETRA_CHANNEL_WIDTH_HZ
        channel = (round(self.current.freq_hz / TETRA_CHANNEL_WIDTH_HZ)
                   * TETRA_CHANNEL_WIDTH_HZ)
        return "%.4f" % (channel / 1e6)

    def block_name(self) -> str:
        """DAB block from the standard raster - arithmetic, not decoded data."""
        if self.current is None or self.spec.key != DAB:
            return ""
        from ..config import dab_block_for
        return dab_block_for(self.current.freq_hz)


TREND_RAPID_DB = 6.0


def trend_label(result) -> tuple[str, str]:
    """Five-level trend arrow and words from a TrendResult."""
    if result.samples < 4:
        return "", ""
    change = result.change_db
    if change >= TREND_RAPID_DB:
        return "↑↑", "RAPIDLY RISING"
    if change >= 2.0:
        return "↑", "RISING"
    if change <= -TREND_RAPID_DB:
        return "↓↓", "RAPIDLY FALLING"
    if change <= -2.0:
        return "↓", "FALLING"
    return "→", "STABLE"


class BandRotation:
    """Which band the receiver should sweep next.

    The visible band gets every other slot; the other two alternate in between,
    so the page being watched updates about twice as often as the ones that are
    not, and nothing goes stale. In focused mode the radio stays on the visible
    band instead, trading the other pages for the fastest possible revisit.
    """

    def __init__(self, modes=None):
        self.modes = list(modes or MODES)
        self._others_index = 0
        self._on_visible = False
        self.focused = False
        self.current = self.modes[0]

    def next_band(self, visible_mode: str) -> str:
        if visible_mode not in self.modes:
            visible_mode = self.modes[0]
        if self.focused:
            # Every pass on the band being watched. Sharing the radio costs up
            # to about three seconds between two looks at a narrow band,
            # because the broadcast bands are wide and slow to sweep - long
            # enough to miss a short transmission entirely.
            self.current = visible_mode
            return self.current
        others = [m for m in self.modes if m != visible_mode]
        self._on_visible = not self._on_visible
        if self._on_visible or not others:
            self.current = visible_mode
        else:
            self.current = others[self._others_index % len(others)]
            self._others_index += 1
        return self.current
