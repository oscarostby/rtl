"""Spectrum and waterfall plot widgets built on pyqtgraph."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import theme


class SpectrumPlot(QWidget):
    """Live trace + peak hold + noise floor, with a hover readout."""

    hovered = Signal(float, float)      # freq MHz, level dBFS

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Frequency", units="MHz")
        self.plot.setLabel("left", "Relative power", units="dBFS")
        self.plot.showGrid(x=True, y=True, alpha=0.22)
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.setYRange(-110, -10)
        self.plot.getPlotItem().setMenuEnabled(False)
        lay.addWidget(self.plot)

        self.curve = self.plot.plot(pen=pg.mkPen(theme.TRACE, width=1.4), name="Live")
        self.peak_curve = self.plot.plot(
            pen=pg.mkPen(theme.PEAK, width=1.0, style=Qt.DashLine), name="Peak hold")
        self.noise_line = pg.InfiniteLine(
            angle=0, movable=False,
            pen=pg.mkPen(theme.NOISE, width=1.0, style=Qt.DotLine))
        self.plot.addItem(self.noise_line)

        self.vline = pg.InfiniteLine(angle=90, movable=False,
                                     pen=pg.mkPen(theme.ACCENT, width=1))
        self.hline = pg.InfiniteLine(angle=0, movable=False,
                                     pen=pg.mkPen(theme.ACCENT, width=1))
        for item in (self.vline, self.hline):
            item.setVisible(False)
            self.plot.addItem(item, ignoreBounds=True)

        self.readout = pg.TextItem(anchor=(0, 1), color=theme.TEXT)
        self.readout.setFont(QFont("Cascadia Mono, Consolas, monospace", 9))
        self.readout.setVisible(False)
        self.plot.addItem(self.readout, ignoreBounds=True)

        self._peak_hold = True
        self._peak: np.ndarray | None = None
        self._freqs_mhz: np.ndarray | None = None
        self._power: np.ndarray | None = None
        self._autoscale = True
        self._markers: list = []
        self._y_range: tuple[float, float] | None = None
        self._x_range: tuple[float, float] | None = None

        self.plot.scene().sigMouseMoved.connect(self._on_mouse)

    # -- configuration ------------------------------------------------------
    def set_peak_hold(self, enabled: bool) -> None:
        self._peak_hold = bool(enabled)
        self.peak_curve.setVisible(self._peak_hold)
        if not enabled:
            self._peak = None

    def reset_peak(self) -> None:
        self._peak = None
        self.peak_curve.setData([], [])

    def set_autoscale(self, enabled: bool) -> None:
        self._autoscale = bool(enabled)

    def set_y_range(self, lo: float, hi: float) -> None:
        self._y_range = (lo, hi)
        self.plot.setYRange(lo, hi)

    # -- data ---------------------------------------------------------------
    def set_data(self, freqs_hz: np.ndarray, power_db: np.ndarray,
                 noise_db: float | None = None, keep_x: bool = False) -> None:
        f_mhz = freqs_hz / 1e6
        moved = (self._x_range is None or f_mhz.size == 0
                 or abs(float(f_mhz[0]) - self._x_range[0]) > 1e-9
                 or abs(float(f_mhz[-1]) - self._x_range[1]) > 1e-9)
        if moved:
            # Peak hold from another part of the spectrum is meaningless here.
            self._peak = None
        self._freqs_mhz = f_mhz
        self._power = power_db
        self.curve.setData(f_mhz, power_db)

        if self._peak_hold:
            if self._peak is None or self._peak.shape != power_db.shape:
                self._peak = power_db.copy()
            else:
                np.maximum(self._peak, power_db, out=self._peak)
            self.peak_curve.setData(f_mhz, self._peak)

        if noise_db is not None and np.isfinite(noise_db):
            self.noise_line.setPos(noise_db)
            if self._autoscale:
                lo = noise_db - 15.0
                hi = max(float(power_db.max()) + 8.0, noise_db + 35.0)
                # Only rescale on a real change - every setYRange forces a
                # full repaint, which is the most expensive thing here.
                if (self._y_range is None or abs(lo - self._y_range[0]) > 2.0
                        or abs(hi - self._y_range[1]) > 2.0):
                    self._y_range = (lo, hi)
                    self.plot.setYRange(lo, hi, padding=0)
        if not keep_x and f_mhz.size:
            x0, x1 = float(f_mhz[0]), float(f_mhz[-1])
            if self._x_range is None or (abs(x0 - self._x_range[0]) > 1e-9
                                         or abs(x1 - self._x_range[1]) > 1e-9):
                self._x_range = (x0, x1)
                self.plot.setXRange(x0, x1, padding=0)

    def clear_markers(self) -> None:
        for m in self._markers:
            self.plot.removeItem(m)
        self._markers.clear()

    def add_markers(self, freqs_hz, labels=None) -> None:
        """Draw vertical markers at detected carriers."""
        self.clear_markers()
        labels = labels or [None] * len(freqs_hz)
        for f, text in zip(freqs_hz, labels):
            line = pg.InfiniteLine(pos=f / 1e6, angle=90, movable=False,
                                   pen=pg.mkPen(QColor(47, 155, 255, 120), width=1))
            self.plot.addItem(line, ignoreBounds=True)
            self._markers.append(line)
            if text:
                t = pg.TextItem(text, anchor=(0.5, 1.0), color=theme.ACCENT)
                t.setFont(QFont("Segoe UI", 8))
                t.setPos(f / 1e6, self.plot.viewRange()[1][1])
                self.plot.addItem(t, ignoreBounds=True)
                self._markers.append(t)

    # -- hover --------------------------------------------------------------
    def _on_mouse(self, pos) -> None:
        vb = self.plot.getPlotItem().vb
        if not self.plot.sceneBoundingRect().contains(pos):
            for item in (self.vline, self.hline, self.readout):
                item.setVisible(False)
            return
        point = vb.mapSceneToView(pos)
        x_mhz = float(point.x())
        level = float("nan")
        if self._freqs_mhz is not None and self._freqs_mhz.size:
            idx = int(np.argmin(np.abs(self._freqs_mhz - x_mhz)))
            level = float(self._power[idx])
            x_mhz = float(self._freqs_mhz[idx])
        self.vline.setPos(x_mhz)
        self.hline.setPos(level if np.isfinite(level) else point.y())
        self.readout.setText("  %.4f MHz\n  %.1f dBFS" % (x_mhz, level))
        self.readout.setPos(x_mhz, level if np.isfinite(level) else point.y())
        for item in (self.vline, self.hline, self.readout):
            item.setVisible(True)
        self.hovered.emit(x_mhz, level)


class WaterfallPlot(QWidget):
    """Scrolling waterfall: X = frequency, Y = time (newest at the top)."""

    def __init__(self, history: int = 320, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Frequency", units="MHz")
        self.plot.setLabel("left", "Time", units="s")
        self.plot.getPlotItem().setMenuEnabled(False)
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.getPlotItem().getAxis("left").setStyle(tickTextOffset=6)
        lay.addWidget(self.plot)

        self.image = pg.ImageItem()
        self.plot.addItem(self.image)

        stops, colors = theme.waterfall_colormap()
        self.cmap = pg.ColorMap(stops, colors)
        self.image.setLookupTable(self.cmap.getLookupTable(0.0, 1.0, 256))

        self.history = int(history)
        self._buf: np.ndarray | None = None
        self._f0 = 0.0
        self._f1 = 1.0
        self._dt = 0.1
        self._last_t: float | None = None
        self._span_t: float | None = None
        self._rows_filled = 0
        self.floor_offset = -5.0
        self.range_db = 45.0
        self._auto_levels = True

    def set_dynamic_range(self, floor_offset: float, range_db: float) -> None:
        self.floor_offset = float(floor_offset)
        self.range_db = float(range_db)

    def set_auto_levels(self, enabled: bool) -> None:
        self._auto_levels = bool(enabled)

    def clear(self) -> None:
        self._buf = None
        self._span_t = None
        self._rows_filled = 0
        self.image.clear()

    def add_row(self, freqs_hz: np.ndarray, power_db: np.ndarray,
                noise_db: float | None = None, timestamp: float | None = None) -> None:
        n = power_db.size
        f0, f1 = float(freqs_hz[0]) / 1e6, float(freqs_hz[-1]) / 1e6
        if (self._buf is None or self._buf.shape[1] != n
                or abs(f0 - self._f0) > 1e-9 or abs(f1 - self._f1) > 1e-9):
            self._buf = np.full((self.history, n), -120.0, dtype=np.float32)
            self._f0, self._f1 = f0, f1
            self._span_t = None
            self._rows_filled = 0
        self._buf[:-1] = self._buf[1:]
        self._buf[-1] = power_db
        self._rows_filled = min(self.history, self._rows_filled + 1)

        if timestamp is not None:
            if self._last_t is not None:
                dt = timestamp - self._last_t
                if 0.005 < dt < 5.0:
                    self._dt = 0.9 * self._dt + 0.1 * dt
            self._last_t = timestamp

        if noise_db is not None and np.isfinite(noise_db) and self._auto_levels:
            lo = noise_db + self.floor_offset
            hi = lo + self.range_db
        else:
            lo, hi = -100.0, -40.0
        # Show only the rows that hold real data, so the view is filled from
        # the first frame instead of growing out of a mostly empty buffer.
        rows = max(1, self._rows_filled)
        view = self._buf[-rows:]
        self.image.setImage(view, autoLevels=False, levels=(lo, hi))
        span_t = rows * self._dt
        if self._span_t is None or abs(span_t - self._span_t) > 0.05 * max(span_t, 1e-6):
            self._span_t = span_t
            self.image.setRect(QRectF(self._f0, -span_t,
                                      self._f1 - self._f0, span_t))
            self.plot.setXRange(self._f0, self._f1, padding=0)
            self.plot.setYRange(-span_t, 0.0, padding=0)
        else:
            self.image.setRect(QRectF(self._f0, -self._span_t,
                                      self._f1 - self._f0, self._span_t))
