"""Polar radar view: your position at the centre, signals around you.

Deliberately not a street map. There is no map tile source here, and more
importantly a receiver only ever knows *bearing and range* to something - so a
polar plot shows exactly what is known and nothing more. Everything drawn is
either measured, entered by you, or computed from published coordinates.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPen, QPolygonF,
                           QRadialGradient)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.geo import LatLon, bearing_deg, distance_km
from . import theme

KIND_COLORS = {
    "FM": "#f0a020",
    "DAB": "#2f9bff",
    "TV": "#a06cd5",
    "TETRA site": "#35d6a4",
    "Amateur": "#ef4444",
    "Other": "#93a3b5",
}

CARDINALS = [(0, "N"), (45, "NE"), (90, "E"), (135, "SE"),
             (180, "S"), (225, "SW"), (270, "W"), (315, "NW")]


class RadarView(QWidget):
    """Range rings, known transmitters, bearing lines and triangulated fixes."""

    site_clicked = Signal(int)          # index into the site list
    range_changed = Signal(float)       # km

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self.origin = LatLon(59.9139, 10.7522)
        self.range_km = 50.0
        self.sites: list = []
        self.bearings: list = []
        self.fixes: list = []           # list of (LatLon, quality, label)
        self.sweep: list[tuple[float, float]] = []   # (bearing, level_dbfs)
        self.coverage: list = []        # CoveragePoint, strongest drawn last
        self.coverage_range = (-100.0, -20.0)
        # Signals actually being received whose direction is unknown. Drawn on
        # a ring outside the range rings, so their position never implies a
        # distance - only "heard, somewhere out there".
        self.unlocated: list = []       # LiveSignalDetection objects
        self.selected_detection = None
        self.show_labels = True
        self.show_unheard = True
        self._hover_index = -1
        self._hover_pos = QPointF()

    # ------------------------------------------------------------------
    def set_origin(self, lat: float, lon: float) -> None:
        self.origin = LatLon(lat, lon)
        self.update()

    def set_range_km(self, km: float) -> None:
        self.range_km = max(0.5, min(2000.0, float(km)))
        self.update()

    def set_unlocated(self, detections, selected=None) -> None:
        self.unlocated = list(detections)
        self.selected_detection = selected
        self.update()

    def set_coverage(self, points, level_range) -> None:
        self.coverage = points
        self.coverage_range = level_range
        self.update()

    def set_data(self, sites=None, bearings=None, fixes=None, sweep=None) -> None:
        if sites is not None:
            self.sites = sites
        if bearings is not None:
            self.bearings = bearings
        if fixes is not None:
            self.fixes = fixes
        if sweep is not None:
            self.sweep = sweep
        self.update()

    # -- geometry ------------------------------------------------------
    def _centre(self) -> QPointF:
        return QPointF(self.width() / 2.0, self.height() / 2.0)

    def _radius_px(self) -> float:
        return max(20.0, min(self.width(), self.height()) / 2.0 - 34.0)

    def _to_screen(self, bearing: float, distance: float) -> QPointF:
        """Bearing (deg from north, clockwise) + range (km) -> screen point."""
        c = self._centre()
        r = self._radius_px() * (distance / self.range_km)
        ang = math.radians(bearing - 90.0)      # screen x axis points east
        return QPointF(c.x() + r * math.cos(ang), c.y() + r * math.sin(ang))

    def _ring_steps(self) -> list[float]:
        """Pick round-number ring distances that suit the current range."""
        target = self.range_km / 4.0
        nice = [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500]
        step = min(nice, key=lambda v: abs(v - target))
        rings = []
        d = step
        while d <= self.range_km + 1e-9:
            rings.append(d)
            d += step
        return rings

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self._centre()
        radius = self._radius_px()

        p.fillRect(self.rect(), QColor(theme.BG_ELEV))

        # faint glow so the centre reads as "here"
        grad = QRadialGradient(c, radius)
        grad.setColorAt(0.0, QColor(47, 155, 255, 26))
        grad.setColorAt(1.0, QColor(47, 155, 255, 0))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawEllipse(c, radius, radius)

        self._draw_rings(p, c, radius)
        self._draw_coverage(p, c)
        self._draw_sweep(p, c, radius)
        self._draw_bearings(p, c)
        self._draw_fixes(p, c)
        self._draw_sites(p, c)
        self._draw_unlocated(p, c, radius)
        self._draw_centre(p, c)
        self._draw_empty_hint(p, c, radius)
        self._draw_legend(p)
        self._draw_hover(p)
        p.end()

    def _draw_unlocated(self, p: QPainter, c: QPointF, radius: float) -> None:
        """Live detections with no direction, on a neutral outer ring.

        Position on this ring means nothing at all. Markers are spaced evenly
        and ordered by frequency purely so they stay put between refreshes -
        the angle is not frequency, not strength, and not direction, and the
        ring sits outside the range rings so its radius implies no distance.
        The label says so, because a circle invites exactly that assumption.
        """
        if not self.unlocated:
            return
        ring = radius + 16.0
        items = sorted(self.unlocated, key=lambda d: d.freq_hz)
        n = len(items)

        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, ring, ring)

        p.setFont(QFont("Segoe UI", 7))
        for i, det in enumerate(items):
            # Evenly spaced: carries no information, and is meant not to.
            ang = math.radians((360.0 * i / max(n, 1)) - 90.0)
            pt = QPointF(c.x() + ring * math.cos(ang), c.y() + ring * math.sin(ang))

            snr = det.snr_db if det.snr_db == det.snr_db else 0.0
            strength = max(0.0, min(1.0, snr / 40.0))
            col = QColor(int(60 + 195 * strength), int(200 - 40 * strength),
                         int(200 - 150 * strength), 240)
            size = 3.0 + 4.0 * strength
            if det is self.selected_detection:
                p.setPen(QPen(QColor(theme.TEXT), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, size + 5, size + 5)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(col))
            p.drawEllipse(pt, size, size)

            if self.show_labels:
                out = QPointF(c.x() + (ring + 15) * math.cos(ang),
                              c.y() + (ring + 15) * math.sin(ang))
                p.setPen(QPen(col))
                align = Qt.AlignLeft if math.cos(ang) >= 0 else Qt.AlignRight
                box_x = out.x() if math.cos(ang) >= 0 else out.x() - 86
                p.drawText(QRectF(box_x, out.y() - 7, 86, 14),
                           align | Qt.AlignVCenter,
                           "%.3f" % (det.freq_hz / 1e6))

        p.setPen(QPen(QColor(theme.WARN)))
        p.setFont(QFont("Segoe UI", 8, QFont.Bold))
        p.drawText(QRectF(6, 4, 520, 15), Qt.AlignLeft | Qt.AlignVCenter,
                   "UNLOCATED SIGNALS - DIRECTION UNKNOWN  (%d live; ring "
                   "position carries no meaning)" % n)

    def _draw_legend(self, p: QPainter) -> None:
        entries = [
            ("LIVE SIGNAL", theme.TRACE, "ring"),
            ("KNOWN SITE", theme.ACCENT, "dot"),
            ("BEARING", theme.PEAK, "line"),
            ("ESTIMATED FIX", theme.GOOD, "cross"),
        ]
        p.setFont(QFont("Segoe UI", 8))
        fm = p.fontMetrics()
        y = self.height() - 20.0
        x = 10.0
        for label, colour, shape in entries:
            col = QColor(colour)
            p.setPen(QPen(col, 2))
            p.setBrush(QBrush(col))
            if shape == "line":
                p.setBrush(Qt.NoBrush)
                p.drawLine(QPointF(x, y), QPointF(x + 14, y))
            elif shape == "cross":
                p.setBrush(Qt.NoBrush)
                p.drawLine(QPointF(x, y - 5), QPointF(x + 10, y + 5))
                p.drawLine(QPointF(x, y + 5), QPointF(x + 10, y - 5))
            elif shape == "ring":
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(x + 6, y), 5, 5)
            else:
                p.drawEllipse(QPointF(x + 6, y), 4, 4)
            x += 20
            p.setPen(QPen(QColor(theme.TEXT_DIM)))
            p.drawText(QRectF(x, y - 8, 130, 16),
                       Qt.AlignLeft | Qt.AlignVCenter, label)
            x += fm.horizontalAdvance(label) + 22

    def _draw_empty_hint(self, p: QPainter, c: QPointF, radius: float) -> None:
        """Say why the screen is empty, rather than looking broken.

        The radar plots reference data and hand-taken readings - it does not
        scan for transmitters, and cannot: a single antenna has no sense of
        direction. Without that said plainly, an empty radar reads as a fault.
        """
        has_spatial = bool(self.sites or self.bearings or self.fixes
                           or self.coverage)
        if has_spatial:
            return
        if self.unlocated:
            # Live RF, but nothing that can be placed on the map.
            lines = [
                "RF detected",
                "Position unknown",
                "",
                "A single antenna provides signal strength, not direction.",
                "Signals are listed on the outer ring and in the table.",
            ]
            p.setFont(QFont("Segoe UI", 10))
            fm = p.fontMetrics()
            w = max(fm.horizontalAdvance(t) for t in lines) + 40
            h = fm.height() * len(lines) + 24
            box = QRectF(c.x() - w / 2.0, c.y() - h / 2.0, w, h)
            p.setPen(QPen(QColor(theme.BORDER_STRONG)))
            p.setBrush(QBrush(QColor(20, 27, 36, 235)))
            p.drawRoundedRect(box, 8, 8)
            for i, line in enumerate(lines):
                bold = i == 0
                p.setFont(QFont("Segoe UI", 11 if bold else 9,
                                QFont.Bold if bold else QFont.Normal))
                p.setPen(QPen(QColor(theme.TEXT if i < 2 else theme.TEXT_DIM)))
                p.drawText(QRectF(box.left(), box.top() + 12 + i * fm.height(),
                                  box.width(), fm.height()),
                           Qt.AlignCenter, line)
            return
        lines = [
            "Nothing to plot yet.",
            "",
            "Start a scan (Scanner or TETRA RF Check) and every signal you",
            "receive appears on the outer ring straight away.",
            "",
            "To place things on the map itself, the radar needs position data,",
            "because one antenna cannot tell direction on its own:",
            "",
            "Known transmitters   Load CSV, or Write template for the format.",
            "Bearing lines        Point a directional antenna, type the bearing,",
            "                     press Record bearing. Two of them cross at a fix.",
            "Coverage survey      Press Record here while receiving, move, repeat.",
        ]
        p.setFont(QFont("Consolas", 9))
        fm = p.fontMetrics()
        w = max(fm.horizontalAdvance(t) for t in lines) + 34
        h = fm.height() * len(lines) + 26
        box = QRectF(c.x() - w / 2.0, c.y() + radius * 0.18, w, h)
        if box.bottom() > self.height() - 6:
            box.moveTop(max(6.0, self.height() - h - 6))
        p.setPen(QPen(QColor(theme.BORDER_STRONG)))
        p.setBrush(QBrush(QColor(20, 27, 36, 235)))
        p.drawRoundedRect(box, 8, 8)
        for i, line in enumerate(lines):
            p.setPen(QPen(QColor(theme.TEXT if i == 0 else theme.TEXT_DIM)))
            p.drawText(QRectF(box.left() + 16, box.top() + 12 + i * fm.height(),
                              box.width() - 32, fm.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, line)

    def _draw_rings(self, p: QPainter, c: QPointF, radius: float) -> None:
        font = QFont("Segoe UI", 8)
        p.setFont(font)
        for d in self._ring_steps():
            r = radius * (d / self.range_km)
            p.setPen(QPen(QColor(theme.BORDER), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r, r)
            p.setPen(QPen(QColor(theme.TEXT_FAINT)))
            label = ("%g km" % d) if d >= 1 else ("%g m" % (d * 1000))
            p.drawText(QRectF(c.x() + 4, c.y() - r - 14, 70, 14),
                       Qt.AlignLeft | Qt.AlignVCenter, label)

        for deg, name in CARDINALS:
            ang = math.radians(deg - 90.0)
            outer = QPointF(c.x() + radius * math.cos(ang),
                            c.y() + radius * math.sin(ang))
            major = deg % 90 == 0
            p.setPen(QPen(QColor(theme.BORDER_STRONG if major else theme.BORDER),
                          1, Qt.SolidLine if major else Qt.DotLine))
            p.drawLine(c, outer)
            lp = QPointF(c.x() + (radius + 16) * math.cos(ang),
                         c.y() + (radius + 16) * math.sin(ang))
            p.setPen(QPen(QColor(theme.TEXT_DIM if major else theme.TEXT_FAINT)))
            f = QFont("Segoe UI", 9 if major else 8)
            f.setBold(major)
            p.setFont(f)
            p.drawText(QRectF(lp.x() - 16, lp.y() - 9, 32, 18), Qt.AlignCenter, name)
        p.setFont(font)

    def _draw_sweep(self, p: QPainter, c: QPointF, radius: float) -> None:
        """Signal strength versus bearing, as a filled rose."""
        if len(self.sweep) < 3:
            return
        levels = [lv for _, lv in self.sweep]
        lo, hi = min(levels), max(levels)
        span = max(hi - lo, 1e-6)
        poly = QPolygonF()
        for brg, lv in sorted(self.sweep, key=lambda t: t[0]):
            frac = 0.15 + 0.85 * ((lv - lo) / span)
            ang = math.radians(brg - 90.0)
            r = radius * frac
            poly.append(QPointF(c.x() + r * math.cos(ang), c.y() + r * math.sin(ang)))
        if poly.count() > 2:
            poly.append(poly.at(0))
        p.setPen(QPen(QColor(53, 214, 164, 190), 2))
        p.setBrush(QBrush(QColor(53, 214, 164, 45)))
        p.drawPolygon(poly)

    def _draw_coverage(self, p: QPainter, c: QPointF) -> None:
        """Every place you measured from, coloured by how strong it was there."""
        if not self.coverage:
            return
        lo, hi = self.coverage_range
        span = max(hi - lo, 1e-6)
        p.setPen(Qt.NoPen)
        for point in self.coverage:
            pos = point.position()
            d = distance_km(self.origin, pos)
            if d > self.range_km * 1.02:
                continue
            b = bearing_deg(self.origin, pos) if d > 1e-9 else 0.0
            pt = self._to_screen(b, d)
            frac = max(0.0, min(1.0, (point.level_dbfs - lo) / span))
            # weak -> blue, mid -> green, strong -> amber/red
            if frac < 0.5:
                t = frac / 0.5
                col = QColor(int(30 + 10 * t), int(70 + 140 * t), int(160 - 30 * t), 210)
            else:
                t = (frac - 0.5) / 0.5
                col = QColor(int(40 + 215 * t), int(210 - 40 * t), int(130 - 100 * t), 220)
            p.setBrush(QBrush(col))
            p.drawEllipse(pt, 4.5, 4.5)

    def _draw_bearings(self, p: QPainter, c: QPointF) -> None:
        radius = self._radius_px()
        for rec in self.bearings:
            here = LatLon(rec.lat, rec.lon)
            # Where the observer stood, relative to the radar centre.
            off_d = distance_km(self.origin, here)
            off_b = bearing_deg(self.origin, here) if off_d > 1e-9 else 0.0
            start = self._to_screen(off_b, off_d) if off_d > 1e-9 else QPointF(c)
            ang = math.radians(rec.bearing_deg - 90.0)
            end = QPointF(start.x() + radius * 2.2 * math.cos(ang),
                          start.y() + radius * 2.2 * math.sin(ang))
            p.setPen(QPen(QColor(240, 160, 32, 170), 1.6, Qt.DashLine))
            p.drawLine(start, end)
            p.setPen(QPen(QColor(theme.PEAK), 1))
            p.setBrush(QBrush(QColor(theme.PEAK)))
            p.drawEllipse(start, 3.0, 3.0)

    def _draw_fixes(self, p: QPainter, c: QPointF) -> None:
        colors = {"good": theme.GOOD, "fair": theme.WARN, "poor": theme.BAD}
        for point, quality, label in self.fixes:
            d = distance_km(self.origin, point)
            if d > self.range_km * 1.05:
                continue
            b = bearing_deg(self.origin, point)
            pt = self._to_screen(b, d)
            col = QColor(colors.get(quality, theme.TEXT_DIM))
            p.setPen(QPen(col, 2))
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(pt.x() - 7, pt.y()), QPointF(pt.x() + 7, pt.y()))
            p.drawLine(QPointF(pt.x(), pt.y() - 7), QPointF(pt.x(), pt.y() + 7))
            p.drawEllipse(pt, 9.0, 9.0)
            if self.show_labels and label:
                p.setPen(QPen(col))
                p.setFont(QFont("Segoe UI", 8))
                # Sit below the cross: site labels already occupy the line to
                # the right of a marker, and a fix is usually next to a site.
                p.drawText(QRectF(pt.x() - 95, pt.y() + 12, 190, 16),
                           Qt.AlignHCenter | Qt.AlignVCenter, label)

    def _draw_sites(self, p: QPainter, c: QPointF) -> None:
        for i, site in enumerate(self.sites):
            d = site.distance_from(self.origin)
            if d > self.range_km * 1.02:
                continue
            heard = site.is_heard()
            if not heard and not self.show_unheard:
                continue
            b = site.bearing_from(self.origin)
            pt = self._to_screen(b, d)
            col = QColor(KIND_COLORS.get(site.kind, KIND_COLORS["Other"]))

            if heard:
                # A halo scaled by SNR: stronger signal, bigger ring.
                snr = site.snr_db if site.snr_db == site.snr_db else 0.0
                halo = 8.0 + max(0.0, min(40.0, snr)) * 0.7
                glow = QColor(col)
                glow.setAlpha(45)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(glow))
                p.drawEllipse(pt, halo, halo)

            p.setPen(QPen(col, 2 if heard else 1))
            p.setBrush(QBrush(col if heard else QColor(0, 0, 0, 0)))
            size = 5.0 if heard else 4.0
            p.drawEllipse(pt, size, size)

            if i == self._hover_index:
                p.setPen(QPen(QColor(theme.TEXT), 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, size + 6, size + 6)

            if self.show_labels:
                p.setPen(QPen(QColor(theme.TEXT if heard else theme.TEXT_FAINT)))
                p.setFont(QFont("Segoe UI", 8))
                text = site.name
                if heard and site.snr_db == site.snr_db:
                    text += "  %.0f dB" % site.snr_db
                p.drawText(QRectF(pt.x() + 9, pt.y() - 8, 200, 16),
                           Qt.AlignLeft | Qt.AlignVCenter, text)

    def _draw_centre(self, p: QPainter, c: QPointF) -> None:
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        p.setBrush(QBrush(QColor(theme.ACCENT)))
        p.drawEllipse(c, 4.0, 4.0)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, 10.0, 10.0)
        p.setPen(QPen(QColor(theme.TEXT_DIM)))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(c.x() + 13, c.y() + 2, 220, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, "you  %s" % self.origin)

    def _draw_hover(self, p: QPainter) -> None:
        if not (0 <= self._hover_index < len(self.sites)):
            return
        site = self.sites[self._hover_index]
        d = site.distance_from(self.origin)
        b = site.bearing_from(self.origin)
        lines = [
            site.name,
            "%s   %.4f MHz" % (site.kind, site.freq_hz / 1e6),
            "bearing %.1f deg   range %.1f km" % (b, d),
        ]
        if site.is_heard() and site.snr_db == site.snr_db:
            lines.append("measured %.1f dBFS   SNR %.1f dB"
                         % (site.level_dbfs, site.snr_db))
        else:
            lines.append("not currently being received")
        if site.height_m > 0:
            lines.append("mast %.0f m   radio horizon ~%.0f km"
                         % (site.height_m, site.horizon_km()))

        p.setFont(QFont("Consolas", 8))
        fm = p.fontMetrics()
        w = max(fm.horizontalAdvance(t) for t in lines) + 16
        h = fm.height() * len(lines) + 12
        x = min(self._hover_pos.x() + 14, self.width() - w - 4)
        y = min(self._hover_pos.y() + 14, self.height() - h - 4)
        box = QRectF(x, y, w, h)
        p.setPen(QPen(QColor(theme.BORDER_STRONG)))
        p.setBrush(QBrush(QColor(20, 27, 36, 240)))
        p.drawRoundedRect(box, 6, 6)
        p.setPen(QPen(QColor(theme.TEXT)))
        for i, line in enumerate(lines):
            p.drawText(QRectF(x + 8, y + 6 + i * fm.height(), w - 16, fm.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, line)

    # -- interaction ---------------------------------------------------
    def _site_at(self, pos: QPointF) -> int:
        best, best_d = -1, 12.0
        for i, site in enumerate(self.sites):
            d = site.distance_from(self.origin)
            if d > self.range_km * 1.02:
                continue
            pt = self._to_screen(site.bearing_from(self.origin), d)
            dist = math.hypot(pt.x() - pos.x(), pt.y() - pos.y())
            if dist < best_d:
                best, best_d = i, dist
        return best

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        idx = self._site_at(pos)
        if idx != self._hover_index or idx >= 0:
            self._hover_index = idx
            self._hover_pos = pos
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_index = -1
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        idx = self._site_at(event.position())
        if idx >= 0:
            self.site_clicked.emit(idx)

    def wheelEvent(self, event) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.set_range_km(self.range_km * (0.8 ** steps))
            self.range_changed.emit(self.range_km)
