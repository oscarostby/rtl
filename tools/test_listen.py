"""Listening: parking on a signal, playing it, and giving the receiver back."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import faulthandler
# Watchdog: this suite starts and stops acquisition several times, and a
# deadlock there would otherwise hang without saying where.
faulthandler.dump_traceback_later(120, exit=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rtlsdr_diag.core.detector import (DAB, FM, MODES,  # noqa: E402
                                       MODE_SPECS, TETRA_MOBILE)
from rtlsdr_diag.sdr.engine import MODE_SPECTRUM, MODE_SWEEP  # noqa: E402
from rtlsdr_diag.ui.app_core import AppCore  # noqa: E402
from rtlsdr_diag.ui.detector_window import DetectorWindow  # noqa: E402
from rtlsdr_diag.ui.speech import Announcer, spell_frequency  # noqa: E402

failures: list[str] = []


def check(name, ok, detail=""):
    print("%-58s %s%s" % (name, "OK" if ok else "FAIL",
                          ("  " + detail) if detail else ""), flush=True)
    if not ok:
        failures.append(name)


app = QApplication(sys.argv)
core = AppCore(simulate=True)
win = DetectorWindow(core)
win.resize(900, 600)
win.show()

pcm_blocks = []
core.engine.audio_ready.connect(lambda pcm, rate: pcm_blocks.append((len(pcm), rate)))
core.start()

STEPS = []


def step(fn, name, gap=900):
    STEPS.append((fn, name, gap))


def s_band_audio():
    check("FM is played as real demodulated audio",
          MODE_SPECS[FM].audio_mode == "WFM")
    check("both TETRA bands use envelope audification",
          MODE_SPECS["TETRA_MAST"].audio_mode == "ENV"
          and MODE_SPECS[TETRA_MOBILE].audio_mode == "ENV")
    check("DAB offers no audio and says why",
          not MODE_SPECS[DAB].audio_mode
          and "decoder" in MODE_SPECS[DAB].audio_note)


def s_gain():
    # A wanted gain has to land on a value the tuner actually offers.
    table = [0.0, 16.6, 25.4, 32.8, 40.2, 44.5, 49.6]
    saved = core._device_info
    core._device_info = {"gains_db": table}
    check("\"max\" picks the tuner's highest", core.gain_for(FM) == 49.6,
          str(core.gain_for(FM)))
    check("a number snaps to the nearest available",
          core.gain_for(DAB) == 40.2, str(core.gain_for(DAB)))
    check("\"auto\" is left to the tuner",
          core.gain_for("TETRA_MAST") == "auto", str(core.gain_for("TETRA_MAST")))
    core.gain_override = 28.0
    check("the settings panel can override every band",
          core.gain_for(FM) == 28.0 and core.gain_for(DAB) == 28.0)
    core.gain_override = None
    core._device_info = saved


def s_speech():
    check("a frequency is spelled for a voice",
          spell_frequency(391.4125e6) == "391 point 4",
          spell_frequency(391.4125e6))
    check("rounding does not produce 'point 10'",
          spell_frequency(381.9875e6) == "382 point 0",
          spell_frequency(381.9875e6))
    an = Announcer()
    if not an.is_available:
        check("speech is optional and degrades quietly",
              an.say("k", "hello") is False, an.last_error)
        return
    check("it says a thing", an.say("k", "hello", 5.0, now=1000.0))
    check("it does not repeat itself straight away",
          not an.say("k", "hello", 5.0, now=1001.0))
    check("but says it again later", an.say("k", "hello", 5.0, now=1010.0))
    check("a different kind is not blocked",
          an.say("other", "hello", 5.0, now=1001.0))
    an.enabled = False
    check("and it can be switched off",
          not an.say("z", "hello", 0.0, now=2000.0))
    an.stop()


def s_tone():
    # The beeping must be driven by the same number the meter is drawn from,
    # or what you hear and what you see disagree.
    state = win.states[FM]
    core.listening_band = FM
    win._tone_strength = -1.0
    sent = []
    real = core.engine_update
    core.engine_update = lambda ch: sent.append(ch)
    try:
        win._drive_tone()
    finally:
        core.engine_update = real
    core.listening_band = ""
    check("FM is not sonified - it has real audio", not sent, str(sent))

    mast = win.states["TETRA_MAST"]
    core.listening_band = "TETRA_MAST"
    mast.listen_noise_dbfs = -80.0
    for _ in range(40):
        mast.push_listen_level(-45.0)     # a solid 35 dB in the channel
    win._tone_strength = -1.0
    sent = []
    core.engine_update = lambda ch: sent.append(ch)
    try:
        win._drive_tone()
    finally:
        core.engine_update = real
    core.listening_band = ""
    ok = bool(sent) and "tone_strength" in sent[0]
    check("TETRA drives the beeping from the meter", ok, str(sent[:1]))
    if ok:
        check("and by exactly the meter's own value",
              abs(sent[0]["tone_strength"] - mast.strength_fraction()) < 1e-9)


def s_start():
    check("a signal was found to listen to",
          win.states[FM].current is not None)
    pcm_blocks.clear()
    win._toggle_listen()


def s_listening():
    check("listening started", core.is_listening(FM), core.listening_band)
    check("the page knows it is listening", win.states[FM].listening)
    check("the receiver parked instead of sweeping",
          core.engine._cfg.mode == MODE_SPECTRUM, core.engine._cfg.mode)
    check("it tuned off-centre to dodge the DC spike",
          abs(core.engine._cfg.center_hz - core.listen_freq_hz) > 1e5
          and core.engine._cfg.audio_offset_hz > 0,
          "%.4f MHz + %.0f kHz" % (core.engine._cfg.center_hz / 1e6,
                                   core.engine._cfg.audio_offset_hz / 1e3))
    check("audio is reaching the player", len(pcm_blocks) > 0,
          "%d blocks" % len(pcm_blocks))
    rates = sorted({r for _, r in pcm_blocks})
    check("audio is 48 kHz PCM", rates == [48_000], "rates seen: %s" % rates)
    # A sweep finishing just after listening started must not retune the
    # receiver, or the audio changes rate mid-stream.
    check("the parked sample rate is not disturbed",
          abs(core.engine._cfg.sample_rate - 2.4e6) < 1.0,
          "%.0f S/s" % core.engine._cfg.sample_rate)
    check("and the band rotation stayed put",
          core.engine._cfg.mode == MODE_SPECTRUM)


def s_still_showing():
    st = win.states[FM]
    check("the parked page keeps showing what it tuned to",
          st.current is not None)
    check("the meter is driven by the audio channel",
          st.listen_snr_db > 0.0, "SNR %.1f dB" % st.listen_snr_db)
    check("the displayed SNR is the live one",
          abs(st.shown_snr_db() - st.listen_snr_db) < 1e-6)


def s_stop():
    win._toggle_listen()


def s_stopped():
    check("listening stopped", not core.is_listening())
    check("the page cleared its listening flag", not win.states[FM].listening)


def s_resumed():
    check("the sweep took the receiver back",
          core.engine._cfg.mode == MODE_SWEEP and core.acquisition_status().is_running,
          core.engine._cfg.mode)


def s_dab_refused():
    win.stack.go_to(MODES.index(DAB))
    win._page_changed(MODES.index(DAB))
    check("DAB cannot be listened to", not win.listen_btn.isEnabled())
    problem = core.start_listening(DAB, 222.064e6)
    check("asking anyway is refused with a reason", bool(problem), problem[:44])
    check("and nothing started listening", not core.is_listening())


def s_swipe_away():
    win.stack.go_to(0)
    win._page_changed(0)
    if win.states[FM].current is not None:
        win._toggle_listen()


def s_swipe_stops():
    was = core.is_listening(FM)
    win.stack.go_to(1)
    win._page_changed(1)
    check("swiping to another band gives the receiver back",
          was and not core.is_listening(), "was listening: %s" % was)


step(s_band_audio, "band audio", 300)
step(s_gain, "gain", 300)
step(s_speech, "speech", 300)
step(s_tone, "tone", 300)
step(s_start, "start", 2500)
step(s_listening, "listening", 600)
step(s_still_showing, "still showing", 300)
step(s_stop, "stop", 900)
step(s_stopped, "stopped", 900)
step(s_resumed, "resumed", 400)
step(s_dab_refused, "dab refused", 600)
step(s_swipe_away, "swipe away", 2000)
step(s_swipe_stops, "swipe stops", 400)


def run(i=0):
    if i >= len(STEPS):
        print("", flush=True)
        if failures:
            print("LISTEN TESTS FAILED (%d):" % len(failures), flush=True)
            for f in failures:
                print("   - %s" % f, flush=True)
        else:
            print("ALL LISTEN TESTS PASSED", flush=True)
        win.close()          # closeEvent shuts the core down
        core.shutdown()      # and calling it again must be harmless
        faulthandler.cancel_dump_traceback_later()
        raise SystemExit(1 if failures else 0)
    fn, name, gap = STEPS[i]
    try:
        fn()
    except Exception as exc:
        import traceback
        failures.append("%s raised %s" % (name, exc))
        traceback.print_exc()
    QTimer.singleShot(gap, lambda: run(i + 1))


QTimer.singleShot(3000, lambda: run(0))
sys.exit(app.exec())
