"""CSV logging of signal detections."""
from __future__ import annotations

import csv
import datetime as _dt
import threading
from pathlib import Path

HEADER = [
    "timestamp",
    "frequency_hz",
    "frequency_mhz",
    "signal_dbfs",
    "noise_floor_dbfs",
    "snr_db",
    "estimated_bandwidth_hz",
    "source",
]


class CsvLogger:
    """Append-only CSV writer, safe to call from several threads."""

    def __init__(self) -> None:
        self._fh = None
        self._writer = None
        self._lock = threading.Lock()
        self.path: Path | None = None
        self.rows_written = 0

    @property
    def is_logging(self) -> bool:
        return self._fh is not None

    def start(self, path: str | Path) -> Path:
        self.stop()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        new_file = not p.exists() or p.stat().st_size == 0
        with self._lock:
            self._fh = open(p, "a", newline="", encoding="utf-8")
            self._writer = csv.writer(self._fh)
            if new_file:
                self._writer.writerow(HEADER)
                self._fh.flush()
            self.path = p
            self.rows_written = 0
        return p

    def stop(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                    self._fh.close()
                except Exception:
                    pass
            self._fh = None
            self._writer = None

    def log(self, freq_hz: float, signal_dbfs: float, noise_dbfs: float,
            snr_db: float, bandwidth_hz: float, source: str = "") -> None:
        with self._lock:
            if self._writer is None:
                return
            ts = _dt.datetime.now().isoformat(timespec="milliseconds")
            self._writer.writerow([
                ts,
                "%.0f" % freq_hz,
                "%.6f" % (freq_hz / 1e6),
                "%.2f" % signal_dbfs,
                "%.2f" % noise_dbfs,
                "%.2f" % snr_db,
                "%.0f" % bandwidth_hz,
                source,
            ])
            self.rows_written += 1
            if self.rows_written % 20 == 0:
                try:
                    self._fh.flush()
                except Exception:
                    pass

    def flush(self) -> None:
        """Force pending rows to disk.

        Rare, important rows should not sit in a buffer waiting for the file to
        be closed - a drive that ends with the laptop being shut off would
        otherwise lose them.
        """
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                except Exception:
                    pass

    def default_filename(self, prefix: str = "rtlsdr_signals") -> str:
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        return "%s_%s.csv" % (prefix, stamp)
