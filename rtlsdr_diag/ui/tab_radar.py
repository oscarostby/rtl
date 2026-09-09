"""RF Monitor (shown as "Radar"): live signals, reference sites, DF, coverage.

Four layers, deliberately kept apart because they have very different standing:

A. LIVE SIGNALS       measured now. Frequency/level/SNR only - no position.
B. KNOWN TRANSMITTERS reference data the operator supplied. Has position.
C. MANUAL DF          bearings the operator read off a directional antenna.
D. COVERAGE SURVEY    levels recorded at the operator's own positions.

Only B, C and D can be drawn on the map. A is shown on a neutral outer ring
and in the table, because a single antenna measures strength, not direction.

This page never opens the receiver itself. It subscribes to the shared signal
store and asks the Scanner tab to start acquisition, so only one component ever
owns the RTL-SDR.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import settings
from ..core.geo import LatLon, angle_between, distance_km, triangulate
from ..core.models import BearingMeasurement, CoverageMeasurement
from ..core.sites import (BearingRecord, CoverageLog, CoveragePoint, Site,
                          SiteStore, write_template)
from . import theme, widgets
from .live_table import LiveDetectionPanel
from .radar import KIND_COLORS, RadarView
from .status import SdrStatusStrip

RANGE_PRESETS_KM = [1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]
DEFAULT_RANGE_KM = 10.0

HELP_TEXT = (
    "1.  The Scanner (or TETRA RF Check) sweeps a range and finds RF energy.\n"
    "2.  Each detection is classified from its frequency and occupied bandwidth.\n"
    "3.  A normal RTL-SDR measures frequency, level and SNR - nothing else.\n"
    "4.  One antenna gives no direction, so live signals cannot be placed on a map.\n"
    "5.  Direction comes only from a directional antenna you rotate and read off.\n"
    "6.  Geographic positions come from known-site data, or from your own bearings.\n"
    "7.  Signal strength is not distance: transmit power, antennas, terrain,\n"
    "    multipath and AGC all change it independently of range."
)


class RadarTab(QWidget):
    tab_label = "Radar"

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.store = SiteStore()
        self.coverage = CoverageLog()
        self._fixes: list = []
        self._sweep: list[tuple[float, float]] = []
        self._selected = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # --- acquisition status -----------------------------------------
        self.status_strip = SdrStatusStrip("Start Scanner")
        self.status_strip.start_requested.connect(self._start_scanner)
        self.status_strip.stop_requested.connect(self.app.engine_stop)
        root.addWidget(self.status_strip)

        # --- collapsible help -------------------------------------------
        head = QHBoxLayout()
        self.btn_help = QPushButton("How this works")
        self.btn_help.setCheckable(True)
        self.btn_help.toggled.connect(self._toggle_help)
        head.addWidget(self.btn_help)
        head.addStretch(1)
        root.addLayout(head)

        self.help_box = QLabel(HELP_TEXT)
        self.help_box.setWordWrap(True)
        self.help_box.setVisible(False)
        self.help_box.setStyleSheet(
            "background: rgba(47,155,255,0.08); border: 1px solid %s; "
            "border-radius: 8px; padding: 10px; color: %s; font-size: 12px; "
            "font-family: Consolas, monospace;" % (theme.ACCENT_DIM, theme.TEXT))
        root.addWidget(self.help_box)

        root.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Horizontal)
        left = QSplitter(Qt.Vertical)
        self.radar = RadarView()
        self.radar.site_clicked.connect(self._select_site)
        self.radar.range_changed.connect(self._range_from_wheel)
        left.addWidget(self.radar)

        live_box = widgets.group("Live RF detections")
        self.live_panel = LiveDetectionPanel()
        self.live_panel.selection_changed.connect(self._on_live_selected)
        self.live_panel.tune_requested.connect(self._tune_from_live)
        self.live_panel.clear_requested.connect(self._clear_live)
        live_box.layout().addWidget(self.live_panel)
        left.addWidget(live_box)
        left.setSizes([400, 320])
        splitter.addWidget(left)

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QScrollArea.NoFrame)
        side_scroll.setWidget(self._build_side_panel())
        side_scroll.setMinimumWidth(430)
        splitter.addWidget(side_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([820, 470])
        root.addWidget(splitter, 1)

        self.status = widgets.dim_label("")
        root.addWidget(self.status)

        # The radar widget exists by now, so the remembered range can be applied.
        self._apply_range(settings.radar_range_km(DEFAULT_RANGE_KM),
                          remember=False)

        # Subscribe to the shared store rather than scanning ourselves.
        self.app.signal_store.subscribe(self._on_detections)
        self.app.register_status_listener(self._on_status)
        self._refresh_all()
        self._tick = QTimer(self)
        self._tick.setInterval(1500)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        lat, lon = settings.observer_position()
        bar.addWidget(QLabel("Your position"))
        self.lat = QDoubleSpinBox()
        self.lat.setRange(-90.0, 90.0)
        self.lat.setDecimals(5)
        self.lat.setValue(lat)
        self.lat.setPrefix("lat ")
        self.lat.setMinimumWidth(118)
        self.lat.valueChanged.connect(self._origin_changed)
        bar.addWidget(self.lat)

        self.lon = QDoubleSpinBox()
        self.lon.setRange(-180.0, 180.0)
        self.lon.setDecimals(5)
        self.lon.setValue(lon)
        self.lon.setPrefix("lon ")
        self.lon.setMinimumWidth(118)
        self.lon.valueChanged.connect(self._origin_changed)
        bar.addWidget(self.lon)

        bar.addWidget(QLabel("Range"))
        self.range_box = QComboBox()
        for km in RANGE_PRESETS_KM:
            self.range_box.addItem("%g km" % km, km)
        self.range_box.addItem("Custom...", None)
        self.range_box.currentIndexChanged.connect(self._range_preset_changed)
        bar.addWidget(self.range_box)

        self.range_custom = QDoubleSpinBox()
        self.range_custom.setRange(0.2, 2000.0)
        self.range_custom.setDecimals(1)
        self.range_custom.setSuffix(" km")
        self.range_custom.setValue(DEFAULT_RANGE_KM)
        self.range_custom.setVisible(False)
        self.range_custom.valueChanged.connect(self._range_custom_changed)
        bar.addWidget(self.range_custom)

        self.chk_labels = QCheckBox("Labels")
        self.chk_labels.setChecked(True)
        self.chk_labels.toggled.connect(self._toggle_labels)
        bar.addWidget(self.chk_labels)

        self.chk_unheard = QCheckBox("Show silent sites")
        self.chk_unheard.setChecked(True)
        self.chk_unheard.toggled.connect(self._toggle_unheard)
        bar.addWidget(self.chk_unheard)

        bar.addStretch(1)
        return bar

    # ------------------------------------------------------------------
    # Side panel
    # ------------------------------------------------------------------
    def _build_side_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # --- B. known transmitters ---------------------------------------
        sites_box = widgets.group("Known transmitters  (reference data)")
        row = QHBoxLayout()
        for text, slot in (("Load CSV", self._load_sites),
                           ("Save CSV", self._save_sites),
                           ("Write template", self._write_template),
                           ("Add here", self._add_site),
                           ("Remove", self._remove_site)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        sites_box.layout().addWidget(holder)

        tol_row = QHBoxLayout()
        tol_row.addWidget(QLabel("Frequency match tolerance"))
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.1, 2000.0)
        self.tolerance.setDecimals(1)
        self.tolerance.setSuffix(" kHz")
        self.tolerance.setValue(settings.frequency_match_tolerance_hz() / 1e3)
        self.tolerance.setToolTip(
            "How close a live detection must be to a known site's frequency "
            "before the site is flagged as a possible match.")
        self.tolerance.valueChanged.connect(self._tolerance_changed)
        tol_row.addWidget(self.tolerance)
        tol_row.addStretch(1)
        th = QWidget()
        th.setLayout(tol_row)
        sites_box.layout().addWidget(th)

        self.site_table = QTableWidget(0, 7)
        self.site_table.setHorizontalHeaderLabels(
            ["Name", "Kind", "MHz", "Bearing", "Range", "Level", "Match"])
        self.site_table.setAlternatingRowColors(True)
        self.site_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.site_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.site_table.verticalHeader().setVisible(False)
        self.site_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.site_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self.site_table.setMinimumHeight(170)
        self.site_table.doubleClicked.connect(self._tune_to_selected_site)
        sites_box.layout().addWidget(self.site_table)
        self.lbl_sites_hint = widgets.dim_label("")
        sites_box.layout().addWidget(self.lbl_sites_hint)
        lay.addWidget(sites_box)

        # --- C. manual direction finding ---------------------------------
        df_box = widgets.group("Manual direction finding")
        df_box.layout().addWidget(widgets.dim_label(
            "For fixed transmitters, test transmitters, and transmitters you "
            "are authorised to locate."))
        df_row = QHBoxLayout()
        df_row.addWidget(QLabel("Bearing"))
        self.bearing = QDoubleSpinBox()
        self.bearing.setRange(0.0, 359.9)
        self.bearing.setDecimals(1)
        self.bearing.setSuffix(" deg")
        self.bearing.setToolTip("True bearing the antenna pointed when the "
                                "signal peaked.")
        df_row.addWidget(self.bearing)
        self.btn_bearing = QPushButton("Record bearing")
        self.btn_bearing.setProperty("accent", True)
        self.btn_bearing.clicked.connect(self._record_bearing)
        df_row.addWidget(self.btn_bearing)
        self.btn_fix = QPushButton("Estimate Intersection")
        self.btn_fix.setEnabled(False)
        self.btn_fix.clicked.connect(self._triangulate)
        df_row.addWidget(self.btn_fix)
        b = QPushButton("Clear")
        b.clicked.connect(self._clear_bearings)
        df_row.addWidget(b)
        df_row.addStretch(1)
        dfh = QWidget()
        dfh.setLayout(df_row)
        df_box.layout().addWidget(dfh)

        self.bearing_table = QTableWidget(0, 5)
        self.bearing_table.setHorizontalHeaderLabels(
            ["From", "Bearing", "MHz", "Level", "Gain"])
        self.bearing_table.setAlternatingRowColors(True)
        self.bearing_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.bearing_table.verticalHeader().setVisible(False)
        self.bearing_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.bearing_table.setMaximumHeight(130)
        df_box.layout().addWidget(self.bearing_table)
        self.lbl_geometry = widgets.dim_label("")
        df_box.layout().addWidget(self.lbl_geometry)
        lay.addWidget(df_box)

        # --- signal strength vs bearing ----------------------------------
        rose_box = widgets.group("Signal strength vs bearing")
        srow = QHBoxLayout()
        b = QPushButton("Record current level")
        b.clicked.connect(self._add_sweep_point)
        srow.addWidget(b)
        b = QPushButton("Clear rose")
        b.clicked.connect(self._clear_sweep)
        srow.addWidget(b)
        srow.addStretch(1)
        sh = QWidget()
        sh.setLayout(srow)
        rose_box.layout().addWidget(sh)
        self.lbl_rose = widgets.dim_label("")
        rose_box.layout().addWidget(self.lbl_rose)
        lay.addWidget(rose_box)

        # --- D. coverage survey -------------------------------------------
        cov_box = widgets.group("Coverage survey")
        crow = QHBoxLayout()
        self.btn_cov_add = QPushButton("Record here")
        self.btn_cov_add.setProperty("accent", True)
        self.btn_cov_add.clicked.connect(self._record_coverage)
        crow.addWidget(self.btn_cov_add)
        for text, slot in (("Save CSV", self._save_coverage),
                           ("Load CSV", self._load_coverage),
                           ("Clear", self._clear_coverage)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            crow.addWidget(b)
        crow.addStretch(1)
        ch = QWidget()
        ch.setLayout(crow)
        cov_box.layout().addWidget(ch)
        warn = QLabel(
            "A coverage survey maps WHERE RECEPTION WAS STRONG - not where the "
            "transmitter is. Use a fixed manual gain: levels taken at different "
            "gains are not comparable, so the gain is stored with every point "
            "and a mismatch is flagged below.")
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "background: rgba(240,160,32,0.08); border: 1px solid %s; "
            "border-radius: 8px; padding: 8px; color: %s; font-size: 11px;"
            % (theme.WARN, theme.TEXT))
        cov_box.layout().addWidget(warn)
        self.lbl_coverage = widgets.dim_label("")
        cov_box.layout().addWidget(self.lbl_coverage)
        lay.addWidget(cov_box)

        legend = QLabel("  ".join(
            "<span style='color:%s'>&#9679; %s</span>" % (col, kind)
            for kind, col in KIND_COLORS.items()))
        legend.setStyleSheet("font-size: 11px;")
        lay.addWidget(legend)
        lay.addStretch(1)
        return panel

    # ------------------------------------------------------------------
    # Status / live data
    # ------------------------------------------------------------------
    def _on_status(self, status) -> None:
        self.status_strip.update_status(status)

    def _start_scanner(self) -> None:
        """Hand acquisition to the Scanner - never open a second receiver."""
        self.app.start_default_scan()

    def _on_detections(self, detections) -> None:
        self.live_panel.set_detections(detections)
        timeout = self.live_panel.stale_timeout_s()
        active = [d for d in detections if d.is_active(timeout)]
        self.radar.set_unlocated(active or list(detections), self._selected)
        self._update_site_matches(detections)
        self._refresh_tables()

    def _on_tick(self) -> None:
        self.live_panel.refresh()
        self.radar.update()

    def _on_live_selected(self, detection) -> None:
        self._selected = detection
        self.radar.set_unlocated(self.radar.unlocated, detection)
        if detection is None:
            self.status.setText("")
            return
        ppm = self.app.acquisition_status().ppm
        self.status.setText(
            "Monitoring %s     measured %.4f MHz, corrected %.4f MHz at %+d ppm"
            % (detection.describe(), detection.frequency_mhz,
               self._corrected_mhz(detection.freq_hz), ppm))

    def _corrected_mhz(self, freq_hz: float) -> float:
        """The frequency after the operator's PPM correction is applied."""
        ppm = self.app.acquisition_status().ppm
        return (freq_hz * (1.0 + ppm / 1e6)) / 1e6

    def _tune_from_live(self, freq_hz: float) -> None:
        self.app.tune_to(freq_hz)

    def _clear_live(self) -> None:
        self.app.signal_store.clear(notify=True)
        self._selected = None
        self.status.setText("Live detections cleared.")

    def selected_frequency_hz(self):
        if self._selected is not None:
            return self._selected.freq_hz
        return self.app.current_center_hz()

    # ------------------------------------------------------------------
    # Range / position
    # ------------------------------------------------------------------
    def _apply_range(self, km: float, remember: bool = True) -> None:
        self.radar.set_range_km(km)
        idx = self.range_box.findData(km)
        self.range_box.blockSignals(True)
        if idx >= 0:
            self.range_box.setCurrentIndex(idx)
            self.range_custom.setVisible(False)
        else:
            self.range_box.setCurrentIndex(self.range_box.count() - 1)
            self.range_custom.blockSignals(True)
            self.range_custom.setValue(km)
            self.range_custom.blockSignals(False)
            self.range_custom.setVisible(True)
        self.range_box.blockSignals(False)
        if remember:
            settings.set_radar_range_km(km)

    def _range_preset_changed(self) -> None:
        km = self.range_box.currentData()
        if km is None:
            self.range_custom.setVisible(True)
            self._apply_range(float(self.range_custom.value()))
        else:
            self.range_custom.setVisible(False)
            self._apply_range(float(km))
        self._refresh_tables()

    def _range_custom_changed(self, km: float) -> None:
        if self.range_box.currentData() is None:
            self._apply_range(float(km))

    def _range_from_wheel(self, km: float) -> None:
        self._apply_range(km)

    def _origin_changed(self) -> None:
        settings.set_observer_position(self.lat.value(), self.lon.value())
        self.radar.set_origin(self.lat.value(), self.lon.value())
        self._refresh_tables()

    def _toggle_labels(self, on: bool) -> None:
        self.radar.show_labels = on
        self.radar.update()

    def _toggle_unheard(self, on: bool) -> None:
        self.radar.show_unheard = on
        self.radar.update()

    def _toggle_help(self, shown: bool) -> None:
        self.help_box.setVisible(shown)

    def here(self) -> LatLon:
        return LatLon(self.lat.value(), self.lon.value())

    def _tolerance_changed(self, khz: float) -> None:
        settings.set_frequency_match_tolerance_hz(khz * 1e3)
        self._update_site_matches(self.app.signal_store.items())
        self._refresh_tables()

    # ------------------------------------------------------------------
    # Known transmitters
    # ------------------------------------------------------------------
    def _update_site_matches(self, detections) -> None:
        """Flag sites a live detection sits close to in frequency.

        A frequency match is exactly that. It is not evidence the signal came
        from that site, so the column says "possible" and nothing stronger.
        """
        tol = self.tolerance.value() * 1e3
        for site in self.store.sites:
            site.level_dbfs = float("nan")
            site.snr_db = float("nan")
            best = None
            for det in detections:
                if site.matches_frequency(det.freq_hz, tol):
                    if best is None or det.snr_db > best.snr_db:
                        best = det
            if best is not None:
                site.level_dbfs = best.level_dbfs
                site.snr_db = best.snr_db
                site.last_heard = best.last_seen

    def _load_sites(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load transmitter sites", str(Path.cwd()), "CSV files (*.csv)")
        if not path:
            return
        try:
            count, warnings = self.store.load_sites(path)
        except OSError as exc:
            QMessageBox.warning(self, "Could not read file", str(exc))
            return
        self.status.setText("Loaded %d site(s) from %s." % (count, Path(path).name))
        if warnings:
            QMessageBox.information(
                self, "Some rows were skipped",
                "These rows could not be used:" + chr(10) + chr(10)
                + chr(10).join(warnings[:15]))
        self._refresh_all()

    def _save_sites(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save transmitter sites", str(Path.cwd() / "transmitters.csv"),
            "CSV files (*.csv)")
        if not path:
            return
        try:
            p = self.store.save_sites(path)
        except OSError as exc:
            QMessageBox.warning(self, "Could not write file", str(exc))
            return
        self.status.setText("Saved %d site(s) to %s" % (len(self.store.sites), p))

    def _write_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Write CSV template",
            str(Path.cwd() / "transmitters_template.csv"), "CSV files (*.csv)")
        if not path:
            return
        p = write_template(path)
        QMessageBox.information(
            self, "Template written",
            "Wrote %s" % p + chr(10) * 2
            + "Columns: name,kind,frequency_mhz,latitude,longitude,height_m,"
              "power_kw,notes" + chr(10) * 2
            + "Example: Test Beacon,Test,433.920,59.9000,10.7000,10,0.01,"
              "Own transmitter" + chr(10) * 2
            + "Template rows begin with # and are ignored until you remove it.")

    def _add_site(self) -> None:
        freq = self.selected_frequency_hz() or 100.0e6
        site = Site(name="New site %d" % (len(self.store.sites) + 1),
                    kind="Test", freq_hz=freq,
                    lat=self.lat.value(), lon=self.lon.value(),
                    notes="edit the coordinates, then Save CSV")
        self.store.add_site(site)
        self.status.setText(
            "Added a placeholder at your own position on %.4f MHz. Edit the "
            "coordinates - a site on top of you is meaningless." % (freq / 1e6))
        self._refresh_all()

    def _remove_site(self) -> None:
        row = self.site_table.currentRow()
        if row < 0:
            return
        self.store.remove_site(row)
        self._refresh_all()

    def _select_site(self, index: int) -> None:
        self.site_table.selectRow(index)

    def _tune_to_selected_site(self) -> None:
        row = self.site_table.currentRow()
        if 0 <= row < len(self.store.sites):
            site = self.store.sites[row]
            if site.freq_hz > 0:
                self.app.tune_to(site.freq_hz)

    # ------------------------------------------------------------------
    # Manual direction finding
    # ------------------------------------------------------------------
    def _record_bearing(self) -> None:
        freq = self.selected_frequency_hz() or 0.0
        level = self.app.current_peak_dbfs()
        if self._selected is not None:
            level = self._selected.level_dbfs
        gain = self.app.current_gain()

        measurement = BearingMeasurement(
            observer_lat=self.lat.value(), observer_lon=self.lon.value(),
            bearing_deg=float(self.bearing.value()), freq_hz=freq,
            level_dbfs=level if level is not None else float("nan"),
            gain_db=gain)
        try:
            measurement.validate()
        except Exception as exc:
            QMessageBox.warning(self, "Bearing not recorded", str(exc))
            return

        rec = BearingRecord(
            label="%.4f MHz" % (freq / 1e6) if freq else "unspecified",
            freq_hz=freq, lat=measurement.observer_lat,
            lon=measurement.observer_lon,
            bearing_deg=measurement.bearing_deg,
            level_dbfs=measurement.level_dbfs)
        rec.gain_db = gain
        self.store.add_bearing(rec)
        self.status.setText("Recorded %.1f deg from %s."
                            % (rec.bearing_deg, rec.position()))
        self._refresh_all()

    def _clear_bearings(self) -> None:
        self.store.clear_bearings()
        self._fixes = []
        self._refresh_all()

    def _bearing_geometry(self) -> tuple[bool, str]:
        """Can the last two bearings give a fix, and how trustworthy is it?"""
        recs = self.store.bearings
        if len(recs) < 2:
            return False, ("Record two bearings from different positions to "
                           "estimate an intersection.")
        a, b = recs[-2], recs[-1]
        separation = distance_km(a.position(), b.position())
        if separation < 0.05:
            return False, ("The last two bearings were taken from effectively "
                           "the same place (%.0f m apart). Move at least a few "
                           "hundred metres to the side."
                           % (separation * 1000.0))
        cut = angle_between(a.bearing_deg, b.bearing_deg)
        cut = min(cut, 180.0 - cut)
        if cut < 15.0:
            return False, ("Bearings are within %.0f deg of parallel - they "
                           "cannot fix a position." % cut)
        quality = ("GOOD" if cut >= 45.0 else
                   "FAIR" if cut >= 25.0 else "POOR GEOMETRY")
        return True, ("Baseline %.2f km, cut angle %.0f deg  ->  %s"
                      % (separation, cut, quality))

    def _triangulate(self) -> None:
        ok, message = self._bearing_geometry()
        if not ok:
            QMessageBox.information(self, "Cannot estimate intersection", message)
            self.status.setText(message)
            return
        a, b = self.store.bearings[-2], self.store.bearings[-1]
        fix, reason = triangulate(
            a.position(), a.bearing_deg, b.position(), b.bearing_deg,
            max_distance_km=max(self.radar.range_km * 4.0, 50.0))
        if fix is None:
            self.status.setText("No usable fix: %s" % reason)
            QMessageBox.information(self, "No fix", reason)
            return
        label = "Estimated intersection (%s, cut %.0f deg)" % (
            fix.quality.upper(), fix.cut_angle_deg)
        self._fixes.append((fix.point, fix.quality, label))
        self.status.setText(
            "Estimated intersection at %s - %.1f km and %.1f km from the two "
            "observation points, cut angle %.0f deg (%s). An estimate, not an "
            "exact location." % (fix.point, fix.distance1_km, fix.distance2_km,
                                 fix.cut_angle_deg, fix.quality))
        self._refresh_all()

    # ------------------------------------------------------------------
    # Bearing rose
    # ------------------------------------------------------------------
    def _add_sweep_point(self) -> None:
        level = self.app.current_peak_dbfs()
        if self._selected is not None:
            level = self._selected.level_dbfs
        if level is None:
            self.status.setText(
                "Nothing is being received - start the Scanner first.")
            return
        brg = float(self.bearing.value())
        self._sweep = [(b, l) for b, l in self._sweep if abs(b - brg) > 0.5]
        self._sweep.append((brg, level))
        self._refresh_all()

    def _clear_sweep(self) -> None:
        self._sweep = []
        self._refresh_all()

    def _rose_summary(self) -> str:
        if len(self._sweep) < 3:
            return ("Rotate a directional antenna and record the level at each "
                    "bearing. %d point(s) so far; a peak direction needs at "
                    "least 3. It is never inferred from an omnidirectional "
                    "antenna." % len(self._sweep))
        peak_brg, peak_lvl = max(self._sweep, key=lambda t: t[1])
        opposite = (peak_brg + 180.0) % 360.0
        back_brg, back_lvl = min(
            self._sweep, key=lambda t: angle_between(t[0], opposite))
        return ("Peak bearing: %03.0f deg     Peak level: %.1f dBFS     "
                "Front-to-back: %.1f dB (vs %03.0f deg)"
                % (peak_brg, peak_lvl, peak_lvl - back_lvl, back_brg))

    # ------------------------------------------------------------------
    # Coverage
    # ------------------------------------------------------------------
    def _record_coverage(self) -> None:
        freq = self.selected_frequency_hz()
        level = self.app.current_peak_dbfs()
        noise = self.app.current_noise_dbfs()
        if self._selected is not None:
            freq = self._selected.freq_hz
            level = self._selected.level_dbfs
            noise = self._selected.noise_dbfs
        if level is None or not freq:
            self.status.setText(
                "Select a live signal, or start the Scanner, before recording.")
            return
        gain = self.app.current_gain()
        measurement = CoverageMeasurement(
            observer_lat=self.lat.value(), observer_lon=self.lon.value(),
            freq_hz=freq, level_dbfs=level,
            noise_dbfs=noise if noise is not None else float("nan"),
            snr_db=(level - noise) if noise is not None else float("nan"),
            gain_db=gain)
        try:
            measurement.validate()
        except Exception as exc:
            QMessageBox.warning(self, "Point not recorded", str(exc))
            return

        point = CoveragePoint(
            lat=measurement.observer_lat, lon=measurement.observer_lon,
            freq_hz=measurement.freq_hz, level_dbfs=measurement.level_dbfs,
            noise_dbfs=measurement.noise_dbfs, snr_db=measurement.snr_db,
            notes="gain=%s" % gain)
        self.coverage.add(point)
        self.status.setText(
            "Coverage point %d: %.1f dBFS on %.4f MHz at %s (gain %s)."
            % (len(self.coverage), level, freq / 1e6, point.position(), gain))
        self._refresh_all()

    def _gain_consistency(self) -> str:
        gains = set()
        for p in self.coverage.points:
            note = p.notes or ""
            if note.startswith("gain="):
                gains.add(note.split("=", 1)[1])
        if len(gains) > 1:
            return ("NOT COMPARABLE - points were recorded at %d different gain "
                    "settings (%s). Fix the gain and survey again."
                    % (len(gains), ", ".join(sorted(gains))))
        if gains:
            return "%d point(s), all recorded at gain %s." % (
                len(self.coverage), next(iter(gains)))
        return "%d point(s)." % len(self.coverage)

    def _save_coverage(self) -> None:
        if not len(self.coverage):
            self.status.setText("No coverage points to save yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save coverage survey", str(Path.cwd() / "coverage.csv"),
            "CSV files (*.csv)")
        if not path:
            return
        try:
            p = self.coverage.save(path)
        except OSError as exc:
            QMessageBox.warning(self, "Could not write file", str(exc))
            return
        self.status.setText("Saved %d coverage point(s) to %s"
                            % (len(self.coverage), p))

    def _load_coverage(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load coverage survey", str(Path.cwd()), "CSV files (*.csv)")
        if not path:
            return
        try:
            count, warnings = self.coverage.load(path)
        except OSError as exc:
            QMessageBox.warning(self, "Could not read file", str(exc))
            return
        self.status.setText("Loaded %d coverage point(s)." % count)
        if warnings:
            QMessageBox.information(self, "Some rows were skipped",
                                    chr(10).join(warnings[:12]))
        self._refresh_all()

    def _clear_coverage(self) -> None:
        self.coverage.clear()
        self._refresh_all()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def _refresh_all(self) -> None:
        self.radar.set_origin(self.lat.value(), self.lon.value())
        self.radar.set_data(sites=self.store.sites, bearings=self.store.bearings,
                            fixes=self._fixes, sweep=self._sweep)
        self.radar.set_coverage(self.coverage.points, self.coverage.level_range())
        self._refresh_tables()

    def _refresh_tables(self) -> None:
        here = self.here()
        sites = self.store.sites
        tol = self.tolerance.value() * 1e3
        self.site_table.setRowCount(len(sites))
        for r, s in enumerate(sites):
            heard = s.is_heard()
            level = ("%.1f dBFS" % s.level_dbfs
                     if heard and s.level_dbfs == s.level_dbfs else "-")
            cells = [s.name, s.kind, "%.4f" % (s.freq_hz / 1e6),
                     "%.1f deg" % s.bearing_from(here),
                     "%.1f km" % s.distance_from(here), level,
                     "possible" if heard else "-"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if heard and c == 5:
                    item.setForeground(QColor(theme.GOOD))
                if heard and c == 6:
                    item.setForeground(QColor(theme.WARN))
                self.site_table.setItem(r, c, item)

        if sites:
            matched = sum(1 for s in sites if s.is_heard())
            self.lbl_sites_hint.setText(
                "%d site(s); %d with a possible frequency match within "
                "+/-%.1f kHz. A frequency match is not proof the signal came "
                "from that site. Double-click a row to tune to it."
                % (len(sites), matched, tol / 1e3))
        else:
            self.lbl_sites_hint.setText(
                "Empty because this is reference data you supply - the radar "
                "does not scan for transmitters. Press Write template for the "
                "CSV format, then fill it from a published register.")

        recs = self.store.bearings
        self.bearing_table.setRowCount(len(recs))
        for r, rec in enumerate(recs):
            level = ("-" if rec.level_dbfs != rec.level_dbfs
                     else "%.1f" % rec.level_dbfs)
            gain = getattr(rec, "gain_db", "auto")
            gain_text = gain if isinstance(gain, str) else "%.1f" % float(gain)
            cells = [str(rec.position()), "%.1f deg" % rec.bearing_deg,
                     "%.4f" % (rec.freq_hz / 1e6), level, gain_text]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if c:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.bearing_table.setItem(r, c, item)

        ok, message = self._bearing_geometry()
        self.btn_fix.setEnabled(ok)
        self.lbl_geometry.setText(message)
        self.lbl_rose.setText(self._rose_summary())
        self.lbl_coverage.setText(self._gain_consistency())

    # ------------------------------------------------------------------
    def update_frame(self, frame) -> None:
        """Not needed: detections arrive through the shared store."""

    def set_running(self, running: bool) -> None:
        pass
