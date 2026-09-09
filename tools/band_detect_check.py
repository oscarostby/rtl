"""Sweep two bands with their own detector settings and report what is found."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from rtlsdr_diag.config import PRESET_BY_NAME
from rtlsdr_diag.core.classify import classify
from rtlsdr_diag.sdr import dsp
from rtlsdr_diag.sdr.device import RtlSdrSource

src = RtlSdrSource(0)
src.open()
gains = [g for g in src.valid_gains_db() if g <= 45.0]
src.set_gain(max(gains) if gains else 40.0)

for name in ("FM broadcast", "TETRA / Nodnett RF band 380-400 MHz"):
    preset = PRESET_BY_NAME[name]
    fs = preset.sample_rate
    src.set_sample_rate(fs)
    step = fs * 0.75
    F, P = [], []
    n = int(np.ceil((preset.stop_hz - preset.start_hz) / step))
    for i in range(n):
        c = preset.start_hz + step * (i + 0.5)
        try:
            src.set_center_freq(c)
        except Exception:
            continue
        src.read(8192)
        p = dsp.psd_dbfs(src.read(131072), 4096, 32)
        f = dsp.freq_axis(c, fs, 4096)
        m = np.abs(f - c) <= step / 2
        F.append(f[m])
        P.append(p[m])
    f = np.concatenate(F)
    p = np.concatenate(P)
    o = np.argsort(f)
    f, p = f[o], p[o]

    peaks = dsp.find_carriers(
        f, p, preset.snr_threshold_db, preset.min_bandwidth_hz,
        max_carriers=200, smoothing_hz=preset.smoothing_hz,
        gap_hz=preset.gap_hz)
    classes = {}
    for c in peaks:
        k = classify(c.freq_hz, c.bandwidth_hz)[0]
        classes[k] = classes.get(k, 0) + 1
    print("")
    print("%s" % name)
    print("   noise floor %.1f dBFS   detections %d   %s"
          % (dsp.noise_floor_db(p), len(peaks), classes))
    for c in sorted(peaks, key=lambda x: x.level_dbfs, reverse=True)[:8]:
        cls, conf = classify(c.freq_hz, c.bandwidth_hz)
        print("   %9.4f MHz  %6.1f dBFS  snr %5.1f  bw %7.1f kHz  %s (%s)"
              % (c.freq_hz / 1e6, c.level_dbfs, c.snr_db,
                 c.bandwidth_hz / 1e3, cls, conf))

src.close()
