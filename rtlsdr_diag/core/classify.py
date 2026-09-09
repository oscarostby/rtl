"""Classify a detection from its frequency and occupied bandwidth.

This is band-plan arithmetic, nothing more. It says what *kind of RF signal*
something looks like - never who is transmitting, and never that a protocol has
been decoded. "TETRA-like" means "a narrowband carrier on the 25 kHz raster in
the 380-400 MHz band", which is a description of the RF, not an identification
of a network, an organisation or a user.
"""
from __future__ import annotations

from ..config import DAB_BLOCK_WIDTH_HZ, TETRA_CHANNEL_WIDTH_HZ, dab_block_for
from .models import (CLASS_AIRBAND, CLASS_AMATEUR, CLASS_DAB, CLASS_FM,
                     CLASS_ISM, CLASS_MARINE, CLASS_TETRA, CLASS_UNKNOWN,
                     CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MEDIUM)

MHZ = 1e6

# (class, low Hz, high Hz, expected bandwidth low, expected bandwidth high)
BAND_PLAN = [
    (CLASS_FM, 87.5 * MHZ, 108.0 * MHZ, 80e3, 300e3),
    (CLASS_AIRBAND, 108.0 * MHZ, 137.0 * MHZ, 2e3, 20e3),
    (CLASS_AMATEUR, 144.0 * MHZ, 146.0 * MHZ, 2e3, 30e3),
    (CLASS_MARINE, 156.0 * MHZ, 163.0 * MHZ, 5e3, 30e3),
    (CLASS_DAB, 174.0 * MHZ, 240.0 * MHZ, 800e3, 2.2e6),
    (CLASS_TETRA, 380.0 * MHZ, 400.0 * MHZ, 8e3, 40e3),
    (CLASS_AMATEUR, 430.0 * MHZ, 440.0 * MHZ, 2e3, 30e3),
    (CLASS_ISM, 433.05 * MHZ, 434.79 * MHZ, 1e3, 300e3),
    (CLASS_ISM, 863.0 * MHZ, 870.0 * MHZ, 1e3, 300e3),
]


def classify(freq_hz: float, bandwidth_hz: float = 0.0) -> tuple[str, str]:
    """Return ``(signal_class, confidence)`` for a detection.

    Confidence is HIGH when the frequency lands in a band *and* the measured
    bandwidth fits what that band normally carries, MEDIUM when only the band
    matches, and LOW when nothing does.
    """
    if freq_hz <= 0:
        return CLASS_UNKNOWN, CONFIDENCE_LOW

    # ISM overlaps the 70 cm amateur allocation; prefer the narrower ISM window
    # when the frequency is inside it, since that is the more specific claim.
    candidates = [entry for entry in BAND_PLAN
                  if entry[1] <= freq_hz <= entry[2]]
    if not candidates:
        return CLASS_UNKNOWN, CONFIDENCE_LOW
    candidates.sort(key=lambda e: e[2] - e[1])
    name, lo, hi, bw_lo, bw_hi = candidates[0]

    if bandwidth_hz <= 0:
        return name, CONFIDENCE_MEDIUM

    if bw_lo <= bandwidth_hz <= bw_hi:
        confidence = CONFIDENCE_HIGH
    elif bw_lo * 0.4 <= bandwidth_hz <= bw_hi * 2.5:
        confidence = CONFIDENCE_MEDIUM
    else:
        # Bandwidth flatly contradicts the band - say so rather than insist.
        return CLASS_UNKNOWN, CONFIDENCE_LOW

    # DAB has a published block raster; landing on one is corroboration.
    if name == CLASS_DAB and confidence == CONFIDENCE_HIGH:
        if not dab_block_for(freq_hz):
            confidence = CONFIDENCE_MEDIUM

    return name, confidence


def describe_class(freq_hz: float, bandwidth_hz: float) -> str:
    """A short human description including any raster hint."""
    name, confidence = classify(freq_hz, bandwidth_hz)
    if name == CLASS_DAB:
        block = dab_block_for(freq_hz)
        if block:
            return "%s block %s" % (name, block)
    if name == CLASS_TETRA:
        channel = round(freq_hz / TETRA_CHANNEL_WIDTH_HZ) * TETRA_CHANNEL_WIDTH_HZ
        return "%s (nearest 25 kHz channel %.4f MHz)" % (name, channel / 1e6)
    return name


def classification_note(signal_class: str) -> str:
    """The caveat that belongs next to a classification in the UI."""
    if signal_class == CLASS_TETRA:
        return ("Matches TETRA's band and channel width. This is an RF "
                "description only - nothing has been demodulated or decoded, "
                "and no network, organisation or user is identified.")
    if signal_class == CLASS_DAB:
        return ("Wideband signal on a DAB Band III block centre. Presence and "
                "level only; this application does not decode DAB.")
    if signal_class == CLASS_UNKNOWN:
        return "Frequency and bandwidth do not match a band plan entry."
    return "Classified from frequency and occupied bandwidth only."
