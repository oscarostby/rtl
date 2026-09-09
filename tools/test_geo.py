"""Checks for the geodesy helpers, against known reference values."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rtlsdr_diag.core.geo import (LatLon, angle_between, bearing_deg,  # noqa: E402
                                  destination, distance_km, intersection,
                                  radio_horizon_km, triangulate)

failures = []


def check(name, got, want, tol):
    ok = abs(got - want) <= tol
    print("%-46s got %10.4f  want %10.4f  %s"
          % (name, got, want, "OK" if ok else "FAIL"))
    if not ok:
        failures.append(name)


OSLO = LatLon(59.9139, 10.7522)
BERGEN = LatLon(60.3913, 5.3221)
TRONDHEIM = LatLon(63.4305, 10.3951)

# Oslo <-> Bergen is about 306 km great-circle.
check("distance Oslo-Bergen (km)", distance_km(OSLO, BERGEN), 306.0, 4.0)
check("distance is symmetric", distance_km(BERGEN, OSLO), distance_km(OSLO, BERGEN), 1e-9)
check("distance to self", distance_km(OSLO, OSLO), 0.0, 1e-9)

# Bergen is roughly west-north-west of Oslo; Trondheim is nearly due north.
# Bergen lies ~302 km west and ~53 km north of Oslo -> just north of due west.
check("bearing Oslo->Bergen (deg)", bearing_deg(OSLO, BERGEN), 282.4, 1.0)
check("bearing Oslo->Trondheim (deg)", bearing_deg(OSLO, TRONDHEIM), 358.0, 3.0)

# Round trip: project out and measure back.
for brg in (0.0, 45.0, 123.4, 270.0, 359.0):
    for dist in (1.0, 25.0, 300.0):
        p = destination(OSLO, brg, dist)
        check("roundtrip dist b=%.0f d=%.0f" % (brg, dist),
              distance_km(OSLO, p), dist, 0.02)
        check("roundtrip brg  b=%.0f d=%.0f" % (brg, dist),
              bearing_deg(OSLO, p), brg, 0.05)

# Triangulation: put a target somewhere, take true bearings from two places,
# and confirm the crossing lands back on the target.
TARGET = destination(OSLO, 35.0, 40.0)
OBS2 = destination(OSLO, 90.0, 30.0)
fix = intersection(OSLO, bearing_deg(OSLO, TARGET), OBS2, bearing_deg(OBS2, TARGET))
if fix is None:
    print("%-46s FAIL (no intersection)" % "triangulation")
    failures.append("triangulation")
else:
    check("triangulated fix error (km)", distance_km(fix, TARGET), 0.0, 0.05)

# On a sphere two north-bound great circles really do meet - at the pole - so
# a raw crossing is not enough. triangulate() must reject that as unusable.
par_fix, reason = triangulate(OSLO, 0.0, OBS2, 0.0)
print("%-46s %s (%s)" % ("parallel bearings rejected",
                         "OK" if par_fix is None else "FAIL", reason[:40]))
if par_fix is not None:
    failures.append("parallel bearings")

# A good cut angle must be accepted and land on the target.
good_fix, reason = triangulate(OSLO, bearing_deg(OSLO, TARGET),
                               OBS2, bearing_deg(OBS2, TARGET))
if good_fix is None:
    print("%-46s FAIL (%s)" % ("good cut accepted", reason))
    failures.append("good cut")
else:
    check("triangulate() fix error (km)", distance_km(good_fix.point, TARGET), 0.0, 0.05)
    print("%-46s cut %.1f deg -> %s" % ("triangulate() quality",
                                        good_fix.cut_angle_deg, good_fix.quality))

# A bearing pointing away from the target must not yield a fix.
away_fix, reason = triangulate(OSLO, bearing_deg(OSLO, TARGET),
                               OBS2, (bearing_deg(OBS2, TARGET) + 180.0) % 360.0)
print("%-46s %s" % ("reversed bearing rejected",
                    "OK" if away_fix is None else "FAIL"))
if away_fix is not None:
    failures.append("reversed bearing")

check("angle_between wraps 350 vs 10", angle_between(350.0, 10.0), 20.0, 1e-9)
check("angle_between 0 vs 180", angle_between(0.0, 180.0), 180.0, 1e-9)

# Same point twice is degenerate.
same = intersection(OSLO, 10.0, OSLO, 20.0)
print("%-46s %s" % ("identical observation points give no fix",
                    "OK" if same is None else "FAIL"))
if same is not None:
    failures.append("identical points")

# A 300 m mast seen from 2 m: roughly 74 km of radio horizon.
check("radio horizon 300 m mast (km)", radio_horizon_km(300.0, 2.0), 77.2, 2.0)

print("")
if failures:
    print("FAILED: %s" % ", ".join(failures))
    sys.exit(1)
print("ALL GEO TESTS PASSED")
