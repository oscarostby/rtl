"""Fetching the driver tool: where from, what is accepted, and what is not.

Runs entirely offline. The network is replaced, because a test that depends on
GitHub being reachable tells you about GitHub, not about this code.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

SANDBOX = Path(tempfile.mkdtemp(prefix="rtlsdr_tools_"))
os.environ["RTLSDR_TOOLS_DIR"] = str(SANDBOX)

from rtlsdr_diag.core import provisioning  # noqa: E402
from rtlsdr_diag.core.provisioning import (ProvisionError,  # noqa: E402
                                           ensure_zadig, existing_zadig,
                                           launch_zadig, tools_dir)

failures: list[str] = []


def check(name, ok, detail=""):
    print("%-58s %s%s" % (name, "OK" if ok else "FAIL",
                          ("  " + detail) if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def fails(fn, fragment=""):
    try:
        fn()
    except ProvisionError as exc:
        return fragment.lower() in str(exc).lower() if fragment else True
    except Exception:
        return False
    return False


class FakeResponse(io.BytesIO):
    def __init__(self, payload, length=None):
        super().__init__(payload)
        self.headers = {"Content-Length": str(length if length is not None
                                              else len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def serve(payload, length=None):
    """Replace the network with a fixed answer."""
    provisioning._open = lambda url: FakeResponse(payload, length)


real_open = provisioning._open

print("--- where it is allowed to fetch from ---")
check("plain HTTP is refused",
      fails(lambda: provisioning._check_url("http://github.com/a.exe"), "https"))
check("another host is refused",
      fails(lambda: provisioning._check_url("https://evil.example.com/z.exe"),
            "unexpected host"))
check("a lookalike host is refused",
      fails(lambda: provisioning._check_url("https://github.com.evil.net/z.exe"),
            "unexpected host"))
check("the real release host is allowed",
      provisioning._check_url(provisioning.ZADIG_FALLBACK)
      == provisioning.ZADIG_FALLBACK)
check("the pinned fallback is an https github URL",
      provisioning.ZADIG_FALLBACK.startswith("https://github.com/pbatard/"))

print("")
print("--- what it accepts as a download ---")
check("the tools folder honours the override", tools_dir() == SANDBOX,
      str(tools_dir()))

serve(b"MZ" + b"\0" * 2_000_000)
path = ensure_zadig()
check("a real-looking executable is kept", path.is_file(), path.name)
check("it landed in the tools folder", path.parent == SANDBOX)
check("and is found again afterwards", existing_zadig() == path)

check("a second call does not download again",
      ensure_zadig() == path)

# Now the things that must be rejected. Each leaves the good copy alone.
serve(b"MZ" + b"\0" * 10)
check("something far too small is rejected",
      fails(lambda: ensure_zadig(force=True), "not Zadig"))

serve(b"<html>not an exe</html>" + b"\0" * 2_000_000)
check("something that is not a Windows program is rejected",
      fails(lambda: ensure_zadig(force=True), "not a Windows program"))

serve(b"MZ" + b"\0" * 2_000_000, length=provisioning.MAX_BYTES + 1)
check("something enormous is rejected",
      fails(lambda: ensure_zadig(force=True), "larger"))

check("the good copy survived every rejection", path.is_file())
check("no half-written file is left behind",
      not list(SANDBOX.glob("*.part")), str(list(SANDBOX.glob("*.part"))))


def boom(url):
    raise OSError("network is down")


provisioning._open = boom
check("being offline is reported, not raised as a crash",
      fails(lambda: ensure_zadig(force=True), "could not download"))
provisioning._open = real_open

print("")
print("--- it downloads, it does not install ---")
# Nothing in this module may bind a driver or ask for Administrator rights.
source = Path(provisioning.__file__).read_text(encoding="utf-8").lower()
for forbidden in ("runas", "shellexecute", "libwdi.dll", "installdriver",
                  "pnputil", "devcon"):
    if forbidden in source:
        check("it never installs a driver itself", False, forbidden)
        break
else:
    check("it never installs a driver itself", True)

for spare in SANDBOX.glob("*"):
    spare.unlink()
check("with nothing downloaded, launching says so",
      "not been downloaded" in launch_zadig())

print("")
if failures:
    print("PROVISIONING TESTS FAILED (%d):" % len(failures))
    for f in failures:
        print("   - %s" % f)
    sys.exit(1)
print("ALL PROVISIONING TESTS PASSED")
