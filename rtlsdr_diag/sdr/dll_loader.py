"""Locate librtlsdr on Windows before pyrtlsdr is imported.

pyrtlsdr does not ship the native library. On Windows the user normally drops
``rtlsdr.dll`` (plus ``libusb-1.0.dll``) somewhere on PATH, or next to this
project. This module makes a best effort to find it so that the common case
"I unzipped the rtl-sdr binaries somewhere" just works.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

DLL_NAMES = ("rtlsdr.dll", "librtlsdr.dll")

_search_report: list[str] = []
_found_dll: Path | None = None


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("RTLSDR_DLL_DIR")
    if env:
        dirs.append(Path(env))
    if getattr(sys, "frozen", False):
        # Packaged as an executable: __file__ points inside the bundle, which
        # is not where a user drops their DLLs. Look beside the .exe, and in
        # whatever onefile unpacked itself into.
        beside = Path(sys.executable).resolve().parent
        dirs += [beside / "dll", beside]
        unpacked = getattr(sys, "_MEIPASS", "")
        if unpacked:
            dirs += [Path(unpacked) / "dll", Path(unpacked)]
    project_root = Path(__file__).resolve().parents[2]
    dirs += [
        project_root / "dll",
        project_root / "rtlsdr",
        project_root,
        Path.cwd() / "dll",
        Path.cwd(),
    ]
    for base in (r"C:\Program Files\rtl-sdr", r"C:\Program Files (x86)\rtl-sdr",
                 r"C:\rtl-sdr", r"C:\SDR\rtl-sdr", r"C:\tools\rtl-sdr"):
        p = Path(base)
        dirs += [p, p / "bin", p / "x64"]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry.strip():
            dirs.append(Path(entry))
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def prepare() -> Path | None:
    """Add the directory containing rtlsdr.dll to the DLL search path."""
    global _found_dll
    if _found_dll is not None:
        return _found_dll
    if sys.platform != "win32":
        _search_report.append("Non-Windows platform: relying on the system loader.")
        return None
    for d in _candidate_dirs():
        try:
            if not d.is_dir():
                continue
        except OSError:
            continue
        for name in DLL_NAMES:
            candidate = d / name
            try:
                if candidate.is_file():
                    try:
                        os.add_dll_directory(str(d))
                    except (OSError, AttributeError) as exc:  # pragma: no cover
                        _search_report.append(f"add_dll_directory({d}) failed: {exc}")
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                    _found_dll = candidate
                    _search_report.append(f"Found {name} in {d}")
                    return candidate
            except OSError:
                continue
    _search_report.append(
        "rtlsdr.dll was not found in RTLSDR_DLL_DIR, ./dll, the project folder, "
        "the usual install locations or PATH."
    )
    return None


def found_dll() -> Path | None:
    return _found_dll


def report() -> list[str]:
    return list(_search_report)
