"""Tests for the shared core: store, states, CSV, matching, geometry, smoothing."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rtlsdr_diag.core.acquisition import (AcquisitionState,  # noqa: E402
                                          AcquisitionStatus, state_for)
from rtlsdr_diag.core.classify import classify  # noqa: E402
from rtlsdr_diag.core.geo import (LatLon, angle_between, bearing_deg,  # noqa: E402
                                  destination, distance_km, triangulate)
from rtlsdr_diag.core.models import (CLASS_FM, CLASS_TETRA,  # noqa: E402
                                     CLASS_UNKNOWN, BearingMeasurement,
                                     CoverageMeasurement, ValidationError,
                                     validate_latitude, validate_longitude)
from rtlsdr_diag.core.signal_store import SignalStore  # noqa: E402
from rtlsdr_diag.core.sites import CoverageLog, Site, SiteStore  # noqa: E402
from rtlsdr_diag.core.smoothing import (FAST, MEDIUM, SLOW,  # noqa: E402
                                        LevelSmoother)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print("%-52s %s%s" % (name, "OK" if condition else "FAIL",
                          ("  " + detail) if detail else ""))
    if not condition:
        failures.append(name)


# ===========================================================================
print("--- SignalStore ---")
store = SignalStore()
store.begin_pass()
det, is_new = store.add_or_update_detection(107.7e6, -42.0, -73.0, 31.0, 180e3,
                                            source="Scanner")
check("first detection is new", is_new)
check("classified from frequency and bandwidth", det.signal_class == CLASS_FM,
      det.signal_class)
check("detection carries no position",
      not hasattr(det, "lat") and not hasattr(det, "bearing_deg"))
check("frequency_mhz view", abs(det.frequency_mhz - 107.7) < 1e-9)

# A second sighting close by must merge, not duplicate.
store.begin_pass()
det2, is_new2 = store.add_or_update_detection(107.7005e6, -40.0, -73.0, 33.0,
                                              180e3, source="Scanner")
check("nearby sighting merges", (not is_new2) and len(store) == 1,
      "%d item(s)" % len(store))
check("hits accumulate", det2.hits == 2, str(det2.hits))
check("frequency follows the strongest sighting",
      abs(det2.freq_hz - 107.7005e6) < 1.0)

# A clearly different frequency must be a separate signal.
store.begin_pass()
store.add_or_update_detection(391.4125e6, -40.0, -74.0, 34.0, 25e3,
                              source="TETRA RF Check")
check("distant frequency is separate", len(store) == 2, "%d" % len(store))
check("TETRA band classified", store.items()[1].signal_class == CLASS_TETRA,
      store.items()[1].signal_class)

# Repeated sightings inside one sweep pass are ONE observation.
dup = SignalStore()
dup.begin_pass()
d0, _ = dup.add_or_update_detection(390.0125e6, -50.0, -80.0, 30.0, 14e3)
same, new2 = dup.add_or_update_detection(390.0130e6, -60.0, -80.0, 20.0, 10e3)
check("weaker same-pass duplicate adds no hit",
      same is d0 and not new2 and d0.hits == 1, "hits=%d" % d0.hits)
check("weaker same-pass duplicate cannot lower the level",
      d0.level_dbfs == -50.0 and d0.seen_passes[1] == -50.0,
      "%.1f" % d0.level_dbfs)
dup.add_or_update_detection(390.01275e6, -42.0, -79.0, 37.0, 12e3)
check("stronger same-pass duplicate wins without a hit",
      d0.hits == 1 and d0.level_dbfs == -42.0 and d0.seen_passes[1] == -42.0,
      "hits=%d level=%.1f" % (d0.hits, d0.level_dbfs))
dup.begin_pass()
dup.add_or_update_detection(390.0126e6, -45.0, -80.0, 35.0, 13e3)
check("a new pass does add a hit", d0.hits == 2, "hits=%d" % d0.hits)

# Subscribers
seen: list = []
store.subscribe(lambda items: seen.append(len(items)))
store.notify()
check("subscriber receives detections", seen == [2], str(seen))


def _boom(items):
    raise RuntimeError("listener blew up")


store.subscribe(_boom)
store.notify()
check("a broken subscriber cannot break notify", len(seen) == 2)
store.unsubscribe(_boom)

# Confirmation threshold
check("confirmed needs two hits", len(store.confirmed_items(2)) == 1,
      "%d confirmed" % len(store.confirmed_items(2)))

# Filtering helpers
check("by_class filters", len(store.by_class(CLASS_TETRA)) == 1)
check("classes_present lists both", set(store.classes_present()) ==
      {CLASS_FM, CLASS_TETRA})
check("strongest picks best SNR",
      abs(store.strongest().freq_hz - 391.4125e6) < 1.0)

# ===========================================================================
print("")
print("--- stale expiry ---")
stale_store = SignalStore()
stale_store.begin_pass()
d1, _ = stale_store.add_or_update_detection(100e6, -50.0, -80.0, 30.0, 180e3)
d2, _ = stale_store.add_or_update_detection(145e6, -60.0, -80.0, 20.0, 12.5e3)
d1.last_seen = time.time() - 600.0
check("stale detection is not active", not d1.is_active(6.0))
check("fresh detection is active", d2.is_active(6.0))
check("active list excludes stale",
      [d.freq_hz for d in stale_store.get_active_detections()] == [145e6])
removed = stale_store.clear_stale_detections(300.0)
check("clear_stale_detections removes old rows",
      removed == 1 and len(stale_store) == 1, "removed %d" % removed)
check("clear_stale keeps recent rows", stale_store.items()[0].freq_hz == 145e6)

# Occupancy over passes
occ = SignalStore()
for p in range(1, 11):
    occ.begin_pass()
    if p in (1, 2, 3, 7, 8, 9):
        occ.add_or_update_detection(390.0125e6, -40.0, -80.0, 40.0, 25e3)
item = occ.items()[0]
passes = occ.recent_passes()
check("occupancy over passes", abs(item.occupancy(passes) - 0.6) < 1e-9,
      "%.2f" % item.occupancy(passes))
check("burst counting", item.bursts(passes) == 2, str(item.bursts(passes)))

# ===========================================================================
print("")
print("--- acquisition state transitions ---")
check("no device -> DISCONNECTED",
      state_for(False, False) is AcquisitionState.DISCONNECTED)
check("device, not running -> CONNECTED_IDLE",
      state_for(True, False) is AcquisitionState.CONNECTED_IDLE)
check("sweeping -> SCANNING",
      state_for(True, True, "sweep") is AcquisitionState.SCANNING)
check("single frequency -> TUNED",
      state_for(True, True, "spectrum") is AcquisitionState.TUNED)
check("error wins over everything",
      state_for(True, True, "sweep", "device lost") is AcquisitionState.ERROR)

st = AcquisitionStatus(state=AcquisitionState.SCANNING, owner="Scanner",
                       start_hz=390e6, stop_hz=395e6, sample_rate=2.048e6)
check("scanning headline names the range",
      st.headline() == "SDR SCANNING 390.000-395.000 MHz", st.headline())
check("owner is reported", st.owner_text() == "LIVE DATA FROM: Scanner")
check("scanning counts as running", st.is_running)
st2 = AcquisitionStatus(state=AcquisitionState.TUNED, center_hz=391.4125e6)
check("tuned headline names the frequency",
      st2.headline() == "SDR TUNED 391.4125 MHz", st2.headline())
st3 = AcquisitionStatus(state=AcquisitionState.CONNECTED_IDLE)
check("idle headline", st3.headline() == "SDR CONNECTED - IDLE", st3.headline())
check("idle is not running", not st3.is_running)
st4 = AcquisitionStatus(state=AcquisitionState.ERROR, detail="DEVICE LOST")
check("error headline", "DEVICE LOST" in st4.headline(), st4.headline())
check("error colour is bad", st4.colour_key() == "bad")
st5 = AcquisitionStatus(state=AcquisitionState.SCANNING, simulated=True)
check("simulation is flagged in the headline",
      st5.headline().startswith("SIM"), st5.headline())

# ===========================================================================
print("")
print("--- coordinate validation ---")
for bad in (91.0, -90.5, float("nan")):
    try:
        validate_latitude(bad)
        check("latitude %r rejected" % bad, False)
    except ValidationError:
        check("latitude %r rejected" % bad, True)
for bad in (181.0, -180.5):
    try:
        validate_longitude(bad)
        check("longitude %r rejected" % bad, False)
    except ValidationError:
        check("longitude %r rejected" % bad, True)
check("valid latitude accepted", validate_latitude(59.9139) == 59.9139)

try:
    BearingMeasurement(observer_lat=59.9, observer_lon=10.7, bearing_deg=400.0,
                       freq_hz=100e6).validate()
    check("bearing over 360 rejected", False)
except ValidationError:
    check("bearing over 360 rejected", True)
BearingMeasurement(observer_lat=59.9, observer_lon=10.7, bearing_deg=359.9,
                   freq_hz=100e6).validate()
check("bearing 359.9 accepted", True)
CoverageMeasurement(observer_lat=59.9, observer_lon=10.7, freq_hz=100e6,
                    level_dbfs=-50.0).validate()
check("coverage measurement validates", True)

# ===========================================================================
print("")
print("--- known transmitter CSV ---")
tmp = Path(tempfile.mkdtemp())
csv_path = tmp / "sites.csv"
csv_path.write_text(
    "name,kind,frequency_mhz,latitude,longitude,height_m,power_kw,notes\n"
    "Good Site,DAB,222.064,59.98,10.66,300,10,fine\n"
    "Bad Lat,FM,99.3,95.0,10.0,100,5,broken\n"
    "Bad Lon,FM,99.3,59.0,200.0,100,5,broken\n"
    "Zero Freq,FM,0,59.0,10.0,100,5,no frequency\n"
    ",FM,99.3,59.0,10.0,100,5,no name\n"
    "# commented,FM,99.3,59.0,10.0,100,5,ignored\n",
    encoding="utf-8")
site_store = SiteStore()
count, warnings = site_store.load_sites(csv_path)
check("only the valid row loads", count == 1, "%d loaded" % count)
check("four bad rows warned about", len(warnings) == 4, str(warnings))
check("warning names the bad latitude",
      any("latitude" in w for w in warnings), str(warnings))
check("commented row silently skipped",
      not any("commented" in w for w in warnings))
check("zero frequency rejected",
      any("frequency" in w for w in warnings), str(warnings))
check("nameless row is reported, not dropped",
      any("no name" in w for w in warnings), str(warnings))

roundtrip = tmp / "out.csv"
site_store.save_sites(roundtrip)
again = SiteStore()
n2, w2 = again.load_sites(roundtrip)
check("CSV round trip", n2 == 1 and not w2)
check("round trip keeps the frequency",
      abs(again.sites[0].freq_hz - 222.064e6) < 1.0)

bad_header = tmp / "bad.csv"
bad_header.write_text("foo,bar\n1,2\n", encoding="utf-8")
n3, w3 = again.load_sites(bad_header)
check("missing columns reported clearly",
      n3 == 0 and "Missing required column" in w3[0], str(w3))

# ===========================================================================
print("")
print("--- frequency matching tolerance ---")
site = Site("Beacon", "Test", 433.920e6, 59.9, 10.7)
check("exact match", site.matches_frequency(433.920e6, 25e3))
check("inside tolerance", site.matches_frequency(433.930e6, 25e3))
check("outside tolerance", not site.matches_frequency(433.960e6, 25e3))
check("tolerance is configurable", site.matches_frequency(433.960e6, 50e3))
check("zero frequency never matches",
      not Site("x", "Test", 0.0, 59.0, 10.0).matches_frequency(100e6, 25e3))

# ===========================================================================
print("")
print("--- bearing geometry ---")
HERE = LatLon(59.9139, 10.7522)
TARGET = destination(HERE, 35.0, 12.0)
OBS2 = destination(HERE, 100.0, 6.0)
fix, reason = triangulate(HERE, bearing_deg(HERE, TARGET),
                          OBS2, bearing_deg(OBS2, TARGET))
check("good geometry produces a fix", fix is not None, reason)
if fix is not None:
    check("fix lands on the target",
          distance_km(fix.point, TARGET) < 0.05,
          "%.3f km off" % distance_km(fix.point, TARGET))
    check("cut angle reported", fix.cut_angle_deg > 15.0,
          "%.1f deg" % fix.cut_angle_deg)
    check("quality graded", fix.quality in ("good", "fair", "poor"), fix.quality)

par_fix, par_reason = triangulate(HERE, 10.0, OBS2, 10.0)
check("parallel bearings rejected", par_fix is None, par_reason[:40])
near_par, near_reason = triangulate(HERE, 10.0, OBS2, 18.0)
check("nearly parallel bearings rejected", near_par is None, near_reason[:40])
rev_fix, _ = triangulate(HERE, bearing_deg(HERE, TARGET),
                         OBS2, (bearing_deg(OBS2, TARGET) + 180.0) % 360.0)
check("reversed bearing rejected", rev_fix is None)
same_fix, same_reason = triangulate(HERE, 10.0, HERE, 80.0)
check("same observation point rejected", same_fix is None, same_reason[:40])
check("angle_between wraps", abs(angle_between(350.0, 10.0) - 20.0) < 1e-9)

# ===========================================================================
print("")
print("--- level smoothing ---")
fast, slow = LevelSmoother(FAST), LevelSmoother(SLOW)
t = 0.0
for _ in range(40):
    t += 0.1
    fast.update(-40.0, t)
    slow.update(-40.0, t)
check("settles on a steady input", abs(fast.value + 40.0) < 0.5,
      "%.2f" % fast.value)
tf = ts = t
for _ in range(5):
    tf += 0.1
    ts += 0.1
    fast.update(-20.0, tf)
    slow.update(-20.0, ts)
check("FAST reacts quicker than SLOW", fast.value > slow.value + 5.0,
      "fast %.1f vs slow %.1f" % (fast.value, slow.value))
check("min tracked", abs(fast.minimum + 40.0) < 1e-9)
check("max tracked", abs(fast.maximum + 20.0) < 1e-9)
check("average is between the two extremes",
      -40.0 < fast.average < -20.0, "%.1f" % fast.average)
med = LevelSmoother(MEDIUM)
check("NaN input is ignored", med.update(float("nan")) != med.update(float("nan"))
      or med.count == 0)
med.reset()
check("reset clears the count", med.count == 0)

# ===========================================================================
print("")
print("--- gain consistency ---")
cov = CoverageLog()
for lat, gain in ((59.90, "40.2"), (59.91, "40.2")):
    from rtlsdr_diag.core.sites import CoveragePoint
    cov.add(CoveragePoint(lat=lat, lon=10.7, freq_hz=100e6, level_dbfs=-50.0,
                          notes="gain=%s" % gain))
gains = {p.notes.split("=", 1)[1] for p in cov.points}
check("same gain recorded for both points", len(gains) == 1)
cov.add(CoveragePoint(lat=59.92, lon=10.7, freq_hz=100e6, level_dbfs=-50.0,
                      notes="gain=auto"))
gains = {p.notes.split("=", 1)[1] for p in cov.points}
check("mixed gains are detectable", len(gains) == 2, str(sorted(gains)))

cov_path = tmp / "coverage.csv"
cov.save(cov_path)
cov2 = CoverageLog()
n4, w4 = cov2.load(cov_path)
check("coverage CSV round trip", n4 == 3 and not w4, "%d rows" % n4)

# ===========================================================================
print("")
print("--- classification ---")
for freq, bw, expected in (
        (107.7e6, 180e3, CLASS_FM),
        (391.4125e6, 25e3, CLASS_TETRA),
        (700.0e6, 20e3, CLASS_UNKNOWN),
        (107.7e6, 25e3, CLASS_UNKNOWN),          # too narrow for wide FM
):
    got, conf = classify(freq, bw)
    check("classify %.3f MHz / %.0f kHz -> %s" % (freq / 1e6, bw / 1e3, expected),
          got == expected, "%s (%s)" % (got, conf))
check("no bandwidth gives medium confidence at best",
      classify(107.7e6, 0.0)[1] == "MEDIUM")

print("")
if failures:
    print("FAILED (%d): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("ALL CORE TESTS PASSED")
