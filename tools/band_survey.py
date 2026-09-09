"""Sweep the bands this dongle can actually reach and list what is out there."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from rtlsdr_diag.sdr import dsp
from rtlsdr_diag.sdr.device import RtlSdrSource

BANDS = [("FM broadcast", 87.5, 108.0), ("Airband", 118.0, 137.0),
         ("2 m amateur", 144.0, 146.0), ("DAB Band III", 174.0, 240.0),
         ("TETRA uplink", 380.0, 385.0), ("TETRA downlink", 390.0, 395.0),
         ("70 cm / ISM", 430.0, 440.0), ("ISM 868", 866.0, 870.0)]
FS = 2.048e6
src = RtlSdrSource(0)
src.open()
src.set_sample_rate(FS)
gains = [g for g in src.valid_gains_db() if g <= 45.0]
src.set_gain(max(gains) if gains else 40.0)

print("%-16s %-9s %-9s %s" % ("band", "noise", "peak", "strongest carriers"))
for name, lo, hi in BANDS:
    step = FS * 0.75
    F, P = [], []
    n = max(1, int(np.ceil((hi - lo) * 1e6 / step)))
    for i in range(n):
        c = lo * 1e6 + step * (i + 0.5)
        try:
            src.set_center_freq(c)
        except Exception:
            continue
        src.read(8192)
        p = dsp.psd_dbfs(src.read(65536), 4096, 16)
        f = dsp.freq_axis(c, FS, 4096)
        m = np.abs(f - c) <= step / 2
        F.append(f[m]); P.append(p[m])
    if not F:
        continue
    f = np.concatenate(F); p = np.concatenate(P)
    o = np.argsort(f); f, p = f[o], p[o]
    nf = dsp.noise_floor_db(p)
    peaks = dsp.find_carriers(f, p, 10.0, 6000.0, max_carriers=200)
    top = ", ".join("%.3f MHz %.0f dB" % (c.freq_hz / 1e6, c.snr_db)
                    for c in peaks[:3]) or "nothing above +10 dB"
    print("%-16s %-9.1f %-9.1f %s" % (name, nf, p.max(), top))
src.close()
