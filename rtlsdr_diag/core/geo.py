"""Geodesy helpers: distance, bearing, projection and bearing-line crossing.

Spherical-earth formulas. At the ranges this tool deals with (tens to a few
hundred kilometres) the error against a full ellipsoidal model is well under a
percent, which is far smaller than the uncertainty in any bearing a person
takes by hand.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float

    def is_valid(self) -> bool:
        return (-90.0 <= self.lat <= 90.0) and (-180.0 <= self.lon <= 180.0)

    def __str__(self) -> str:
        ns = "N" if self.lat >= 0 else "S"
        ew = "E" if self.lon >= 0 else "W"
        return "%.5f%s %.5f%s" % (abs(self.lat), ns, abs(self.lon), ew)


def distance_km(a: LatLon, b: LatLon) -> float:
    """Great-circle distance in kilometres (haversine)."""
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dphi = phi2 - phi1
    dlam = math.radians(b.lon - a.lon)
    h = (math.sin(dphi / 2.0) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2)
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: LatLon, b: LatLon) -> float:
    """Initial true bearing from a to b, degrees clockwise from north."""
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dlam = math.radians(b.lon - a.lon)
    y = math.sin(dlam) * math.cos(phi2)
    x = (math.cos(phi1) * math.sin(phi2)
         - math.sin(phi1) * math.cos(phi2) * math.cos(dlam))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination(origin: LatLon, bearing: float, distance: float) -> LatLon:
    """Point reached from origin on `bearing` degrees after `distance` km."""
    ang = distance / EARTH_RADIUS_KM
    brg = math.radians(bearing)
    phi1, lam1 = math.radians(origin.lat), math.radians(origin.lon)
    phi2 = math.asin(math.sin(phi1) * math.cos(ang)
                     + math.cos(phi1) * math.sin(ang) * math.cos(brg))
    lam2 = lam1 + math.atan2(math.sin(brg) * math.sin(ang) * math.cos(phi1),
                             math.cos(ang) - math.sin(phi1) * math.sin(phi2))
    return LatLon(math.degrees(phi2), (math.degrees(lam2) + 540.0) % 360.0 - 180.0)


def intersection(p1: LatLon, brg1: float, p2: LatLon, brg2: float) -> LatLon | None:
    """Where two bearing lines cross, or None if they do not.

    This is the triangulation step: two bearings taken from two different
    places pin the transmitter to their crossing point.
    """
    phi1, lam1 = math.radians(p1.lat), math.radians(p1.lon)
    phi2, lam2 = math.radians(p2.lat), math.radians(p2.lon)
    th13, th23 = math.radians(brg1), math.radians(brg2)
    dphi, dlam = phi2 - phi1, lam2 - lam1

    delta12 = 2.0 * math.asin(min(1.0, math.sqrt(
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2)))
    if delta12 < 1e-12:
        return None                       # the two observation points coincide

    cos_tha = ((math.sin(phi2) - math.sin(phi1) * math.cos(delta12))
               / (math.sin(delta12) * math.cos(phi1)))
    cos_thb = ((math.sin(phi1) - math.sin(phi2) * math.cos(delta12))
               / (math.sin(delta12) * math.cos(phi2)))
    tha = math.acos(max(-1.0, min(1.0, cos_tha)))
    thb = math.acos(max(-1.0, min(1.0, cos_thb)))

    if math.sin(lam2 - lam1) > 0.0:
        th12, th21 = tha, 2.0 * math.pi - thb
    else:
        th12, th21 = 2.0 * math.pi - tha, thb

    alpha1 = th13 - th12
    alpha2 = th21 - th23
    if math.sin(alpha1) == 0.0 and math.sin(alpha2) == 0.0:
        return None                       # the bearings are parallel
    if math.sin(alpha1) * math.sin(alpha2) < 0.0:
        return None                       # they diverge; no forward crossing

    cos_a3 = -math.cos(alpha1) * math.cos(alpha2) + \
        math.sin(alpha1) * math.sin(alpha2) * math.cos(delta12)
    delta13 = math.atan2(math.sin(delta12) * math.sin(alpha1) * math.sin(alpha2),
                         math.cos(alpha2) + math.cos(alpha1) * cos_a3)
    phi3 = math.asin(max(-1.0, min(1.0,
                                   math.sin(phi1) * math.cos(delta13)
                                   + math.cos(phi1) * math.sin(delta13) * math.cos(th13))))
    dlam13 = math.atan2(math.sin(th13) * math.sin(delta13) * math.cos(phi1),
                        math.cos(delta13) - math.sin(phi1) * math.sin(phi3))
    lam3 = lam1 + dlam13
    return LatLon(math.degrees(phi3), (math.degrees(lam3) + 540.0) % 360.0 - 180.0)


def angle_between(b1: float, b2: float) -> float:
    """Smallest angle between two bearings, 0-180 degrees."""
    d = abs((b1 - b2) % 360.0)
    return d if d <= 180.0 else 360.0 - d


@dataclass
class Fix:
    point: LatLon
    distance1_km: float
    distance2_km: float
    cut_angle_deg: float
    quality: str          # "good" | "fair" | "poor"


def triangulate(p1: LatLon, brg1: float, p2: LatLon, brg2: float,
                max_distance_km: float = 500.0,
                min_cut_angle_deg: float = 15.0) -> tuple[Fix | None, str]:
    """Cross two bearing lines and judge whether the result means anything.

    Returns (fix, reason). Two great circles almost always cross somewhere -
    two due-north bearings meet at the pole - so a raw crossing is not enough.
    A usable fix needs the lines to cut at a decent angle and to meet within a
    plausible distance.
    """
    cut = angle_between(brg1, brg2)
    if cut < min_cut_angle_deg or cut > 180.0 - min_cut_angle_deg:
        return None, ("The two bearings are within %.0f deg of parallel, so they "
                      "do not fix a position. Move further to the side of the "
                      "signal and take the second bearing from there."
                      % min(cut, 180.0 - cut))
    point = intersection(p1, brg1, p2, brg2)
    if point is None:
        return None, ("The bearings do not cross ahead of you - at least one is "
                      "pointing away from the signal.")
    d1 = distance_km(p1, point)
    d2 = distance_km(p2, point)
    if d1 > max_distance_km or d2 > max_distance_km:
        return None, ("The bearings cross %.0f km away, beyond the %.0f km limit. "
                      "That usually means one bearing is wrong or they are nearly "
                      "parallel." % (max(d1, d2), max_distance_km))
    if cut >= 45.0:
        quality = "good"
    elif cut >= 25.0:
        quality = "fair"
    else:
        quality = "poor"
    return Fix(point, d1, d2, cut, quality), "ok"


def radio_horizon_km(height_tx_m: float, height_rx_m: float = 2.0) -> float:
    """Approximate line-of-sight range including atmospheric refraction.

    The usual 4/3-earth engineering approximation. VHF/UHF signals do not
    simply stop here, but it is a good sanity check on whether a distant
    transmitter could plausibly be heard directly.
    """
    return 4.12 * (math.sqrt(max(height_tx_m, 0.0)) + math.sqrt(max(height_rx_m, 0.0)))


def free_space_path_loss_db(distance_km_: float, freq_hz: float) -> float:
    """Free-space path loss in dB. A best case - reality is always worse."""
    if distance_km_ <= 0 or freq_hz <= 0:
        return float("nan")
    return (20.0 * math.log10(distance_km_)
            + 20.0 * math.log10(freq_hz / 1e6) + 32.44)
