# Third-party components

The `dll/` folder contains binaries this program did not build. They are kept
in the repository so a fresh clone runs without hunting for them. Their
licences are their own, not this project's.

## librtlsdr — `rtlsdr.dll`, `rtl_test.exe`, `rtl_sdr.exe`, `rtl_eeprom.exe`, `rtl_biast.exe`

From the RTL-SDR Blog build of librtlsdr, the driver library for RTL2832U
receivers.

* Source: <https://github.com/rtlsdrblog/rtl-sdr-blog>
* Upstream: <https://github.com/osmocom/rtl-sdr>
* Licence: GNU General Public License v2 or later

These are GPL binaries. The corresponding source is at the links above; if you
redistribute this repository you carry the same obligation to make it
available.

## pthreads-win32 — `pthreadVC2.dll`

POSIX threads for Windows, required by librtlsdr.

* Source: <https://sourceware.org/pthreads-win32/>
* Licence: GNU Lesser General Public License v2.1 or later

## Microsoft Visual C++ 2010 runtime — `msvcr100.dll`

Required by the librtlsdr build above. Redistributed under the Microsoft
Visual C++ redistributable terms that accompany it. If you would rather not
carry it, delete it and install the Microsoft Visual C++ 2010 Redistributable
instead — the program only needs it to be somewhere Windows can find it.

## Zadig — not included

Zadig binds the receiver's USB interface to WinUSB, which Windows requires
before the dongle can be opened. It is **not** in this repository. The program
downloads it on first run, over HTTPS, from its author's own release page:

* <https://github.com/pbatard/libwdi/releases>
* Author: Pete Batard
* Licence: GNU General Public License v3

The download is checked before it is kept, and it is never run without you
pressing the button. Binding a driver needs Administrator rights and a choice
about which device to touch, and that choice is made by a person in Zadig's own
window — see `rtlsdr_diag/core/provisioning.py`.

## Python packages

PySide6 (LGPLv3 / commercial), NumPy, SciPy and pyqtgraph (BSD-style) are
installed from PyPI by `requirements.txt` and are not vendored here.
