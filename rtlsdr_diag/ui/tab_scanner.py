"""General RF scanner over a configurable frequency range."""
from __future__ import annotations

from .sweep_base import SweepTabBase


class ScannerTab(SweepTabBase):
    tab_label = "Scanner"
    default_start_mhz = 87.5
    default_stop_mhz = 108.0
    default_raster_hz = 12_500.0
    show_presets = True
    intro_text = (
        "General RF scanner. Pick a preset or type any start/stop range, then "
        "press Start Scan. The receiver steps across the range and reports where "
        "RF energy rises above the noise floor - channel activity only, with no "
        "demodulation or decoding."
    )
