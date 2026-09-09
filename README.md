# RTL-SDR V4 Diagnostic & Spectrum Monitor

A Windows desktop application for verifying that an **RTL-SDR Blog V4** is
detected over USB, working correctly, and actually receiving RF through the
connected antenna. It also provides a live spectrum analyser, waterfall,
scanner, signal-strength meter, analog-radio listening, and recent-channel
activity tracking for the 380–400 MHz TETRA/Nødnett range.

> **Scope.** This is a receive-only tool: it never transmits. The Spectrum,
> Scanner, TETRA, Radar and CSV paths measure RF energy and metadata without
> recovering communication content. The **Listen** tab is the deliberate
> exception: it demodulates ordinary analog WFM, NFM and AM to the computer's
> local audio output. The application does not decode DAB+, TETRA or any other
> digital service; decrypt traffic; or identify a network, subscriber, vehicle
> or user. Reception laws vary, so only receive content you are permitted to
> receive in your location.

---

## Contents

- [Quick start](#quick-start)
- [1. Install the RTL-SDR driver](#1-install-the-rtl-sdr-driver)
- [2. Connect the RTL-SDR](#2-connect-the-rtl-sdr)
- [3. Launch the program](#3-launch-the-program)
- [4. Run the hardware test](#4-run-the-hardware-test)
- [5. Tune to 380–400 MHz](#5-tune-to-380400-mhz)
- [6. Is the antenna actually receiving?](#6-is-the-antenna-actually-receiving)
- [Features by tab](#features-by-tab)
- [Listening to analog radio](#listening-to-analog-radio)
- [DAB+ support: RF visibility, not audio](#dab-support-rf-visibility-not-audio)
- [TETRA activity tracker](#tetra-activity-tracker)
- [Radar: where signals come from](#radar-where-signals-come-from)
- [Privacy and legal scope](#privacy-and-legal-scope)
- [Simulation mode](#simulation-mode)
- [CSV log format](#csv-log-format)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

---

## Quick start

From a Windows Command Prompt in the project directory:

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py --simulate
```

Simulation mode exercises the interface without radio hardware. For normal use,
close it and start again with `python main.py`, or double-click `run.bat`.

Useful non-interactive checks:

```bat
python main.py --check
python tools\test_demod.py
python tools\test_geo.py
python tools\smoke_test.py
```

The last check drives the complete GUI in simulation mode and takes about a
minute. Hardware-only console checks are documented under
[Run the hardware test](#4-run-the-hardware-test).

---

## 1. Install the RTL-SDR driver

On Windows an RTL-SDR needs **two** things. Both are required — installing only
one is the most common reason the device is "found but will not open".

### 1a. The WinUSB driver (Zadig)

1. Plug the RTL-SDR V4 **directly into a USB port** — avoid unpowered hubs and
   long extension cables.
2. Close SDR#, SDRuno, `rtl_tcp`, and anything else that might hold the device.
3. Download **Zadig** from <https://zadig.akeo.ie> and run it **as
   Administrator**.
4. In Zadig, open **Options → List All Devices**.
5. In the dropdown, select the RTL-SDR interface. It is usually named
   **"Bulk-In, Interface (Interface 0)"**, `RTL2838UHIDIR`, or `RTL2832U`.
6. **Confirm the USB ID reads `0bda:2838`** (or `0bda:2832`). If it does not,
   **stop** — you have the wrong device selected.
7. Set the target driver on the right to **WinUSB**, then click
   **Replace Driver** / **Install Driver**.
8. Unplug and replug the RTL-SDR, then start this application.

> ⚠️ **Be careful.** Zadig can overwrite the driver of *any* USB device in that
> list. Selecting your keyboard, mouse, webcam or a USB drive will break it.
> Always verify the USB ID before clicking Replace Driver. If the device shows
> more than one interface, choose **Interface 0** (the bulk-in interface).

### 1b. The native `librtlsdr` library

Windows has no system copy of `librtlsdr`, so you must supply `rtlsdr.dll`.

1. Download the **RTL-SDR Blog** Windows release
   (<https://github.com/rtlsdrblog/rtl-sdr-blog/releases>) and unzip it.
   Use the `x64` folder for 64-bit Python, `x86` for 32-bit
   (`python -c "import platform;print(platform.architecture())"`).
2. Copy **`rtlsdr.dll`**, **`msvcr100.dll`** and **`pthreadVC2.dll`** into
   **any one** of these places:
   - a folder named `dll` next to `main.py` (simplest — `run.bat` picks it up
     automatically),
   - any folder already on your `PATH`,
   - a folder pointed to by the `RTLSDR_DLL_DIR` environment variable.

   `msvcr100.dll` and `pthreadVC2.dll` are runtime dependencies of `rtlsdr.dll`
   and ship in the same folder. There is **no** separate `libusb-1.0.dll` in
   this release — libusb is linked into `rtlsdr.dll` already.

   Copying `rtl_test.exe` alongside them is useful: it lets you confirm the
   dongle independently of this application.

> **Use the RTL-SDR Blog build.** An RTL-SDR Blog V4 uses an R828D tuner and
> needs the `rtlsdr_blog` fork of `librtlsdr`. Generic builds will detect a V4
> but will not tune it correctly. Older dongles (R820T/R820T2) work with either.

**A note on `pyrtlsdr`.** This application does **not** require it. It talks to
`librtlsdr` through its own ctypes binding (`rtlsdr_diag/sdr/native.py`), which
binds each function independently and tolerates missing optional ones. This
matters in practice: pyrtlsdr 0.5.0 binds `rtlsdr_set_dithering` unconditionally
at import, and the RTL-SDR Blog V1.4.0 Windows release does not export it, so
pyrtlsdr fails to import and a perfectly healthy dongle looks "not found". If
pyrtlsdr *is* installed and working it is used as a fallback backend. The
**Diagnostics** tab reports which backend is active and which DLL was loaded.

---

## 2. Connect the RTL-SDR

1. Screw a 50 Ω antenna onto the SMA connector **before** plugging in the
   dongle. Running a receiver with an open input is harmless, but you will not
   receive anything useful.
2. Plug the dongle into a USB port. USB 2.0 ports are fine; a short, good
   quality cable helps.
3. The dongle gets warm in normal operation — that is expected.
4. Give it a minute after plugging in: the tuner's temperature drifts at first,
   which slightly shifts frequency until it settles.

The app polls USB every 2 seconds, so connecting or disconnecting the dongle is
reflected automatically — the big status indicator switches between
**RTL-SDR CONNECTED** and **RTL-SDR NOT FOUND** on its own.

---

## 3. Launch the program

```bat
python -m pip install -r requirements.txt
python main.py
```

or just double-click **`run.bat`**. It uses `.venv\Scripts\python.exe` when that
virtual environment exists, otherwise tries Python 3.12, adds a local `dll\`
folder to the DLL search path, forwards command-line options, and pauses on an
error so you can read the message. For example:

```bat
run.bat --simulate
run.bat --check
```

Recommended setup with a virtual environment:

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py
```

The GUI **always starts**, with or without hardware. If no RTL-SDR can be
opened, it says so, shows the Zadig instructions inline, and offers simulation
mode.

---

## 4. Run the hardware test

Open the **Dashboard** tab and press **Run Hardware Test**. It runs, in order:

| Step | What it checks |
|---|---|
| Driver library | `librtlsdr` loads, and reports the DLL path |
| USB enumeration | how many devices, their names and serial numbers |
| Open device | the WinUSB driver works and nothing else holds the device |
| Configure receiver | sample rate and centre frequency are accepted and read back |
| Tuner retune | the tuner really follows 100 / 390 / 433 MHz, within 5 kHz |
| Sample capture | 262 144 IQ samples are read, and how long that took |
| Sample stream health | not all-zero, not stuck, DC offset, clipping %, distinct ADC levels |
| Signal power | average power, noise floor, peak, and peak-to-noise ratio |

A healthy V4 with an antenna typically shows an average power around
−30 to −45 dBFS, a noise floor near −75 to −85 dBFS at a 4096-point FFT, and
clipping well under 1 %.

**What failures mean**

- *Driver library fails* → `rtlsdr.dll` is missing. See step 1b.
- *USB enumeration finds nothing* → cable, port, or the dongle itself.
- *Open device fails* → the WinUSB driver is not installed (Zadig, step 1a), or
  another program has the device open.
- *Stream is all zero or stuck* → a genuinely faulty dongle or a USB problem.
- *Heavy clipping* → turn AGC off and lower the tuner gain.

The same checks can run without opening the GUI:

```bat
python main.py --hardware-test
python main.py --antenna-test
```

Add `--simulate` to either command to verify the test workflow without hardware,
for example `python main.py --simulate --hardware-test`. A simulated pass checks
the application logic only; it says nothing about the physical dongle, antenna,
WinUSB driver or DLL installation.

---

## 5. Tune to 380–400 MHz

Two ways:

**Sweep the whole band** — open the **TETRA RF Check** tab and press
**Start Scan**. It sweeps 380–400 MHz continuously and lists every carrier it
finds with frequency, level, SNR, estimated bandwidth, the nearest 25 kHz
channel, frequency-plan classification, occupancy, burst count, first/last seen
and Active/Idle status. The timeline below the table shows up to 120 recent
sweeps. A carrier is listed once it has been seen at least twice, which keeps
single noise excursions out of the table.

**Look at one frequency** — open the **Spectrum** tab, set **Centre** to e.g.
`390.0000 MHz`, sample rate `2.048 MS/s`, and press **Start**. Hover anywhere on
the trace to read exact frequency and level. Switch to **Waterfall** to see the
same data over time. Double-clicking any row in a detection table tunes the
Spectrum tab straight to it.

For continuous monitoring of one channel, use the **Signal Meter** tab with the
bandwidth set to **25 kHz**.

> This mode reports carrier presence and strength only. It does not demodulate,
> decode or decrypt TETRA. A carrier in this range is not proof of TETRA — the
> band can contain other licensed users, and the uplink/downlink labels are only
> lookups against the configured European frequency plan.

---

## 6. Is the antenna actually receiving?

**The quick check:** press **Antenna Connected?** on the Dashboard. It measures
the spectrum at 98 MHz (FM), 145 MHz (2 m), 222 MHz (DAB+), 390 MHz and
433.9 MHz (ISM) at a fixed gain, and compares how much *structure* it sees
against a flat noise floor, then reports:

- **Likely antenna connected** — strong structured carriers found.
- **Weak/no signal** — essentially flat noise; disconnected, badly placed,
  indoors/shielded, or the wrong band for that antenna.
- **Unable to determine** — measurements were inconclusive.

**The manual check that is hardest to fool:** tune the **Spectrum** tab to a
band you know is busy where you live, press Start, then unscrew the antenna. The
signals should collapse into flat noise and the noise floor should drop
slightly. If the picture barely changes, your antenna is not really connected.

> **Do not rely on the FM broadcast band for this.** Norway switched off
> national FM in 2017 and other countries are following, so an empty FM band
> says nothing about your antenna. Better universal probes are **433 MHz** (ISM
> sensors and remotes, almost always present indoors), **2 m / 145 MHz**, and
> **DAB+ Band III** around 174–240 MHz.

To **compare antenna placements**, use the **Signal Meter** tab: pick a
frequency, watch *average* and especially *SNR* for 20–30 seconds in each
position. SNR is the number that matters — raising gain lifts signal and noise
together and does not improve reception.

> ⚠️ **An RTL-SDR cannot measure SWR, return loss or antenna impedance.** It is
> receive-only and has no directional coupler or transmitter. Every antenna
> result here is a heuristic based on received signal structure. For real
> SWR/impedance figures you need an antenna analyser or a VNA (e.g. a NanoVNA).

---

## Features by tab

| Tab | What it does |
|---|---|
| **Dashboard** | USB/receiver status, device name, tuner, serial, live frequency, level, noise floor, detected signals, Hardware Test and Antenna Check, inline driver help |
| **Spectrum** | Live FFT with peak hold and noise-floor line; centre frequency, span/sample rate, tuner gain, AGC, PPM correction, FFT size and averaging; hover readout of frequency and level |
| **Waterfall** | Scrolling waterfall (X = frequency, Y = time, newest on top) with adjustable floor offset, dynamic range and history depth |
| **TETRA RF Check** | 380–400 MHz RF-energy sweep; carrier table with frequency, level, SNR, bandwidth, nearest 25 kHz raster point, frequency-plan uplink/downlink hint, occupancy %, burst count, first/last seen and status; plus a recent-sweep activity timeline. No TETRA demodulation or decoding |
| **Signal Meter** | One frequency monitored continuously: current / average / max / min level, noise floor, SNR, a large meter and a level-vs-time history plot |
| **Scanner** | RF-energy sweep over any configurable range, with presets for FM broadcast, airband, 2 m, 70 cm, TETRA/Nødnett and DAB+ Band III; no audio or protocol decoding |
| **Radar** | Polar map centred on your position with a scalable range (0.5–2000 km): known transmitter sites at true bearing and distance, lit by the level measured on their frequency; manual direction-finding bearing lines and triangulated fixes; a signal-strength-versus-bearing rose; and a coverage survey plotting measured strength at each place you stood |
| **Listen** | Demodulates and locally plays analog wide FM (broadcast), narrow FM (amateur/PMR) and AM (airband), with volume, squelch, presets and a channel-level meter; no DAB+, digital voice, stereo FM or RDS decoding |
| **Diagnostics** | Library versions, DLL path, USB strings, current receiver settings, frame/sample counters, estimated dropped samples, last device error, and **Copy Diagnostics** |

All acquisition runs on a worker thread; the UI never blocks on USB. The engine
applies back-pressure — if the display falls behind, frames are dropped rather
than queued (the count is shown in Diagnostics).

---

## Listening to analog radio

The **Listen** tab is a single-frequency analog receiver. It demodulates and
plays:

| Mode | Use | Capture rate |
|---|---|---|
| **WFM** | FM broadcast, 87.5–108 MHz | 2.400 MS/s |
| **NFM** | amateur, marine, PMR — 12.5 kHz channels | 1.920 MS/s |
| **AM** | aviation airband, 118–137 MHz | 1.920 MS/s |

Pick the correct mode, type a frequency (or use a preset), and press **Listen**.
Starting it replaces any current spectrum, meter or sweep acquisition because
one RTL-SDR can run only one tuning job in this application at a time. The
volume control changes the local output level. Squelch mutes audio while the
measured channel level is below its dBFS threshold; `-100 dBFS` effectively
leaves it open.

Audio is 48 kHz, 16-bit, mono and is sent to the default Windows output device
shown on the *Output* tile. The *Dropped blocks* tile counts audio blocks the
application discarded instead of building up playback delay. This is a simple
analog monitor: WFM output is mono, with no stereo or RDS decoding, and the app
does not record audio.

Those capture rates are not arbitrary: they make every decimation factor an
integer (2.4 MS/s ÷ 10 → 240 kHz ÷ 5 → 48 kHz), and each filter carries its
state from one block to the next. A per-block filter restart would put a click
at every boundary and turn repeated boundaries into a buzz. The synthetic
checks in `tools/test_demod.py` compare continuous processing with live-sized
and deliberately irregular input blocks; their output must agree sample for
sample.

## DAB+ support: RF visibility, not audio

This application does **not** play DAB+ and does not decode an ensemble, station
list, service metadata or audio. DAB+ requires a complete digital receiver
chain (OFDM synchronisation and demodulation, error correction,
de-interleaving, service parsing and an audio codec); that chain is not
implemented here.

The DAB-related features are deliberately limited to:

- a **DAB+ Band III 174–240 MHz** Scanner preset for finding and measuring
  wideband RF energy;
- the standard Band III block centre-frequency list, **5A through 13F**, in the
  Listen tab's **Jump to block** selector; and
- a frequency lookup that can say, for example, that 222.064 MHz is the
  standard centre of block 11D.

The block label comes only from the selected frequency. It does not inspect the
signal, prove that a detected signal is DAB, or reveal an ensemble's contents.
Use dedicated DAB receiver software if decoded services or audio are required.

## TETRA activity tracker

The **TETRA RF Check** tab is an RF activity tracker, not a TETRA receiver. It
sweeps the configured 380–400 MHz range and groups repeated energy detections
into carrier rows. For each row it shows measured level and SNR, approximate
bandwidth, nearest 25 kHz raster frequency, first/last seen time, recent status,
occupancy and burst count. These are receiver observations, not decoded network
data.

### Frequency-plan labels are hints

The *Transmitter* column compares only the measured centre frequency with the
default European emergency-services duplex plan:

| Configured sub-band | Label | Meaning if the signal really uses this TETRA plan |
|---|---|---|
| 380–385 MHz | **Uplink-plan band (inferred)** | the uplink side normally used by handsets or vehicle radios |
| 390–395 MHz | **Downlink-plan band (inferred)** | the downlink side normally used by base stations |
| Remaining parts of 380–400 MHz | **Outside paired bands** | outside the two configured sub-bands |

The label is a frequency lookup only. It does not establish that the signal is
TETRA, identify an actual transmitter type, pair two live channels, estimate
distance, or identify a network, vehicle, subscriber or user. Plans differ by
country and system; the defaults are `TETRA_UPLINK_BAND` and
`TETRA_DOWNLINK_BAND` in `rtlsdr_diag/config.py`.

### Occupancy, bursts and timeline

Every completed 380–400 MHz sweep is one observation pass:

- **Occupancy** is the percentage of the most recent 60 passes in which that
  carrier was detected.
- **Bursts** counts off-to-on transitions over the same 60-pass window; it is
  not a decoded call or timeslot count.
- **Activity timeline** shows up to 120 recent passes, one row per tracked
  carrier and newest pass on the right. A coloured cell means the carrier was
  detected in that pass, with colour reflecting measured level. Click a row to
  tune the Spectrum tab to that frequency.

Continuous activity therefore draws a mostly solid row while intermittent
activity draws separated cells. Results depend on the SNR threshold, sweep
speed and reception conditions, and a sweep can miss transmissions that occur
while the tuner is visiting another segment. **Clear** resets the in-memory
tracker. CSV logging records individual RF sightings, not demodulated content.

## Radar: where signals come from

### What a receiver can and cannot know

**One antenna gives you no direction and no distance.** An RTL-SDR measures how
much energy arrives, not which way it came from. Getting a direction needs one
of:

- a **directional antenna** you rotate by hand (a Yagi, or a loop with a null),
- a **coherent multi-channel receiver** such as a KrakenSDR, which compares
  phase across several antennas,
- or **several receivers** at known positions comparing arrival times (TDoA).

Distance is worse: received power alone cannot give it, because you would need
the transmitter's power, both antenna gains and the real path loss. Anything
that drew vehicles or transmitters at made-up positions from a single dongle
would be inventing them, so this tab does not.

Everything on the radar is one of three honest things:

**1. Known transmitters.** Broadcast sites are public infrastructure with
published coordinates — in Norway the [Nkom frequency
register](https://frekvens.nkom.no). Put them in a CSV and the radar shows each
one at its true bearing and distance from you, and lights it up with the signal
level actually being measured on its frequency. That answers "where is this
signal coming from" with real data rather than a guess.

Use **Write template** for the CSV format:

```csv
name,kind,frequency_mhz,latitude,longitude,height_m,power_kw,notes
Some DAB site,DAB,222.064,59.98000,10.66000,300,10,
```

Every row in the template is commented out with a leading `#`, so it loads as
empty until you fill in real data. `kind` is one of FM, DAB, TV, TETRA site,
Amateur, Other. Hovering a marker shows bearing, range, measured level and the
mast's approximate radio horizon.

**2. Manual direction finding.** Point a directional antenna, note the true
bearing where the signal peaks, type it into **Bearing** and press **Record
bearing** — the radar draws the ray from where you stood. Move a few kilometres
*to the side* of the signal, take a second bearing, and press **Triangulate**.
Where the rays cross is the transmitter.

The maths is guarded: bearings closer than 15° to parallel are rejected
(two near-parallel lines cross almost anywhere), reversed bearings are rejected,
and the cut angle is reported — below 25° the fix is marked *poor*. A shallow
cut is the classic way to fool yourself in a transmitter hunt.

**3. A signal-strength rose.** Rotate a directional antenna and press **Add
current level at bearing** at each step. The green shape is the antenna pattern
you measured; its peak points at the transmitter. With an omnidirectional
antenna the rose is a circle — which is itself the honest answer.

### Why there is no street map

There is no map tile layer: this application makes no network calls, and a
polar plot shows exactly what a receiver knows — bearing and range — without
implying a precision that is not there. Your position is entered by hand; there
is no GPS on most desktops.

### Coverage survey

This is the honest way to map a network, and it is what operators and
regulators actually do: record how strong the signal is **at each place you
stand**. Set your position, press **Record here**, move, repeat. Each dot on
the radar is a real measurement at a real place, coloured blue (weak) through
green to amber (strong), and the whole survey saves to CSV:

```csv
timestamp,latitude,longitude,frequency_mhz,level_dbfs,noise_dbfs,snr_db,notes
2026-09-08T19:25:33,59.913900,10.752200,390.012500,-40.30,-79.10,38.80,
```

Note what this maps: **your reception**, not a transmitter's position. A weak
patch means you received poorly there — which could be distance, terrain, a
building, or your antenna — and that is exactly the question a coverage survey
is meant to answer.

### Radar limits

The radar cannot automatically show where a mobile terminal is. One ordinary
antenna measures received energy but not arrival direction or range. The manual
bearing workflow can estimate the intersection of bearings that **you** record
with a directional antenna; that estimate is not identity information and can
be badly affected by reflections or poor bearing geometry. Automatic direction
finding would require different hardware, such as a coherent antenna array, or
multiple synchronised receivers.

## Privacy and legal scope

- The RTL-SDR and this application are **receive-only**; they do not transmit.
- Spectrum, Scanner, TETRA, Radar and CSV features handle RF measurements such
  as frequency, level, time and user-entered location/bearing data. They do not
  recover message or voice content.
- Listen intentionally demodulates **analog** WFM, NFM and AM waveforms and
  plays them through the local default audio device. It does not decrypt
  anything, and analog demodulation must not be confused with permission to
  listen.
- DAB+ and TETRA are not demodulated or decoded. The app does not determine an
  ensemble, talkgroup, network, subscriber, vehicle, user or speaker identity.
- The runtime contains no telemetry or network client and does not save audio.
  It writes files only when you start CSV logging or explicitly save site,
  bearing or coverage data. Those files can contain timestamps and locations;
  store and share them accordingly.

Radio-reception and privacy rules vary by country, frequency and service. You
are responsible for using suitable antennas, frequencies and listening modes
only where permitted, and for handling any incidentally received information
lawfully. Do not use the measurements to target or claim to identify people.
This project documentation is not legal advice.

## Simulation mode

`python main.py --simulate`, the **Device → Simulation mode** menu item, or the
checkbox on the Diagnostics tab.

The simulator synthesises IQ with a realistic noise floor and a catalogue of
carriers (FM broadcast, airband, DAB+, 2 m, 70 cm, and 25 kHz-raster carriers in
the 380–400 MHz band), so every screen, table and test can be exercised without
hardware. The Diagnostics tab also has **Simulated antenna connected**, which
lets you see what the antenna check reports in both states.

Simulation mode is clearly labelled everywhere — the status indicator turns
amber and reads **SIMULATION MODE**. No real RF is received.

To verify the whole UI end to end:

```bat
python tools\smoke_test.py
```

This builds the window off-screen, drives every tab, runs both tests and fails
on any exception.

---

## CSV log format

**Start Logging** on the TETRA, Scanner or Signal Meter tabs (or
**Logging → Start logging to CSV…** for a chosen filename) writes:

```csv
timestamp,frequency_hz,frequency_mhz,signal_dbfs,noise_floor_dbfs,snr_db,estimated_bandwidth_hz,source
2026-09-08T17:41:22.184,390012500,390.012500,-39.82,-79.10,39.28,22500,TETRA RF Check
```

Files default to `rtlsdr_signals_YYYYmmdd_HHMMSS.csv` in the working directory.
Every sighting is written, including unconfirmed ones, so the log is a complete
measurement record rather than only what the table shows.

---

## Project layout

```
main.py                     entry point (--simulate, --check)
run.bat                     Windows launcher
requirements.txt
tools/smoke_test.py         headless end-to-end UI test
tools/test_geo.py           geodesy and triangulation checks
tools/test_demod.py         demodulator checks against synthetic signals
tools/capture_tabs.py       render every tab to screenshots/
tools/capture_radar.py      render the radar with a synthetic scene
rtlsdr_diag/
    config.py               presets, defaults, band definitions
    core/
        detections.py       carrier tracking (first/last seen, confirmation)
        csvlog.py           CSV logger
        geo.py              distance, bearing, projection, triangulation
        sites.py            transmitter sites and recorded bearings
    sdr/
        dll_loader.py       finds rtlsdr.dll on Windows
        native.py           built-in ctypes binding to librtlsdr
        device.py           enumeration + real hardware source
        simulator.py        synthetic IQ source
        dsp.py              PSD, noise floor, carrier detection, stream health
        demod.py            WFM / NFM / AM audio demodulation
        engine.py           worker-thread acquisition engine
        hardware_test.py    hardware self-test procedure
        antenna_test.py     antenna heuristic
    ui/
        theme.py  widgets.py  plots.py  controls.py  tables.py
        radar.py            polar radar rendering
        timeline.py         channel activity timeline
        audio.py            Qt Multimedia playback
        main_window.py      thread wiring and signal routing
        tab_*.py            one module per tab
        sweep_base.py       shared sweep tab logic
        driver_help.py      Zadig / WinUSB guidance
```

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Error loading librtlsdr` | `rtlsdr.dll` not found — see step 1b. Check the path shown in Diagnostics. |
| `AttributeError: function 'rtlsdr_set_dithering' not found` | A pyrtlsdr/DLL version mismatch. Harmless here — the built-in ctypes backend is used instead. Diagnostics will show `Backend in use: native`. |
| Device found, but every band is flat noise | Another program may have left the dongle in **direct sampling** (HF) mode — `rtl_test -t` does this. The app forces it off on open; if in doubt, unplug and replug the dongle. Verify the tuner path is live by changing the gain: the noise floor should move by tens of dB. |
| Tuner reports `R820T` when you expected a V4 | The dongle is not an RTL-SDR Blog V4. A genuine V4 reports **R828D**. It will still work fine from 24 MHz to 1.7 GHz; it just lacks the V4's HF direct-sampling path and improved filtering. |
| **RTL-SDR NOT FOUND** with the dongle plugged in | WinUSB driver not installed (Zadig, step 1a), or a bad cable/port. |
| **FOUND BUT NOT OPEN** | Another program holds the device, or Zadig was applied to the wrong interface. Close SDR#/`rtl_tcp` and retry. |
| Device disappears mid-session | USB power saving or a marginal cable. The app detects this, stops cleanly and tells you to press Start again. |
| Frequencies are consistently offset | Set **Frequency correction (PPM)** on the Spectrum tab. Calibrate against a known transmitter. |
| Spectrum looks flat everywhere | Antenna not connected, or gain far too low. Turn AGC on and check a band known to be active locally; in Norway, DAB+ Band III or nearby 433 MHz devices are generally a better check than national FM. |
| Spectrum full of spurious peaks | Gain too high — the front end is overloading. Turn AGC off and reduce gain until the peaks vanish. The Hardware Test reports the clipping percentage. |
| Sample drops at 2.4–3.2 MS/s | Normal on many USB controllers. Use 2.048 MS/s. |
| High CPU usage | Lower the FFT size, reduce averaging, or reduce the waterfall history. |
| Listen shows no audio | Confirm the default Windows output device supports 48 kHz mono 16-bit audio, choose the matching WFM/NFM/AM mode, lower the squelch, and verify the frequency is active. DAB+ and digital voice cannot be played. |
| Listen reports dropped blocks | The audio output or GUI fell behind and the block was discarded to avoid growing latency. Close audio-exclusive applications and reduce other system load. |

---

## Limitations

- Levels are **dBFS** — relative to the receiver's full scale. They are *not*
  calibrated to dBm; the RTL-SDR has no absolute power calibration, and readings
  shift with gain, frequency and temperature. Use them for comparison, not as
  absolute field-strength measurements.
- **No SWR/impedance measurement** is possible with a receive-only device.
- Only one acquisition job can run at a time. Listening, a range sweep, a
  single-frequency meter and live spectrum acquisition do not run concurrently.
- Analog audio is 48 kHz mono only. There is no FM stereo/RDS, audio recording,
  digital-voice, DAB+ or TETRA decoding.
- Bandwidth figures are estimated from the −6 dB width of each peak and are
  approximate, especially for bursty signals.
- Dropped samples are *estimated* from timing gaps between reads; `librtlsdr`
  does not report drops directly in synchronous mode.
- The 25 kHz channel raster is a display aid. A detected carrier is not
  necessarily TETRA.
- DAB block names are centre-frequency lookups, not signal classifications; no
  ensemble or service information is recovered.
- Sweeps are not gap-free in time: the receiver visits one segment at a time, so
  a short transmission elsewhere in the range can be missed.
- Simulation mode uses synthetic signals. It validates workflows and UI state,
  not real-world sensitivity, selectivity, driver health or antenna performance.
