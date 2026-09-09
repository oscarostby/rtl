"""Audio demodulation: wide FM, narrow FM and AM.

Sample rates are chosen so every decimation factor is an integer, and every
filter keeps its state between blocks. That matters more than it sounds: a
per-block filter restart puts a click at every block boundary, which at 15
blocks a second is a buzz rather than audio.

    2.400 MS/s  /10 -> 240 kHz  /5 -> 48 kHz     (wide FM broadcast)
    1.920 MS/s   /8 -> 240 kHz  /5 -> 48 kHz     (narrow FM, AM)
"""
from __future__ import annotations

import numpy as np
from scipy import signal

AUDIO_RATE = 48_000
IF_RATE = 240_000

MODE_WFM = "WFM"
MODE_NFM = "NFM"
MODE_AM = "AM"
# Envelope audification. Not a demodulator: it recovers no modulation at all.
# See Demodulator._audify for what it does and why it cannot do more.
MODE_ENVELOPE = "ENV"
MODES = [MODE_WFM, MODE_NFM, MODE_AM, MODE_ENVELOPE]

# Preferred capture rate per mode, so the decimation chain stays integer.
MODE_SAMPLE_RATE = {
    MODE_WFM: 2.4e6,
    MODE_NFM: 1.92e6,
    MODE_AM: 1.92e6,
    MODE_ENVELOPE: 1.92e6,
}

# Channel bandwidth (Hz) and de-emphasis time constant (s). 50 us is the
# European standard; the Americas use 75 us.
MODE_BANDWIDTH = {
    MODE_WFM: 180_000.0,
    MODE_NFM: 12_500.0,
    MODE_AM: 10_000.0,
    MODE_ENVELOPE: 25_000.0,      # one TETRA channel
}
DEEMPHASIS_TAU = 50e-6

# Envelope audification, as a parking sensor. A steady tone told the listener
# nothing, so instead the beeping gets faster and higher as the signal gets
# stronger - the one sound everybody already knows how to read. The transmitter
# only opens the gate: it says when something is on air, never what it is
# saying. Strength comes from the app, so the sound always agrees with the
# meter on screen.
ENVELOPE_PITCH_HZ = (330.0, 1150.0)
ENVELOPE_RATE_HZ = (1.6, 12.0)
ENVELOPE_DUTY = 0.35             # how much of each period actually sounds
ENVELOPE_SILENCE = 0.04          # below this strength, say nothing at all
ENVELOPE_STRENGTH_TAU = 0.35     # seconds, so the sound does not jitter
# Bandlimiting a phase-modulated signal turns some of that phase into amplitude
# ripple, so a raw envelope still carries a trace of the symbol transitions.
# The bursts we want to hear are at ~17.6 Hz and the symbols are at 18 kBd, so
# smoothing the envelope well below the symbol rate removes that trace entirely
# while leaving the burst rhythm untouched.
ENVELOPE_SMOOTH_HZ = 200.0
ENVELOPE_AGC_TAU = 2.0           # seconds for the reference level to decay
ENVELOPE_CONTRAST = 2.0          # sharpens the gap between burst and silence


class Demodulator:
    """Turns a block of IQ into a block of mono audio at 48 kHz."""

    def __init__(self, mode: str = MODE_WFM, sample_rate: float = 2.4e6,
                 offset_hz: float = 0.0):
        self.mode = mode if mode in MODES else MODE_WFM
        self.sample_rate = float(sample_rate)
        self.offset_hz = float(offset_hz)
        self.squelch_db = -200.0
        self.gain = 1.0
        # 0..1, set by the application from the same meter the user is looking
        # at, so what is heard and what is shown cannot disagree.
        self.strength = 0.0
        self._last_rms_db = -200.0
        self._muted = False
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        fs = self.sample_rate
        self._dec1 = max(1, int(round(fs / IF_RATE)))
        self._if_rate = fs / self._dec1
        self._dec2 = max(1, int(round(self._if_rate / AUDIO_RATE)))
        self._audio_rate = self._if_rate / self._dec2
        # Decimation is a stream operation.  Remember the next sample phase so
        # a device block whose size is not an exact multiple of the factor does
        # not introduce a short/long sample interval at every boundary.
        self._dec1_phase = 0
        self._dec2_phase = 0

        bw = MODE_BANDWIDTH[self.mode]
        # Stage 1: bring the tuned channel down to the IF rate.
        cutoff = min(bw / 2.0 * 1.25, self._if_rate / 2.0 * 0.9)
        self._b1 = signal.firwin(65, cutoff / (fs / 2.0))
        self._z1 = np.zeros(len(self._b1) - 1, dtype=np.complex128)

        # Stage 2 differs per mode - see process().
        if self.mode == MODE_WFM:
            audio_cut = 15_000.0
        elif self.mode == MODE_NFM:
            audio_cut = 4_000.0
        elif self.mode == MODE_ENVELOPE:
            # Half a TETRA channel, so the whole burst is inside the filter and
            # its edges stay sharp enough to hear as a rattle.
            audio_cut = 12_500.0
        else:
            audio_cut = 5_000.0
        self._b2 = signal.firwin(65, audio_cut / (self._if_rate / 2.0))
        self._z2 = np.zeros(len(self._b2) - 1)
        self._z2c = np.zeros(len(self._b2) - 1, dtype=np.complex128)

        # De-emphasis, as a one-pole IIR at the audio rate.
        alpha = 1.0 - np.exp(-1.0 / (self._audio_rate * DEEMPHASIS_TAU))
        self._deemph_b = np.array([alpha])
        self._deemph_a = np.array([1.0, -(1.0 - alpha)])
        self._zd = np.zeros(1)

        # AM envelope detection leaves the carrier as a large DC component.
        # A stateful DC blocker removes it without making the result depend on
        # the size of each incoming IQ block.
        self._am_dc_b, self._am_dc_a = signal.butter(
            1, 30.0, btype="highpass", fs=self._audio_rate)
        self._zam_dc = np.zeros(
            max(len(self._am_dc_a), len(self._am_dc_b)) - 1)

        # Envelope smoothing: stateful, so the result does not depend on how
        # the incoming IQ happens to be cut into blocks.
        self._env_lp_b, self._env_lp_a = signal.butter(
            2, ENVELOPE_SMOOTH_HZ, btype="lowpass", fs=self._audio_rate)
        self._z_env = np.zeros(
            max(len(self._env_lp_a), len(self._env_lp_b)) - 1)

        self._prev = np.complex128(0.0)      # discriminator carry-over
        self._phase = 0.0                    # mixer phase carry-over
        self._tone_phase = 0.0               # audification tone carry-over
        self._lfo_phase = 0.0                # beep repetition carry-over
        self._strength_now = 0.0             # smoothed strength carry-over
        self._env_ref = 0.0                  # slow peak, for the envelope AGC

    # ------------------------------------------------------------------
    def configure(self, mode: str | None = None, sample_rate: float | None = None,
                  offset_hz: float | None = None) -> None:
        changed = False
        if mode is not None and mode in MODES and mode != self.mode:
            self.mode = mode
            changed = True
        if sample_rate is not None and abs(sample_rate - self.sample_rate) > 1.0:
            self.sample_rate = float(sample_rate)
            changed = True
        if offset_hz is not None and abs(offset_hz - self.offset_hz) > 0.5:
            self.offset_hz = float(offset_hz)
        if changed:
            self._build()

    @property
    def audio_rate(self) -> int:
        return int(round(self._audio_rate))

    @property
    def last_level_db(self) -> float:
        return float(self._last_rms_db)

    @property
    def is_muted_by_squelch(self) -> bool:
        return bool(self._muted)

    # ------------------------------------------------------------------
    def _shift(self, iq: np.ndarray) -> np.ndarray:
        """Move the wanted signal to zero, so you can listen off-centre."""
        if abs(self.offset_hz) < 1.0:
            return iq
        n = iq.size
        step = -2.0 * np.pi * self.offset_hz / self.sample_rate
        phase = self._phase + step * np.arange(n)
        self._phase = float((self._phase + step * n) % (2.0 * np.pi))
        return iq * np.exp(1j * phase)

    def _decimate(self, values: np.ndarray, factor: int,
                  phase_attr: str) -> np.ndarray:
        """Select every *factor*th sample, continuously across blocks."""
        if factor <= 1:
            return values
        phase = int(getattr(self, phase_attr))
        out = values[phase::factor]
        setattr(self, phase_attr, int((phase - values.size) % factor))
        return out

    def process(self, iq: np.ndarray) -> np.ndarray:
        """IQ block in, mono float32 audio out (roughly -1..1)."""
        if iq is None or iq.size == 0:
            return np.zeros(0, dtype=np.float32)
        x = self._shift(np.asarray(iq, dtype=np.complex128))

        # Stage 1: channel filter, then decimate to the IF rate.
        x, self._z1 = signal.lfilter(self._b1, [1.0], x, zi=self._z1)
        x = self._decimate(x, self._dec1, "_dec1_phase")
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)

        # Signal level, for the squelch and the level meter.
        power = float(np.mean(x.real ** 2 + x.imag ** 2))
        # Plain Python floats and bools: numpy scalars do not satisfy Qt's
        # Signal(float, bool) and raise on emit.
        self._last_rms_db = float(10.0 * np.log10(power + 1e-20))

        if self.mode == MODE_ENVELOPE:
            x, self._z2c = signal.lfilter(self._b2, [1.0], x, zi=self._z2c)
            audio = self._audify(
                self._decimate(np.abs(x), self._dec2, "_dec2_phase"))
            scale = 1.0
        elif self.mode == MODE_AM:
            x, self._z2c = signal.lfilter(self._b2, [1.0], x, zi=self._z2c)
            audio = self._decimate(np.abs(x), self._dec2, "_dec2_phase")
            audio, self._zam_dc = signal.lfilter(
                self._am_dc_b, self._am_dc_a, audio, zi=self._zam_dc)
            scale = 4.0
        else:
            # Frequency discriminator: phase difference sample to sample.
            prev = np.empty(x.size, dtype=np.complex128)
            prev[0] = self._prev
            prev[1:] = x[:-1]
            self._prev = x[-1]
            disc = np.angle(x * np.conj(prev))

            disc, self._z2 = signal.lfilter(self._b2, [1.0], disc, zi=self._z2)
            audio = self._decimate(disc, self._dec2, "_dec2_phase")
            # Normalise so full deviation is roughly full scale.
            deviation = 75_000.0 if self.mode == MODE_WFM else 2_500.0
            scale = self._if_rate / (2.0 * np.pi * deviation)

        audio = audio * scale
        if self.mode not in (MODE_AM, MODE_ENVELOPE):
            audio, self._zd = signal.lfilter(self._deemph_b, self._deemph_a,
                                             audio, zi=self._zd)

        self._muted = bool(self._last_rms_db < self.squelch_db)
        if self._muted:
            audio = np.zeros_like(audio)

        audio = audio * self.gain
        np.clip(audio, -1.0, 1.0, out=audio)
        return audio.astype(np.float32)


    def _peak_follow(self, env: np.ndarray) -> np.ndarray:
        """Reference level: follow every rise at once, fall away slowly.

        Written per sample rather than per block. Taking one maximum over each
        arriving block would be simpler, but then the sound would depend on how
        the stream happened to be cut up - and block sizes vary in normal use,
        so the same transmission would not sound the same twice.

            y[i] = max(env[i], a * y[i-1])

        which unrolls to a running maximum once each sample is divided by its
        own decay, so it can be evaluated exactly in one pass.
        """
        a = float(np.exp(-1.0 / (self._audio_rate * ENVELOPE_AGC_TAU)))
        decay = a ** np.arange(1, env.size + 1)
        running = np.maximum.accumulate(
            np.concatenate(([self._env_ref], env / decay)))[1:]
        follower = decay * running
        self._env_ref = float(follower[-1])
        return follower

    def _audify(self, env: np.ndarray) -> np.ndarray:
        """Turn a transmission into a sound a driver can read at a glance.

        `env` is |x|: how much power is arriving, sample by sample. Taking the
        magnitude discards the phase, and for TETRA the phase is where all of
        the information lives - it is pi/4-DQPSK, so every symbol is a phase
        step. Throwing the phase away is not a weak form of decoding, it is the
        removal of the only thing a decoder could work from; no amount of later
        processing can put it back. The envelope is then smoothed to a
        hundredth of the symbol rate, which also removes the amplitude ripple
        that bandlimiting converts out of the phase.

        What is left is used for one thing only: to open and close a gate, so
        the beeping is heard while a transmitter is on air and stops when it
        goes quiet. Its speed and pitch come from the measured signal strength,
        not from anything in the signal itself.
        """
        if env.size == 0:
            return env.astype(np.float32)
        # Smooth far below the symbol rate. This is what makes the paragraph
        # above true rather than merely intended: the ripple that bandlimiting
        # converts from phase into amplitude sits at the symbol rate and is
        # removed here, leaving only the burst envelope.
        env, self._z_env = signal.lfilter(self._env_lp_b, self._env_lp_a,
                                          env, zi=self._z_env)
        env = np.maximum(env, 0.0)
        gate = np.clip(env / (self._peak_follow(env) + 1e-12), 0.0, 1.0)
        gate = gate ** ENVELOPE_CONTRAST

        n = env.size
        rate = self._audio_rate
        # Approach the commanded strength smoothly, and exactly the same way
        # however the stream is cut into blocks.
        target = float(np.clip(self.strength, 0.0, 1.0))
        step = float(np.exp(-1.0 / (rate * ENVELOPE_STRENGTH_TAU)))
        s = target + (self._strength_now - target) * step ** np.arange(1, n + 1)
        self._strength_now = float(s[-1])

        lo, hi = ENVELOPE_PITCH_HZ
        rlo, rhi = ENVELOPE_RATE_HZ
        tone = self._tone_phase + np.cumsum(2.0 * np.pi * (lo + (hi - lo) * s)
                                            / rate)
        beat = self._lfo_phase + np.cumsum(2.0 * np.pi * (rlo + (rhi - rlo) * s)
                                           / rate)
        self._tone_phase = float(tone[-1] % (2.0 * np.pi))
        self._lfo_phase = float(beat[-1] % (2.0 * np.pi))

        # One short beep per period, faded in and out so it does not click.
        frac = (beat / (2.0 * np.pi)) % 1.0
        pulse = np.where(frac < ENVELOPE_DUTY,
                         0.5 * (1.0 - np.cos(2.0 * np.pi * frac
                                             / ENVELOPE_DUTY)), 0.0)
        # Nothing worth hearing is nothing to play.
        audible = (s >= ENVELOPE_SILENCE).astype(np.float64)
        return (0.8 * audible * gate * pulse
                * np.sin(tone)).astype(np.float32)


def to_pcm16(audio: np.ndarray) -> bytes:
    """Convert float audio to little-endian 16-bit PCM."""
    if audio.size == 0:
        return b""
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()
