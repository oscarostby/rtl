"""Static configuration: presets, defaults and limits."""
from __future__ import annotations

from dataclasses import dataclass, field

MHZ = 1_000_000.0
KHZ = 1_000.0

# --- Defaults -------------------------------------------------------------
DEFAULT_SAMPLE_RATE = 2.048e6
DEFAULT_CENTER_FREQ = 100.0e6
DEFAULT_FFT_SIZE = 4096
DEFAULT_AVERAGES = 8
DEFAULT_PPM = 0
DEFAULT_GAIN = "auto"

SAMPLE_RATES = [0.25e6, 1.024e6, 1.4e6, 1.8e6, 2.048e6, 2.4e6, 2.56e6, 3.2e6]
FFT_SIZES = [512, 1024, 2048, 4096, 8192, 16384]

# RTL-SDR (R820T/R828D) usable tuning range. The V4 adds an HF direct-sampling
# path below 24 MHz through an upconverter, but we only advertise the VHF/UHF
# range that is valid for every unit.
MIN_TUNE_HZ = 24.0e6
MAX_TUNE_HZ = 1766.0e6

# Detection defaults
DEFAULT_SNR_THRESHOLD_DB = 8.0
DEFAULT_MIN_BW_HZ = 4.0 * KHZ

# TETRA / Nodnett uses a 25 kHz channel raster. We use that ONLY as a
# visualisation/estimation hint - we never assume a detected carrier is TETRA.
TETRA_CHANNEL_WIDTH_HZ = 25.0 * KHZ
TETRA_BAND = (380.0e6, 400.0e6)

# TETRA is frequency-duplex: handsets and vehicle radios transmit in one
# sub-band, base stations in another, 10 MHz higher. Which half a carrier falls
# in therefore tells you what kind of transmitter it is - a mobile near you, or
# a mast. These are the usual European emergency-services values; other
# countries and other TETRA users differ, so they are editable in the GUI.
# The whole lower half of the duplex plan. The emergency-services core is
# 380-385 paired with 390-395, but other licensed TETRA sits above that, and a
# carrier at 396 has its terminals at 386 - outside a 380-385 window, which
# would simply never be looked at.
TETRA_UPLINK_BAND = (380.0e6, 390.0e6)     # mobiles / vehicle radios transmit
# ...and the whole upper half, paired with it 10 MHz up. 390-395 is the
# emergency-services core, 395-400 carries other licensed TETRA. Both halves
# have to span the same 10 MHz or a carrier in one has no partner in the other.
TETRA_DOWNLINK_BAND = (390.0e6, 400.0e6)   # base stations transmit
TETRA_DUPLEX_SPACING_HZ = 10.0e6

# A mobile puts out about 1-3 W into a small antenna; a base station puts out
# far more from a mast. So hearing an uplink carrier at all means a terminal is
# relatively close, whereas a downlink carrier can be tens of kilometres away.
UPLINK_TYPICAL_RANGE_KM = 5.0


def classify_tetra_freq(freq_hz: float,
                        uplink=TETRA_UPLINK_BAND,
                        downlink=TETRA_DOWNLINK_BAND) -> str:
    """Say whether a frequency is a mobile uplink, a base downlink, or neither."""
    if uplink[0] <= freq_hz <= uplink[1]:
        return "Uplink (mobile)"
    if downlink[0] <= freq_hz <= downlink[1]:
        return "Downlink (base)"
    return "Other"


def duplex_partner_hz(freq_hz: float,
                      uplink=TETRA_UPLINK_BAND,
                      downlink=TETRA_DOWNLINK_BAND,
                      spacing=TETRA_DUPLEX_SPACING_HZ) -> float | None:
    """The other half of a duplex pair, if the frequency is in a TETRA band."""
    if uplink[0] <= freq_hz <= uplink[1]:
        return freq_hz + spacing
    if downlink[0] <= freq_hz <= downlink[1]:
        return freq_hz - spacing
    return None


@dataclass(frozen=True)
class BandPreset:
    """A band, plus the detector settings its channel plan needs.

    smoothing_hz fills the momentary nulls inside a modulated carrier, and
    gap_hz is how much quiet may sit inside one signal before it counts as two.
    Both must stay well below the channel spacing or adjacent channels merge:
    40 kHz suits 200 kHz FM channels and would be hopeless at 25 kHz.
    """

    name: str
    start_hz: float
    stop_hz: float
    sample_rate: float = 2.048e6
    snr_threshold_db: float = DEFAULT_SNR_THRESHOLD_DB
    note: str = ""
    min_bandwidth_hz: float = DEFAULT_MIN_BW_HZ
    smoothing_hz: float = 2.0 * KHZ
    gap_hz: float = 3.0 * KHZ


PRESETS: list[BandPreset] = [
    BandPreset("FM broadcast", 87.5e6, 108.0e6, 2.4e6, 10.0,
               "Wide FM, ~180 kHz channels. Best sanity check that an antenna is attached.",
               min_bandwidth_hz=60.0 * KHZ, smoothing_hz=15.0 * KHZ,
               gap_hz=40.0 * KHZ),
    BandPreset("Airband (AM) 118-137 MHz", 118.0e6, 137.0e6, 2.048e6, 8.0,
               "Narrow AM, bursty. Signals only appear when an aircraft/tower transmits.",
               min_bandwidth_hz=4.0 * KHZ, smoothing_hz=1.0 * KHZ,
               gap_hz=2.5 * KHZ),
    BandPreset("2 m amateur 144-146 MHz", 144.0e6, 146.0e6, 2.048e6, 8.0,
               "Narrow FM, 12.5/25 kHz channels.",
               min_bandwidth_hz=5.0 * KHZ, smoothing_hz=1.5 * KHZ,
               gap_hz=4.0 * KHZ),
    BandPreset("70 cm amateur 430-440 MHz", 430.0e6, 440.0e6, 2.048e6, 8.0,
               "Narrow FM / digital voice, 12.5/25 kHz channels.",
               min_bandwidth_hz=5.0 * KHZ, smoothing_hz=1.5 * KHZ,
               gap_hz=4.0 * KHZ),
    BandPreset("TETRA / Nodnett RF band 380-400 MHz", 380.0e6, 400.0e6, 2.048e6, 8.0,
               "RF carrier presence and level only. No demodulation or decoding.",
               min_bandwidth_hz=10.0 * KHZ, smoothing_hz=2.0 * KHZ,
               gap_hz=5.0 * KHZ),
    BandPreset("DAB+ Band III 174-240 MHz", 174.0e6, 240.0e6, 2.4e6, 10.0,
               "Wideband OFDM blocks, ~1.5 MHz each.",
               min_bandwidth_hz=500.0 * KHZ, smoothing_hz=100.0 * KHZ,
               gap_hz=300.0 * KHZ),
]

PRESET_BY_NAME = {p.name: p for p in PRESETS}

# DAB Band III block raster (ETSI / ITU Region 1). These centre frequencies are
# a fixed international standard, so a wideband signal sitting on one of them
# is almost certainly that DAB block.
DAB_BLOCKS = [
    ("5A", 174.928), ("5B", 176.640), ("5C", 178.352), ("5D", 180.064),
    ("6A", 181.936), ("6B", 183.648), ("6C", 185.360), ("6D", 187.072),
    ("7A", 188.928), ("7B", 190.640), ("7C", 192.352), ("7D", 194.064),
    ("8A", 195.936), ("8B", 197.648), ("8C", 199.360), ("8D", 201.072),
    ("9A", 202.928), ("9B", 204.640), ("9C", 206.352), ("9D", 208.064),
    ("10A", 209.936), ("10B", 211.648), ("10C", 213.360), ("10D", 215.072),
    ("11A", 216.928), ("11B", 218.640), ("11C", 220.352), ("11D", 222.064),
    ("12A", 223.936), ("12B", 225.648), ("12C", 227.360), ("12D", 229.072),
    ("13A", 230.784), ("13B", 232.496), ("13C", 234.208), ("13D", 235.776),
    ("13E", 237.488), ("13F", 239.200),
]
DAB_BLOCK_WIDTH_HZ = 1.536e6


def dab_block_for(freq_hz: float, tolerance_hz: float = 400_000.0) -> str:
    """Name the DAB block a frequency falls on, or "" if it is not near one."""
    best, best_d = "", tolerance_hz
    for name, mhz in DAB_BLOCKS:
        d = abs(freq_hz - mhz * 1e6)
        if d <= best_d:
            best, best_d = name, d
    return best


SPOT_PRESETS = [
    ("FM 100.0 MHz", 100.0e6),
    ("Airband 121.5 MHz", 121.5e6),
    ("2 m 145.0 MHz", 145.0e6),
    ("Nodnett band 390.0 MHz", 390.0e6),
    ("70 cm 433.0 MHz", 433.0e6),
]

LEGAL_NOTICE = (
    "The spectrum, scanner and TETRA views report RF energy only. The Listen tab "
    "can demodulate ordinary analog WFM, NFM and AM audio; it does not decode DAB+ "
    "or digital voice, decrypt protected traffic, or identify networks, subscribers "
    "or users. Check the rules that apply where you live before monitoring signals."
)
