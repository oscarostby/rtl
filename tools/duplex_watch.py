"""Watch the TETRA uplink and downlink sub-bands separately for a while."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from rtlsdr_diag.core.detector import MODE_SPECS, TETRA_MAST, TETRA_MOBILE
from rtlsdr_diag.sdr import dsp
from rtlsdr_diag.sdr.device import RtlSdrSource

src = RtlSdrSource(0); src.open()
g = [x for x in src.valid_gains_db() if x <= 45.0]
src.set_gain(max(g) if g else 40.0)
seen = {TETRA_MAST: {}, TETRA_MOBILE: {}}
rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 6

for r in range(rounds):
    for key in (TETRA_MOBILE, TETRA_MAST):
        spec = MODE_SPECS[key]
        src.set_sample_rate(spec.sample_rate)
        step = spec.sample_rate * 0.75
        F, P = [], []
        n = int(np.ceil((spec.stop_hz - spec.start_hz) / step))
        for i in range(n):
            c = spec.start_hz + step * (i + 0.5)
            try: src.set_center_freq(c)
            except Exception: continue
            src.read(8192)
            p = dsp.psd_dbfs(src.read(65536), 4096, 16)
            f = dsp.freq_axis(c, spec.sample_rate, 4096)
            m = np.abs(f - c) <= step / 2
            F.append(f[m]); P.append(p[m])
        f = np.concatenate(F); p = np.concatenate(P)
        o = np.argsort(f); f, p = f[o], p[o]
        peaks = dsp.find_carriers(f, p, spec.snr_threshold_db,
                                  spec.min_bandwidth_hz, max_carriers=100,
                                  smoothing_hz=spec.smoothing_hz,
                                  gap_hz=spec.gap_hz)
        for c in peaks:
            if 15e3 <= c.bandwidth_hz <= 35e3:      # TETRA-shaped only
                k = round(c.freq_hz / 12500.0)
                prev = seen[key].get(k)
                seen[key][k] = (c.freq_hz, max(c.snr_db, prev[1] if prev else 0),
                                (prev[2] if prev else 0) + 1)
    print("round %d/%d" % (r + 1, rounds), flush=True)

src.close()
for key, title in ((TETRA_MAST, "MAST / downlink 390-400 MHz"),
                   (TETRA_MOBILE, "MOBILE / uplink 380-385 MHz")):
    print("")
    print("%s  -> %d TETRA-shaped carrier(s)" % (title, len(seen[key])))
    for k in sorted(seen[key]):
        fz, snr, hits = seen[key][k]
        print("   %9.4f MHz   best SNR %5.1f dB   seen in %d/%d rounds"
              % (fz / 1e6, snr, hits, rounds))
