"""Build a single Windows executable.

    python build_exe.py

Produces dist/RTL-SDR-Detector.exe. Double-clicking it opens the detector and,
on first run, fetches Zadig into a tools_bin folder beside the executable so
the USB driver can be set up without hunting for it.

The native DLLs are carried inside the executable; sdr/dll_loader.py knows to
look in the unpacked bundle as well as beside the .exe, so a copy dropped next
to it still wins if you want to try a different build of librtlsdr.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "RTL-SDR-Detector"

# Qt ships far more than this program uses, and every unused module is weight
# in the download and time on every start.
EXCLUDE = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    "tkinter", "matplotlib", "pytest", "PIL", "IPython", "notebook",
]

HIDDEN = [
    "PySide6.QtTextToSpeech",     # the voice
    "PySide6.QtMultimedia",       # audio output
    "scipy.signal",
]


def main() -> int:
    if sys.platform != "win32":
        print("This build script makes a Windows executable.")
        return 2
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:  pip install pyinstaller")
        return 2

    for stale in (ROOT / "build", ROOT / "dist"):
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onefile", "--windowed", "--name", NAME,
           "--add-data", "%s%sdll" % (ROOT / "dll", ";")]
    for module in EXCLUDE:
        cmd += ["--exclude-module", module]
    for module in HIDDEN:
        cmd += ["--hidden-import", module]
    cmd.append(str(ROOT / "main.py"))

    print("Building %s ..." % NAME)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print("Build failed.")
        return result.returncode

    built = ROOT / "dist" / ("%s.exe" % NAME)
    if not built.is_file():
        print("Build reported success but %s is missing." % built)
        return 1
    print("")
    print("Built %s  (%.0f MB)" % (built, built.stat().st_size / 1e6))
    print("Copy it anywhere and run it. Zadig lands in tools_bin next to it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
