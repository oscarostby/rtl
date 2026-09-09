"""Focused checks for audio-engine scheduling, playback and DAB safety."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from rtlsdr_diag.sdr import engine as engine_mod  # noqa: E402
from rtlsdr_diag.sdr.demod import MODE_WFM  # noqa: E402
from rtlsdr_diag.sdr.engine import AcqConfig, SdrEngine  # noqa: E402
from rtlsdr_diag.sdr.simulator import SimulatedSource  # noqa: E402
from rtlsdr_diag.ui.audio import AudioPlayer  # noqa: E402
from rtlsdr_diag.ui.tab_listen import ListenTab  # noqa: E402


failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    ok = bool(condition)
    print("%-38s %s%s" % (name, "OK" if ok else "FAIL",
                          "  " + detail if detail else ""))
    if not ok:
        failures.append(name)


app = QApplication.instance() or QApplication([])


# Reconfiguring a running engine must not seed another perpetual QTimer chain.
class FakeTimer:
    calls: list[tuple[int, object]] = []

    @staticmethod
    def singleShot(delay: int, callback) -> None:
        FakeTimer.calls.append((delay, callback))


source = SimulatedSource(seed=1)
source.realtime = False
source.open()
engine = SdrEngine()
engine._source = source
cfg = AcqConfig(center_hz=100.0e6, sample_rate=2.4e6, averages=1,
                audio=True, audio_mode=MODE_WFM, label="Listen")
real_timer = engine_mod.QTimer
engine_mod.QTimer = FakeTimer
try:
    engine.start(cfg)
    engine.start(AcqConfig(**{**cfg.__dict__, "center_hz": 101.1e6}))
finally:
    engine_mod.QTimer = real_timer
check("one queued acquisition step", len(FakeTimer.calls) == 1,
      "timer calls=%d" % len(FakeTimer.calls))

# A live retune invalidates discriminator history before the next IQ block.
engine._demod = object()
engine.update_settings({"center_hz": 99.3e6})
check("retune resets demodulator", engine._demod is None)
engine.stop()
source.close()


# QIODevice writes can legally be partial.  Count only accepted bytes and mark
# the block dropped instead of claiming the entire payload was played.
class FakeSink:
    def bytesFree(self) -> int:
        return 100


class PartialIo:
    def write(self, data: bytes) -> int:
        return 3


player = AudioPlayer()
player._sink = FakeSink()
player._io = PartialIo()
player.write(b"12345678")
stats = player.stats()
check("partial audio write accounting",
      stats["written_bytes"] == 3 and stats["dropped_blocks"] == 1,
      "written=%d dropped=%d" %
      (stats["written_bytes"], stats["dropped_blocks"]))


# Minimal host API used by ListenTab.  Keep engine_stop asynchronous from the
# tab's perspective, as it is in the real MainWindow/QThread connection.
class FakeHost:
    def __init__(self):
        self.running = False
        self.stops = 0
        self.updates: list[dict] = []
        self.starts: list[AcqConfig] = []
        self.resets = 0

    def is_running_here(self, label: str) -> bool:
        return self.running and label == "Listen"

    def engine_stop(self) -> None:
        self.stops += 1

    def engine_update(self, changes: dict) -> None:
        self.updates.append(dict(changes))

    def engine_start(self, config: AcqConfig) -> None:
        self.starts.append(config)

    def reset_audio(self) -> None:
        self.resets += 1


host = FakeHost()
tab = ListenTab(host)
host.running = True
dab_index = tab.dab_block.findData(174.928e6)
tab.dab_block.setCurrentIndex(dab_index)
check("DAB selection stops analog audio",
      host.stops == 1 and not host.updates,
      "stops=%d updates=%d" % (host.stops, len(host.updates)))
check("DAB selection explains limitation",
      "cannot play DAB+" in tab.status.text())

# It must also be impossible to press Listen afterwards and feed the DAB
# ensemble into the WFM discriminator as loud digital noise.
host.running = False
start_was_called = []
tab.player.start = lambda *args: start_was_called.append(True) or True
tab._toggle()
check("DAB listen start is blocked",
      not start_was_called and not host.starts,
      "player starts=%d engine starts=%d" %
      (len(start_was_called), len(host.starts)))

# Normal analog live tuning remains a lightweight engine update.
tab.freq.setValue(100.0)
host.running = True
host.updates.clear()
tab.freq.setValue(100.1)
check("analog live retune still works",
      len(host.updates) == 1 and host.updates[0]["center_hz"] == 100.1e6)

tab.deleteLater()
app.processEvents()

print("")
if failures:
    print("FAILED: %s" % ", ".join(failures))
    sys.exit(1)
print("ALL AUDIO PATH TESTS PASSED")
