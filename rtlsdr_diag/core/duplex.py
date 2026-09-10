"""Working out where a terminal would transmit, from where the mast does.

TETRA is frequency duplex with a fixed 10 MHz spacing in the 380-400 MHz band,
so every base station carrier being received here names exactly one uplink
channel: the one a terminal on that cell transmits on.

That matters because of what the receiver is. The published SDR sensors for
this job take the whole 5 MHz uplink band in at once; an RTL-SDR at 2.048 MS/s
sees about 1.5 MHz of it, so sweeping the band means any one channel is only
watched a third of the time, and a transmission lasting a couple of seconds can
fall entirely into the two thirds that are not.

Rather than sweeping and hoping, this places one receiver window over the
uplink channels that could actually carry traffic here - the partners of the
downlinks being received - and leaves it there. Those channels are then watched
continuously.

Nothing here identifies a transmitter. It is arithmetic on the band plan: a
frequency, minus 10 MHz.
"""
from __future__ import annotations

from ..config import TETRA_DUPLEX_SPACING_HZ
from .detector import MODE_SPECS, TETRA_MAST, TETRA_MOBILE

# How far from a window's edge the channel that matters must stay. The sweep
# already keeps only the middle 75% of what it samples, so this is not about
# the receiver's response falling away - it is simply that a 25 kHz carrier
# sitting astride the boundary would have half of itself cut off. A couple of
# channels' worth of room is enough, and sizing it to the signal rather than to
# a fraction of the window leaves room to cover more channels.
EDGE_MARGIN_HZ = 60e3


def uplink_partners(detections, spacing: float = TETRA_DUPLEX_SPACING_HZ):
    """(frequency, snr) of the uplink channel paired with each mast carrier."""
    mast = MODE_SPECS[TETRA_MAST]
    mobile = MODE_SPECS[TETRA_MOBILE]
    partners = []
    for detection in detections:
        if not mast.accepts(detection):
            continue
        freq = detection.freq_hz - spacing
        if mobile.contains(freq):
            partners.append((freq, max(0.0, detection.snr_db)))
    return sorted(partners)


def best_window(partners, width_hz: float):
    """Where to put one receiver window so it covers the most worth covering.

    The window must cover the partner of the *strongest* downlink. That is not
    a tie-breaker but the whole point: the loudest base station is the nearest
    cell, and a terminal standing next to you is camped on the nearest cell.

    Scoring windows by the total strength inside them looks reasonable and is
    not: measured against a real band, three strong partners lost to a crowd of
    twenty-eight weak ones several MHz away, and the receiver was pointed at
    empty spectrum. So the strongest partner anchors the window, and the rest
    only decide where to slide it around that anchor.

    An optimal window can always be slid until its lower edge sits on one of
    the channels, so trying each channel as a lower edge finds the best one.
    """
    if not partners or width_hz <= 0:
        return None
    anchor_hz = max(partners, key=lambda p: p[1])[0]
    # Keep the anchor's whole carrier inside, not astride the boundary.
    margin = min(EDGE_MARGIN_HZ, width_hz * 0.25)
    best = None
    for lower, _ in partners:
        start = lower - margin
        stop = start + width_hz
        if not (start + margin <= anchor_hz <= stop - margin):
            continue                     # anchor on the edge, or missing
        inside = [(f, w) for f, w in partners if start <= f <= stop]
        score = sum(w for _, w in inside)
        if best is None or score > best[0] or (score == best[0]
                                               and len(inside) > best[3]):
            best = (score, start, stop, len(inside))
    if best is None:                     # only the anchor itself fits
        start = anchor_hz - width_hz / 2.0
        return start, start + width_hz, 1
    _, start, stop, count = best
    return start, stop, count


def watch_window(detections, width_hz: float, spacing: float = TETRA_DUPLEX_SPACING_HZ):
    """The uplink window to sit on, or None if no mast carrier is known yet.

    Clamped to the uplink half: a window is a place to point the receiver, not
    a licence to look outside the band this program searches.
    """
    partners = uplink_partners(detections, spacing)
    placed = best_window(partners, width_hz)
    if placed is None:
        return None
    start, stop, count = placed
    mobile = MODE_SPECS[TETRA_MOBILE]
    if start < mobile.start_hz:
        start, stop = mobile.start_hz, mobile.start_hz + width_hz
    if stop > mobile.stop_hz:
        stop, start = mobile.stop_hz, mobile.stop_hz - width_hz
    return start, stop, count
