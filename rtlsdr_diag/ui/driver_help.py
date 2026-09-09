"""Windows driver (Zadig / WinUSB) guidance."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from . import theme

ZADIG_STEPS = [
    "Plug the RTL-SDR V4 directly into a USB port (avoid unpowered hubs).",
    "Close SDR#, SDRuno, rtl_tcp and any other program that may hold the device.",
    "Download Zadig from zadig.akeo.ie and run it as Administrator.",
    "In Zadig, open Options and tick \"List All Devices\".",
    "In the dropdown pick the RTL-SDR interface - it is usually named "
    "\"Bulk-In, Interface (Interface 0)\", \"RTL2838UHIDIR\" or \"RTL2832U\".",
    "Check that the USB ID shown is 0bda:2838 (or 0bda:2832). If it is not, "
    "STOP - you have the wrong device selected.",
    "Set the target driver on the right to WinUSB, then click "
    "\"Replace Driver\" / \"Install Driver\".",
    "Wait for the success message, unplug and replug the RTL-SDR, then restart "
    "this application.",
]

WARNING = (
    "Only replace the driver for the RTL-SDR device itself. Zadig can overwrite "
    "the driver of ANY USB device in that list - selecting your keyboard, mouse, "
    "webcam or a USB storage device will break it. Always confirm the USB ID "
    "reads 0bda:2838 or 0bda:2832 before clicking Replace Driver. If the device "
    "has more than one interface, choose Interface 0 (the bulk-in interface)."
)

DLL_NOTE = (
    "This application also needs the native librtlsdr library (rtlsdr.dll plus "
    "libusb-1.0.dll) on Windows - pyrtlsdr does not ship it. Download the "
    "rtl-sdr-blog release binaries, and put the DLLs either in a \"dll\" folder "
    "next to main.py, anywhere on your PATH, or point the RTLSDR_DLL_DIR "
    "environment variable at them. Use the RTL-SDR Blog build: the V4 needs the "
    "rtlsdr_blog fork of librtlsdr, older generic builds will not tune it "
    "correctly."
)


def help_text_plain() -> str:
    lines = ["RTL-SDR Windows driver setup (Zadig / WinUSB)", ""]
    lines += ["%d. %s" % (i + 1, s) for i, s in enumerate(ZADIG_STEPS)]
    lines += ["", "WARNING: " + WARNING, "", "librtlsdr: " + DLL_NOTE]
    return "\n".join(lines)


class DriverHelpWidget(QWidget):
    def __init__(self, compact: bool = False, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        title = QLabel("The RTL-SDR could not be opened - install the WinUSB driver")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: %s;" % theme.WARN)
        title.setWordWrap(True)
        lay.addWidget(title)

        steps = QLabel("\n".join("%d.  %s" % (i + 1, s)
                                 for i, s in enumerate(ZADIG_STEPS)))
        steps.setWordWrap(True)
        steps.setTextInteractionFlags(Qt.TextSelectableByMouse)
        steps.setStyleSheet(
            "background: %s; border: 1px solid %s; border-radius: 8px; padding: 12px;"
            "line-height: 150%%; color: %s;" % (theme.BG_CARD, theme.BORDER, theme.TEXT))
        lay.addWidget(steps)

        warn = QLabel("CAUTION:  " + WARNING)
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "background: rgba(239,68,68,0.10); border: 1px solid %s; "
            "border-radius: 8px; padding: 11px; color: #ffd9d9;" % theme.BAD)
        lay.addWidget(warn)

        dll = QLabel("librtlsdr:  " + DLL_NOTE)
        dll.setWordWrap(True)
        dll.setStyleSheet(
            "background: rgba(47,155,255,0.08); border: 1px solid %s; "
            "border-radius: 8px; padding: 11px; color: %s;" % (theme.ACCENT_DIM, theme.TEXT))
        lay.addWidget(dll)

        if not compact:
            lay.addStretch(1)


class DriverHelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RTL-SDR driver help (Windows)")
        self.setMinimumSize(700, 620)
        self.setStyleSheet(theme.STYLESHEET)
        lay = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(DriverHelpWidget())
        lay.addWidget(scroll)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignRight)


def show_driver_help(parent=None) -> None:
    DriverHelpDialog(parent).exec()
