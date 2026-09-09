"""Fetching the Windows driver tool the dongle needs before it will open.

An RTL-SDR will not talk to this program until its USB interface is bound to
WinUSB, and on Windows the tool everybody uses for that is Zadig. Rather than
telling somebody to go and find it, this fetches it and puts it next to the
program.

Two deliberate limits:

* It downloads, it does not install. Zadig replaces a device driver, which
  needs Administrator rights and a choice about which device to touch - so a
  person makes that choice, in Zadig's own window, not this program on their
  behalf.
* It only ever fetches from the project's own release page over HTTPS, checks
  what came back really is a Windows executable, and keeps it. Nothing is run.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path

# Zadig is published by its author through libwdi's releases. Asking the API
# means a new version is picked up without editing this file; the pinned URL is
# only there for when that request cannot be made.
ZADIG_API = "https://api.github.com/repos/pbatard/libwdi/releases/latest"
ZADIG_FALLBACK = ("https://github.com/pbatard/libwdi/releases/download/"
                  "v1.5.1/zadig-2.9.exe")
ZADIG_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
               "release-assets.githubusercontent.com")

USER_AGENT = "rtlsdr-diag/1.0 (+driver setup)"
TIMEOUT_S = 30.0
MIN_BYTES = 1_000_000          # Zadig is about 5 MB; anything tiny is an error
MAX_BYTES = 40_000_000


class ProvisionError(RuntimeError):
    """Something went wrong fetching a tool. The message is for the user."""


def tools_dir() -> Path:
    """Where downloaded tools live: beside the program, not in a temp folder."""
    override = os.environ.get("RTLSDR_TOOLS_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "tools_bin"
    return Path(__file__).resolve().parents[2] / "tools_bin"


def existing_zadig() -> Path | None:
    """The copy already downloaded, if there is one."""
    folder = tools_dir()
    try:
        if not folder.is_dir():
            return None
        found = sorted(folder.glob("zadig-*.exe"))
        return found[-1] if found else None
    except OSError:
        return None


def _check_url(url: str) -> str:
    """Refuse anything that is not the official download over HTTPS."""
    from urllib.parse import urlparse
    parts = urlparse(url)
    if parts.scheme != "https":
        raise ProvisionError("Refusing a download that is not over HTTPS.")
    host = (parts.hostname or "").lower()
    if host not in ZADIG_HOSTS and not host.endswith(".githubusercontent.com"):
        raise ProvisionError("Refusing a download from an unexpected host: %s"
                             % host)
    return url


def _open(url: str):
    request = urllib.request.Request(_check_url(url),
                                     headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    return urllib.request.urlopen(request, timeout=TIMEOUT_S, context=context)


def zadig_url() -> tuple[str, str]:
    """Ask where the current Zadig is. Returns (url, filename)."""
    try:
        with _open(ZADIG_API) as response:
            data = json.loads(response.read().decode("utf-8"))
        for asset in data.get("assets", []):
            name = str(asset.get("name", ""))
            if name.lower().startswith("zadig") and name.lower().endswith(".exe"):
                return _check_url(str(asset["browser_download_url"])), name
    except ProvisionError:
        raise
    except Exception:
        pass          # offline, rate limited, or the shape changed
    return ZADIG_FALLBACK, ZADIG_FALLBACK.rsplit("/", 1)[-1]


def ensure_zadig(progress=None, force: bool = False) -> Path:
    """Make sure Zadig is on disk and return where it is.

    `progress` is called with a short line of text as things happen, so the
    caller can show them; it is never required.
    """
    def say(message: str) -> None:
        if progress is not None:
            try:
                progress(message)
            except Exception:
                pass

    if not force:
        already = existing_zadig()
        if already is not None:
            say("Zadig is already here: %s" % already.name)
            return already

    url, name = zadig_url()
    folder = tools_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProvisionError("Could not create %s: %s" % (folder, exc)) from exc

    say("Downloading %s from %s" % (name, url.split("/")[2]))
    target = folder / name
    partial = folder / (name + ".part")
    try:
        with _open(url) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_BYTES:
                raise ProvisionError("That download is far larger than Zadig "
                                     "should be; stopping.")
            data = response.read(MAX_BYTES + 1)
    except ProvisionError:
        raise
    except Exception as exc:
        raise ProvisionError("Could not download Zadig: %s" % exc) from exc

    if len(data) > MAX_BYTES:
        raise ProvisionError("That download is far larger than Zadig should be.")
    if len(data) < MIN_BYTES:
        raise ProvisionError("The download was only %d bytes, which is not "
                             "Zadig." % len(data))
    if data[:2] != b"MZ":
        raise ProvisionError("What came back is not a Windows program.")

    try:
        partial.write_bytes(data)
        partial.replace(target)
    except OSError as exc:
        raise ProvisionError("Could not save Zadig: %s" % exc) from exc
    say("Zadig ready: %s (%.1f MB)" % (target, len(data) / 1e6))
    return target


def launch_zadig(path: Path | None = None) -> str:
    """Open Zadig so the person can bind the driver themselves.

    Returns "" if it started, otherwise why it did not. Zadig asks Windows for
    Administrator rights itself; this never chooses a device or changes a
    driver.
    """
    target = Path(path) if path is not None else existing_zadig()
    if target is None or not target.is_file():
        return "Zadig has not been downloaded yet."
    if sys.platform != "win32":
        return "Zadig is a Windows program."
    try:
        os.startfile(str(target))       # noqa: S606 - the user asked for this
    except OSError as exc:
        return "Could not start Zadig: %s" % exc
    return ""
