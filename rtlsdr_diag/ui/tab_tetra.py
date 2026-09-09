"""TETRA / Nodnett RF check: carrier presence and level only."""
from __future__ import annotations

from ..config import (TETRA_BAND, TETRA_CHANNEL_WIDTH_HZ,
                      classify_tetra_freq)
from .sweep_base import SweepTabBase


def _duplex_plan_hint(freq_hz: float) -> str:
    """Qualify the frequency-only duplex classification shown in the UI."""
    kind = classify_tetra_freq(freq_hz)
    if kind.startswith("Uplink"):
        return "Uplink-plan band (inferred)"
    if kind.startswith("Downlink"):
        return "Downlink-plan band (inferred)"
    return "Outside paired bands"


class TetraTab(SweepTabBase):
    tab_label = "TETRA RF Check"
    default_start_mhz = TETRA_BAND[0] / 1e6
    default_stop_mhz = TETRA_BAND[1] / 1e6
    default_raster_hz = TETRA_CHANNEL_WIDTH_HZ
    show_presets = False
    classifier = staticmethod(_duplex_plan_hint)
    show_timeline = True
    default_min_bandwidth_hz = 10_000.0
    default_smoothing_hz = 2_000.0
    default_gap_hz = 5_000.0
    intro_text = (
        "TETRA / Nodnett RF Check - 380-400 MHz.  This mode measures RF carrier "
        "presence, level, signal-to-noise ratio and approximate bandwidth only. "
        "It does NOT demodulate, decrypt or decode any transmission, and it does "
        "not identify networks, subscribers or users. The 25 kHz column is a "
        "channel-raster estimate for visualisation - a detected carrier is not "
        "necessarily TETRA, and may be any transmitter in this band. The duplex-"
        "plan label is inferred from frequency alone; it does not establish a "
        "transmitter type, identity or distance."
    )

    def footer_text(self) -> str:
        return ("Approximately 25 kHz channel spacing is assumed for the raster "
                "column only. Bandwidth is measured from the -6 dB width of the "
                "peak. The Band-plan hint reads the frequency against the usual "
                "European duplex plan: 380-385 MHz is allocated to terminal "
                "uplinks and 390-395 MHz to base-station downlinks. It is only a "
                "frequency-plan inference. If a signal really uses that plan, "
                "receiving an uplink can be consistent with a relatively nearby "
                "terminal, while a downlink may travel tens of kilometres; RF "
                "propagation and receiver conditions prevent a distance conclusion. "
                "Passive reception and signal-strength analysis only.")
