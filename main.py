"""RTL-SDR V4 Diagnostic & Spectrum Monitor - application entry point.

Usage:
    python main.py                 normal start (uses real hardware if present)
    python main.py --simulate      start in simulation mode (no hardware needed)
    python main.py --check         run a non-GUI import/environment self-check
"""
from __future__ import annotations

import argparse
import sys
import traceback


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def run_check() -> int:
    """Import everything and report the environment - no GUI is created."""
    _reconfigure_stdio()
    problems: list[str] = []
    print("RTL-SDR Diagnostic - environment check")
    print("-" * 60)
    print("Python              : %s" % sys.version.split()[0])
    for name in ("PySide6", "numpy", "scipy", "pyqtgraph", "rtlsdr"):
        try:
            mod = __import__(name)
            print("%-20s: %s" % (name, getattr(mod, "__version__", "installed")))
        except Exception as exc:
            note = " - optional, the built-in binding is used instead" if name == "rtlsdr" else ""
            print("%-20s: MISSING (%s)%s" % (name, exc, note))
            if name != "rtlsdr":
                problems.append(name)

    from rtlsdr_diag.sdr import device as dev
    print("backend             : %s" % dev.BACKEND)
    print("librtlsdr loadable  : %s" % ("yes" if dev.library_available() else "no"))
    print("library status      : %s" % dev.library_status())
    print("devices on USB      : %d" % dev.device_count())
    for info in dev.enumerate_devices():
        print("  -> %s" % info.label())

    try:
        from rtlsdr_diag.ui import main_window  # noqa: F401
        print("GUI modules         : import OK")
    except Exception as exc:
        print("GUI modules         : FAILED (%s)" % exc)
        traceback.print_exc()
        problems.append("ui")

    print("-" * 60)
    if problems:
        print("Problems with: %s" % ", ".join(problems))
        return 1
    if not dev.library_available() or dev.device_count() == 0:
        print("No usable RTL-SDR detected - the GUI will still start, and you can "
              "use simulation mode (python main.py --simulate).")
    else:
        print("Everything looks good.")
    return 0


def run_console_test(kind: str = "hardware", simulate: bool = False) -> int:
    """Run the hardware or antenna test on the console, without a window."""
    _reconfigure_stdio()
    from PySide6.QtCore import QCoreApplication

    from rtlsdr_diag.sdr.engine import SdrEngine

    QCoreApplication([])                     # signals need an application object
    engine = SdrEngine()
    if simulate:
        engine.set_simulation(True)

    reports: list[dict] = []
    engine.test_progress.connect(lambda m: print("  ... %s" % m, flush=True))
    engine.test_finished.connect(reports.append)
    engine.error_occurred.connect(lambda m: print("  !!! %s" % m, flush=True))

    print("Running %s test..." % kind)
    if kind == "antenna":
        engine.run_antenna_test()
    else:
        engine.run_hardware_test()
    engine.close_device()

    if not reports:
        print("The test produced no report.")
        return 1
    report = reports[0]
    print("")
    for step in report.get("steps", []):
        print("%s  %s" % ("[ OK ]" if step.get("ok") else "[FAIL]", step.get("name")))
        if step.get("detail"):
            print("        %s" % step["detail"])
    print("")
    if report.get("verdict"):
        print("VERDICT: %s  (confidence: %s)"
              % (report["verdict"], report.get("confidence", "?")))
    print(report.get("summary", ""))
    if report.get("disclaimer"):
        print("")
        print("NOTE: " + report["disclaimer"])
    return 0 if report.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RTL-SDR V4 diagnostic and spectrum monitoring tool")
    parser.add_argument("--simulate", action="store_true",
                        help="start in simulation mode (no SDR hardware required)")
    parser.add_argument("--check", action="store_true",
                        help="run an environment self-check and exit")
    parser.add_argument("--hardware-test", action="store_true",
                        help="run the hardware test on the console and exit")
    parser.add_argument("--antenna-test", action="store_true",
                        help="run the antenna check on the console and exit")
    parser.add_argument("--classic", action="store_true",
                        help="open the full diagnostic workbench instead of the "
                             "detector (spectrum, waterfall, scanner, maps)")
    parser.add_argument("--windowed", action="store_true",
                        help="do not start the detector full screen")
    args = parser.parse_args()

    if args.check:
        return run_check()
    if args.hardware_test:
        return run_console_test("hardware", simulate=args.simulate)
    if args.antenna_test:
        return run_console_test("antenna", simulate=args.simulate)

    _reconfigure_stdio()

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication, QMessageBox
    except Exception as exc:
        print("PySide6 is not installed correctly: %s" % exc)
        print("Run:  pip install -r requirements.txt")
        return 1

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("RTL-SDR V4 Diagnostic & Spectrum Monitor")
    app.setOrganizationName("rtlsdr-diag")

    from rtlsdr_diag.ui import theme
    app.setStyle("Fusion")
    app.setStyleSheet(theme.STYLESHEET)
    theme.apply_pyqtgraph_defaults()

    try:
        if args.classic:
            # The full diagnostic workbench, kept for bench work and tests.
            from rtlsdr_diag.ui.main_window import MainWindow
            window = MainWindow(start_simulated=args.simulate)
            window.show()
        else:
            from rtlsdr_diag.ui.app_core import AppCore
            from rtlsdr_diag.ui.detector_window import DetectorWindow
            app.setApplicationName("RF Detector")
            core = AppCore(simulate=args.simulate)
            window = DetectorWindow(core)
            if args.windowed:
                window.resize(960, 640)
                window.show()
            else:
                window.showFullScreen()
            window.settings.fullscreen.setChecked(not args.windowed)
            core.start()
    except Exception as exc:
        traceback.print_exc()
        QMessageBox.critical(None, "Startup failed",
                             "The application could not start:\n\n%s\n\n%s"
                             % (exc, traceback.format_exc()))
        return 1

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
