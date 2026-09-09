"""Demodulator checks: feed synthetic FM/AM and confirm the tone comes back."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rtlsdr_diag.sdr.demod import (MODE_AM, MODE_ENVELOPE,  # noqa: E402
                                   MODE_NFM, MODE_WFM,
                                   MODE_SAMPLE_RATE, Demodulator, to_pcm16)

failures = []


def dominant_tone(audio: np.ndarray, rate: int) -> tuple[float, float]:
    """Loudest frequency in the audio, and how far it stands above the rest."""
    if audio.size < 1024:
        return 0.0, 0.0
    w = np.hanning(audio.size)
    spec = np.abs(np.fft.rfft(audio * w))
    freqs = np.fft.rfftfreq(audio.size, 1.0 / rate)
    lo = np.searchsorted(freqs, 50.0)        # ignore DC and de-emphasis lift
    spec[:lo] = 0.0
    k = int(np.argmax(spec))
    peak = spec[k]
    floor = np.median(spec[lo:]) + 1e-12
    return float(freqs[k]), float(20.0 * np.log10(peak / floor))


def fm_signal(fs, seconds, tone_hz, deviation, amplitude=0.5):
    n = int(fs * seconds)
    t = np.arange(n) / fs
    tone = np.sin(2 * np.pi * tone_hz * t)
    phase = 2 * np.pi * deviation * np.cumsum(tone) / fs
    return (amplitude * np.exp(1j * phase)).astype(np.complex64)


def am_signal(fs, seconds, tone_hz, depth=0.6, amplitude=0.4):
    n = int(fs * seconds)
    t = np.arange(n) / fs
    env = 1.0 + depth * np.sin(2 * np.pi * tone_hz * t)
    return (amplitude * env * np.exp(1j * 0.0)).astype(np.complex64)


def run(name, iq, mode, fs, want_hz, tol_hz=30.0, min_snr=20.0, blocks=12):
    """Feed the signal in blocks, as the live path does."""
    dm = Demodulator(mode=mode, sample_rate=fs)
    per = iq.size // blocks
    out = []
    for i in range(blocks):
        out.append(dm.process(iq[i * per:(i + 1) * per]))
    audio = np.concatenate(out)
    # Discard the first chunk while the filters settle.
    audio = audio[audio.size // 6:]
    got, snr = dominant_tone(audio, dm.audio_rate)
    ok = abs(got - want_hz) <= tol_hz and snr >= min_snr
    print("%-34s tone %8.1f Hz (want %6.1f)  snr %5.1f dB  rate %d  %s"
          % (name, got, want_hz, snr, dm.audio_rate, "OK" if ok else "FAIL"))
    if not ok:
        failures.append(name)
    return audio


# --- wide FM broadcast ----------------------------------------------------
fs = MODE_SAMPLE_RATE[MODE_WFM]
run("WFM 1 kHz tone", fm_signal(fs, 0.5, 1000.0, 75_000), MODE_WFM, fs, 1000.0)
run("WFM 400 Hz tone", fm_signal(fs, 0.5, 400.0, 75_000), MODE_WFM, fs, 400.0)
run("WFM half deviation", fm_signal(fs, 0.5, 1000.0, 37_500), MODE_WFM, fs, 1000.0)

# --- narrow FM ------------------------------------------------------------
fs = MODE_SAMPLE_RATE[MODE_NFM]
run("NFM 1 kHz tone", fm_signal(fs, 0.5, 1000.0, 2_500), MODE_NFM, fs, 1000.0)
run("NFM 700 Hz tone", fm_signal(fs, 0.5, 700.0, 2_500), MODE_NFM, fs, 700.0)

# --- AM -------------------------------------------------------------------
fs = MODE_SAMPLE_RATE[MODE_AM]
run("AM 1 kHz tone", am_signal(fs, 0.5, 1000.0), MODE_AM, fs, 1000.0, min_snr=15.0)

# --- listening off-centre -------------------------------------------------
fs = MODE_SAMPLE_RATE[MODE_WFM]
offset = 300_000.0
n = int(fs * 0.5)
t = np.arange(n) / fs
base = fm_signal(fs, 0.5, 1000.0, 75_000)
shifted = (base * np.exp(2j * np.pi * offset * t)).astype(np.complex64)
dm = Demodulator(mode=MODE_WFM, sample_rate=fs, offset_hz=offset)
out = [dm.process(shifted[i * (n // 12):(i + 1) * (n // 12)]) for i in range(12)]
audio = np.concatenate(out)[n // 12 // 5:]
got, snr = dominant_tone(audio, dm.audio_rate)
ok = abs(got - 1000.0) <= 30.0 and snr >= 20.0
print("%-34s tone %8.1f Hz (want 1000.0)  snr %5.1f dB  %s"
      % ("WFM 300 kHz off-centre", got, snr, "OK" if ok else "FAIL"))
if not ok:
    failures.append("off-centre")

# --- squelch --------------------------------------------------------------
fs = MODE_SAMPLE_RATE[MODE_NFM]
dm = Demodulator(mode=MODE_NFM, sample_rate=fs)
dm.squelch_db = -10.0                      # far above the test signal
quiet = dm.process(fm_signal(fs, 0.1, 1000.0, 2_500, amplitude=0.01))
print("%-34s %s" % ("squelch mutes weak signal",
                    "OK" if np.allclose(quiet, 0.0) else "FAIL"))
if not np.allclose(quiet, 0.0):
    failures.append("squelch")

# --- block-boundary continuity -------------------------------------------
# One long block versus many short ones must agree, or the filters/decimator
# are being restarted and every boundary will click.  In particular, the live
# engine rounds a 50 ms read to 512 samples, which is not aligned to the final
# /5 decimator and therefore exercises its carried phase.
for mode in (MODE_WFM, MODE_NFM):
    fs = MODE_SAMPLE_RATE[mode]
    deviation = 75_000 if mode == MODE_WFM else 2_500
    live_block = ((int(fs * 0.05) + 511) // 512) * 512
    sig = fm_signal(fs, live_block * 4 / fs, 1000.0, deviation)
    whole = Demodulator(mode=mode, sample_rate=fs).process(sig)
    dm = Demodulator(mode=mode, sample_rate=fs)
    pieces = np.concatenate([
        dm.process(sig[i:i + live_block])
        for i in range(0, sig.size, live_block)
    ])
    same_length = whole.size == pieces.size
    err = (float(np.max(np.abs(whole - pieces)))
           if same_length and whole.size else 1.0)
    ok = same_length and err < 1e-6
    name = "%s live-block continuity" % mode
    print("%-34s lengths %d/%d  max difference %.2e  %s"
          % (name, whole.size, pieces.size, err, "OK" if ok else "FAIL"))
    if not ok:
        failures.append(name)

# Arbitrary, deliberately unaligned input boundaries exercise both decimation
# stages rather than relying on a convenient engine block size.
fs = MODE_SAMPLE_RATE[MODE_WFM]
sig = fm_signal(fs, 0.12, 997.0, 75_000)
whole = Demodulator(mode=MODE_WFM, sample_rate=fs).process(sig)
dm = Demodulator(mode=MODE_WFM, sample_rate=fs)
cuts = [1237, 8111, 23456, 70003, 90127, 155555, sig.size]
start = 0
parts = []
for stop in cuts:
    parts.append(dm.process(sig[start:stop]))
    start = stop
pieces = np.concatenate(parts)
same_length = whole.size == pieces.size
err = (float(np.max(np.abs(whole - pieces)))
       if same_length and whole.size else 1.0)
ok = same_length and err < 1e-6
print("%-34s lengths %d/%d  max difference %.2e  %s"
      % ("arbitrary-block continuity", whole.size, pieces.size, err,
         "OK" if ok else "FAIL"))
if not ok:
    failures.append("arbitrary continuity")

# AM carrier removal must settle quickly enough that a steady carrier does not
# pin the speaker output at full scale for seconds.
fs = MODE_SAMPLE_RATE[MODE_AM]
dm = Demodulator(mode=MODE_AM, sample_rate=fs)
carrier = am_signal(fs, 0.25, 1000.0, depth=0.0)
audio = dm.process(carrier)
tail = audio[audio.size // 2:]
dc_peak = float(np.max(np.abs(tail))) if tail.size else 1.0
ok = dc_peak < 0.01
print("%-34s tail peak %.4f  %s"
      % ("AM carrier/DC removal", dc_peak, "OK" if ok else "FAIL"))
if not ok:
    failures.append("AM DC removal")

# --- envelope audification ------------------------------------------------
# This plays the shape of a transmission. The tests that matter are that the
# shape survives and that nothing else does.
print("")
print("--- envelope audification ---")
fs = MODE_SAMPLE_RATE[MODE_ENVELOPE]
n = int(fs)
t = np.arange(n) / fs
FRAME_HZ = 17.65                      # TETRA frames per second


def env_audio(iq):
    return Demodulator(mode=MODE_ENVELOPE, sample_rate=fs).process(iq)


def dominant(a, rate, lo, hi):
    a = a - a.mean()
    mag = np.abs(np.fft.rfft(a * np.hanning(a.size)))
    freqs = np.fft.rfftfreq(a.size, 1.0 / rate)
    keep = (freqs > lo) & (freqs < hi)
    return float(freqs[keep][int(np.argmax(mag[keep]))])


def symbols(seed):
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.choice([-1, 1, -3, 3], n) * np.pi / 4.0 / 40.0)


# The sound has to be readable without instructions: stronger must mean
# faster and higher, all the way up.
def sonify(strength, iq):
    dm = Demodulator(mode=MODE_ENVELOPE, sample_rate=fs)
    dm.strength = strength
    return dm.process(iq)


carrier = 0.3 * np.exp(2j * np.pi * 3000.0 * t)
rates, pitches = [], []
for strength in (0.2, 0.5, 0.8, 1.0):
    a = sonify(strength, carrier)
    rates.append(dominant(np.abs(a), 48_000, 0.5, 30.0))
    pitches.append(dominant(a, 48_000, 100.0, 3000.0))
ok = all(b > a + 0.4 for a, b in zip(rates, rates[1:]))
print("%-34s %s beeps/s  %s"
      % ("stronger beeps faster", " ".join("%.1f" % r for r in rates),
         "OK" if ok else "FAIL"))
if not ok:
    failures.append("beep rate not monotonic")

ok = all(b > a + 40.0 for a, b in zip(pitches, pitches[1:]))
print("%-34s %s Hz  %s"
      % ("stronger beeps higher", " ".join("%.0f" % f for f in pitches),
         "OK" if ok else "FAIL"))
if not ok:
    failures.append("pitch not monotonic")

# Nothing there must sound like nothing.
quiet = sonify(0.0, carrier)
ok = float(np.max(np.abs(quiet))) < 1e-6
print("%-34s peak %.1e  %s"
      % ("no signal, no sound", float(np.max(np.abs(quiet))),
         "OK" if ok else "FAIL"))
if not ok:
    failures.append("silence")


def symbols(seed):
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.choice([-1, 1, -3, 3], n) * np.pi / 4.0 / 40.0)


# A handset only transmits in its timeslot, so the beeping must stop with it.
slot = ((t * FRAME_HZ) % 1.0) < 0.25
bursty = sonify(0.8, 0.3 * slot * np.exp(1j * symbols(1)))
dec = int(round(fs / 48_000.0))          # IQ samples per audio sample
audio_slot = slot[::dec][:bursty.size]
on = float(np.sqrt(np.mean(bursty[audio_slot] ** 2)))
off = float(np.sqrt(np.mean(bursty[~audio_slot] ** 2)))
ok = on > off * 3.0
print("%-34s on %.4f  off %.4f  %s"
      % ("beeping stops between bursts", on, off, "OK" if ok else "FAIL"))
if not ok:
    failures.append("burst gate")

# The point of the whole design: two different data streams sent with the same
# power pattern must sound the same. If they did not, the sound would carry
# something about the content, and this would no longer be audification.
other = sonify(0.8, 0.3 * slot * np.exp(1j * symbols(2)))
rms = float(np.sqrt(np.mean(bursty ** 2)))
leak = float(np.max(np.abs(bursty - other))) / max(rms, 1e-9)
ok = leak < 0.10
print("%-34s differs by %.1f%% of the audio  %s"
      % ("data does not reach the sound", 100.0 * leak, "OK" if ok else "FAIL"))
if not ok:
    failures.append("envelope leaks data")

# Block boundaries must not add their own rhythm.
whole = sonify(0.8, 0.3 * slot * np.exp(1j * symbols(1)))
dm = Demodulator(mode=MODE_ENVELOPE, sample_rate=fs)
dm.strength = 0.8
sig = 0.3 * slot * np.exp(1j * symbols(1))
pieces = np.concatenate([dm.process(sig[i:i + 65_536])
                         for i in range(0, sig.size, 65_536)])
m = min(whole.size, pieces.size)
err = float(np.max(np.abs(whole[:m] - pieces[:m])))
ok = err < 1e-6
print("%-34s max difference %.2e  %s"
      % ("envelope block continuity", err, "OK" if ok else "FAIL"))
if not ok:
    failures.append("envelope continuity")

# --- PCM conversion -------------------------------------------------------
pcm = to_pcm16(np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32))
vals = np.frombuffer(pcm, dtype="<i2")
ok = len(pcm) == 8 and vals[1] == 32767 and vals[2] == -32767
print("%-34s %s  %s" % ("PCM16 conversion", list(vals), "OK" if ok else "FAIL"))
if not ok:
    failures.append("pcm")

print("")
if failures:
    print("FAILED: %s" % ", ".join(failures))
    sys.exit(1)
print("ALL DEMOD TESTS PASSED")
