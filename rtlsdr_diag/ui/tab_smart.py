"""Smart Scanner: what is on the air, how strong, and which way it is going.

Four areas, in the order a person actually reads them:

  top     status bar - what the receiver is doing
  left    active signals, strongest first
  right   the selected signal, large and legible
  bottom  a source panel, collapsed unless there is real source data

Everything shown is measured. Nothing on this page invents a direction or a
distance for a live signal, because one antenna cannot supply either.
"""
from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QScrollArea, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..core import settings
from ..core.geo import LatLon, angle_between, distance_km, triangulate
from ..core.models import SIGNAL_CLASSES
from ..core.motion import AUTO, DRIVE, MODES, STATIONARY, DriveSurvey, MotionMode
from ..core.proximity import (PROXIMITY_HELP, proximity_fraction,
                              proximity_label, trend)
from ..core.sites import BearingRecord, Site, SiteStore, write_template
from . import theme, widgets
from .radar import RadarView
from .status import SdrStatusStrip

MAX_ROWS = 20
STALE_S = 20.0

HELP_TEXT = (
    "The scanner finds RF signals and measures each one.\n"
    "Strength is how strongly the signal arrives at this receiver, in dBFS -\n"
    "relative to the receiver's full scale, not calibrated to dBm.\n"
    "Proximity is a relative estimate, never an exact distance.\n"
    "With one ordinary antenna the direction to a source is unknown. It becomes\n"
    "available only if you add known-site data, take directional bearings, or\n"
    "collect a drive survey.\n"
    "The map below shows known sites, drive coverage or your own bearings -\n"
    "never a guessed position for a live signal."
)

TREND_COLOURS = {"Rising": theme.GOOD, "Falling": theme.BAD, "Stable": theme.TEXT_DIM}
PROX_COLOURS = {
    "Very Strong": theme.GOOD, "Strong": theme.GOOD, "Medium": theme.WARN,
    "Weak": theme.TEXT_DIM, "Very Weak": theme.TEXT_FAINT,
}
COLUMNS = ["", "Frequency", "Type", "Strength", "SNR", "Bandwidth",
           "Proximity", "Trend", "Last seen"]


class BigValue(QFrame):
    """A large number with a caption, for the detail panel."""

    def __init__(self, caption: str, unit: str = "", size: int = 26, parent=None):
        super().__init__(parent)
        self.setObjectName("bigValue")
        self.setStyleSheet(
            "#bigValue { background: %s; border: 1px solid %s; "
            "border-radius: 10px; }" % (theme.BG_CARD, theme.BORDER))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 10)
        lay.setSpacing(2)
        cap = QLabel(caption.upper())
        cap.setStyleSheet("color: %s; font-size: 10px; font-weight: 700; "
                          "letter-spacing: 1px;" % theme.TEXT_FAINT)
        lay.addWidget(cap)
        self.value = QLabel("--")
        f = QFont()
        f.setPointSize(size)
        f.setBold(True)
        self.value.setFont(f)
        self.value.setStyleSheet("color: %s;" % theme.TEXT)
        lay.addWidget(self.value)
        self.unit = QLabel(unit)
        self.unit.setStyleSheet("color: %s; font-size: 11px;" % theme.TEXT_DIM)
        self.unit.setVisible(bool(unit))
        lay.addWidget(self.unit)

    def set(self, text: str, colour: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet("color: %s;" % (colour or theme.TEXT))


class SmartScannerTab(QWidget):
    tab_label = "Smart Scanner"

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.sites = SiteStore()
        self.survey = DriveSurvey()
        self.motion = MotionMode(AUTO)
        self._selected_id = None
        self._shown: list = []
        self._auto_select = True
        self._fixes: list = []
        self._last_log = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        root.addLayout(self._build_status())
        root.addWidget(self._build_help())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([720, 620])
        root.addWidget(splitter, 1)

        self.log_line = widgets.dim_label("Ready.")
        root.addWidget(self.log_line)

        self.app.signal_store.subscribe(self._on_detections)
        self.app.register_status_listener(self._on_status)

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()
        self._refresh_source_panel()

    # ==================================================================
    # 1. Status bar
    # ==================================================================
    def _build_status(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.status_strip = SdrStatusStrip("Start Scan")
        self.status_strip.start_requested.connect(self._start_scan)
        self.status_strip.stop_requested.connect(self.app.engine_stop)
        bar.addWidget(self.status_strip, 1)

        side = QVBoxLayout()
        side.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Mode"))
        self.mode_box = QComboBox()
        for m in MODES:
            self.mode_box.addItem(m, m)
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        self.mode_box.setToolTip(
            "AUTO follows GPS speed with hysteresis; without a GPS fix it "
            "stays Stationary.")
        row.addWidget(self.mode_box)
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setCheckable(True)
        self.btn_pause.setToolTip("Freeze the list without stopping the receiver.")
        self.btn_pause.toggled.connect(self._pause_toggled)
        row.addWidget(self.btn_pause)
        self.btn_help = QPushButton("How this works")
        self.btn_help.setCheckable(True)
        self.btn_help.toggled.connect(lambda on: self.help_box.setVisible(on))
        row.addWidget(self.btn_help)
        holder = QWidget()
        holder.setLayout(row)
        side.addWidget(holder)

        self.mode_label = QLabel("Mode: AUTO -> STATIONARY (no GPS)")
        self.mode_label.setStyleSheet("color: %s; font-size: 11px;" % theme.TEXT_DIM)
        side.addWidget(self.mode_label)
        sideholder = QWidget()
        sideholder.setLayout(side)
        bar.addWidget(sideholder)
        return bar

    def _build_help(self) -> QWidget:
        self.help_box = QLabel(HELP_TEXT)
        self.help_box.setWordWrap(True)
        self.help_box.setVisible(False)
        self.help_box.setStyleSheet(
            "background: rgba(47,155,255,0.08); border: 1px solid %s; "
            "border-radius: 8px; padding: 10px; color: %s; font-size: 12px;"
            % (theme.ACCENT_DIM, theme.TEXT))
        return self.help_box

    # ==================================================================
    # 2. Active signals
    # ==================================================================
    def _build_left(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("Sort"))
        self.sort_box = QComboBox()
        self.sort_box.addItem("Strongest first", "strength")
        self.sort_box.addItem("Frequency", "frequency")
        self.sort_box.currentIndexChanged.connect(self._refresh_table)
        bar.addWidget(self.sort_box)

        bar.addWidget(QLabel("Type"))
        self.type_box = QComboBox()
        self.type_box.addItem("All", None)
        for name in SIGNAL_CLASSES:
            self.type_box.addItem(name, name)
        self.type_box.currentIndexChanged.connect(self._refresh_table)
        bar.addWidget(self.type_box)

        self.chk_active = QCheckBox("Active only")
        self.chk_active.setChecked(True)
        self.chk_active.toggled.connect(self._refresh_table)
        bar.addWidget(self.chk_active)

        b = QPushButton("Clear")
        b.clicked.connect(self._clear)
        bar.addWidget(b)
        bar.addStretch(1)
        self.count_label = QLabel("0 signals")
        self.count_label.setStyleSheet("font-weight: 600;")
        bar.addWidget(self.count_label)
        lay.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Stretch)
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 26)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.doubleClicked.connect(self._on_row_activated)
        lay.addWidget(self.table, 1)

        lay.addWidget(widgets.dim_label(
            "Click to inspect. Double-click to tune the Signal Meter to it. "
            "Levels are dBFS, relative to full scale."))
        return panel

    # ==================================================================
    # 3. Selected signal
    # ==================================================================
    def _build_right(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.sel_title = QLabel("No signal selected")
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        self.sel_title.setFont(f)
        lay.addWidget(self.sel_title)
        self.sel_subtitle = widgets.dim_label(
            "Start a scan; the strongest signal is selected automatically.")
        lay.addWidget(self.sel_subtitle)

        meter_box = widgets.group("Signal strength")
        self.meter = widgets.SignalMeter(-90.0, 0.0)
        meter_box.layout().addWidget(self.meter)
        lay.addWidget(meter_box)

        grid = QGridLayout()
        grid.setSpacing(8)
        self.v_strength = BigValue("Strength", "dBFS")
        self.v_snr = BigValue("SNR", "dB")
        self.v_prox = BigValue("Proximity (relative)", "", size=17)
        self.v_trend = BigValue("Trend", "", size=17)
        for i, w in enumerate((self.v_strength, self.v_snr, self.v_prox,
                               self.v_trend)):
            grid.addWidget(w, 0, i)
        gh = QWidget()
        gh.setLayout(grid)
        lay.addWidget(gh)

        self.prox_note = widgets.dim_label(PROXIMITY_HELP)
        self.prox_note.setToolTip(PROXIMITY_HELP)
        lay.addWidget(self.prox_note)

        detail_box = widgets.group("Details")
        self.detail_grid = QGridLayout()
        self.detail_grid.setSpacing(6)
        self._detail_labels = {}
        fields = ["Type", "Confidence", "Average", "Peak", "Minimum",
                  "Noise floor", "Bandwidth", "Gain", "First seen", "Last seen",
                  "Trend 5 s", "Trend 60 s", "Source estimate"]
        for i, name in enumerate(fields):
            cap = QLabel(name)
            cap.setStyleSheet("color: %s; font-size: 11px;" % theme.TEXT_FAINT)
            val = QLabel("--")
            val.setStyleSheet("font-size: 12px;")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.detail_grid.addWidget(cap, i // 2, (i % 2) * 2)
            self.detail_grid.addWidget(val, i // 2, (i % 2) * 2 + 1)
            self._detail_labels[name] = val
        dh = QWidget()
        dh.setLayout(self.detail_grid)
        detail_box.layout().addWidget(dh)
        lay.addWidget(detail_box)

        hist_box = widgets.group("History")
        hrow = QHBoxLayout()
        hrow.addWidget(QLabel("Window"))
        self.hist_window = QComboBox()
        for label, secs in (("30 s", 30.0), ("5 min", 300.0), ("30 min", 1800.0)):
            self.hist_window.addItem(label, secs)
        self.hist_window.currentIndexChanged.connect(self._refresh_history)
        hrow.addWidget(self.hist_window)
        hrow.addStretch(1)
        hh = QWidget()
        hh.setLayout(hrow)
        hist_box.layout().addWidget(hh)

        self.hist_plot = pg.PlotWidget()
        self.hist_plot.setLabel("bottom", "Time", units="s")
        self.hist_plot.setLabel("left", "dB")
        self.hist_plot.showGrid(x=True, y=True, alpha=0.2)
        self.hist_plot.getPlotItem().setMenuEnabled(False)
        self.hist_plot.setMinimumHeight(130)
        self.curve_level = self.hist_plot.plot(pen=pg.mkPen(theme.TRACE, width=1.6),
                                               name="level")
        self.curve_snr = self.hist_plot.plot(
            pen=pg.mkPen(theme.ACCENT, width=1.2, style=Qt.DashLine), name="SNR")
        hist_box.layout().addWidget(self.hist_plot)
        hist_box.layout().addWidget(widgets.dim_label(
            "Solid: level (dBFS).   Dashed: SNR (dB)."))
        lay.addWidget(hist_box)

        lay.addWidget(self._build_source_panel())
        lay.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    # ==================================================================
    # 4. Source panel
    # ==================================================================
    def _build_source_panel(self) -> QWidget:
        box = widgets.group("Source")
        self.btn_source = QPushButton("Show source panel")
        self.btn_source.setCheckable(True)
        self.btn_source.toggled.connect(self._toggle_source)
        box.layout().addWidget(self.btn_source)

        self.source_summary = QLabel("Source location unknown.")
        self.source_summary.setWordWrap(True)
        self.source_summary.setStyleSheet(
            "background: %s; border: 1px solid %s; border-radius: 8px; "
            "padding: 10px; color: %s; font-size: 12px;"
            % (theme.BG_CARD, theme.BORDER, theme.TEXT))
        box.layout().addWidget(self.source_summary)

        self.source_body = QWidget()
        body = QVBoxLayout(self.source_body)
        body.setContentsMargins(0, 6, 0, 0)
        body.setSpacing(8)

        pos = QHBoxLayout()
        lat, lon = settings.observer_position()
        pos.addWidget(QLabel("Your position"))
        self.lat = QDoubleSpinBox()
        self.lat.setRange(-90.0, 90.0)
        self.lat.setDecimals(5)
        self.lat.setValue(lat)
        self.lat.setPrefix("lat ")
        self.lat.valueChanged.connect(self._position_changed)
        pos.addWidget(self.lat)
        self.lon = QDoubleSpinBox()
        self.lon.setRange(-180.0, 180.0)
        self.lon.setDecimals(5)
        self.lon.setValue(lon)
        self.lon.setPrefix("lon ")
        self.lon.valueChanged.connect(self._position_changed)
        pos.addWidget(self.lon)
        pos.addWidget(QLabel("Range"))
        self.range_box = QComboBox()
        for km in (1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0):
            self.range_box.addItem("%g km" % km, km)
        self.range_box.setCurrentIndex(3)
        self.range_box.currentIndexChanged.connect(self._range_changed)
        pos.addWidget(self.range_box)
        pos.addStretch(1)
        ph = QWidget()
        ph.setLayout(pos)
        body.addWidget(ph)

        self.map_view = RadarView()
        self.map_view.setMinimumHeight(260)
        body.addWidget(self.map_view)

        sites_row = QHBoxLayout()
        for text, slot in (("Load Sites", self._load_sites),
                           ("Save Sites", self._save_sites),
                           ("Add Site", self._add_site),
                           ("Remove Site", self._remove_site),
                           ("Template", self._write_template)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            sites_row.addWidget(b)
        sites_row.addStretch(1)
        sh = QWidget()
        sh.setLayout(sites_row)
        body.addWidget(sh)
        self.sites_label = widgets.dim_label("No known sites loaded.")
        body.addWidget(self.sites_label)

        # --- drive survey ------------------------------------------------
        survey_row = QHBoxLayout()
        self.btn_survey = QPushButton("Record point")
        self.btn_survey.clicked.connect(lambda: self._record_survey(force=True))
        survey_row.addWidget(self.btn_survey)
        for text, slot in (("Save survey", self._save_survey),
                           ("Load survey", self._load_survey),
                           ("Clear survey", self._clear_survey)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            survey_row.addWidget(b)
        survey_row.addStretch(1)
        sv = QWidget()
        sv.setLayout(survey_row)
        body.addWidget(sv)
        self.survey_label = widgets.dim_label("No survey points recorded.")
        body.addWidget(self.survey_label)

        # --- advanced: manual DF -----------------------------------------
        self.btn_advanced = QPushButton("Advanced source estimation")
        self.btn_advanced.setCheckable(True)
        self.btn_advanced.toggled.connect(
            lambda on: self.advanced_body.setVisible(on))
        body.addWidget(self.btn_advanced)

        self.advanced_body = QWidget()
        adv = QVBoxLayout(self.advanced_body)
        adv.setContentsMargins(0, 4, 0, 0)
        adv.setSpacing(6)
        adv.addWidget(widgets.dim_label(
            "For fixed sites, test transmitters and transmitters you are "
            "authorised to locate. Point a directional antenna, read the true "
            "bearing, and record it here."))
        arow = QHBoxLayout()
        arow.addWidget(QLabel("Bearing"))
        self.bearing = QDoubleSpinBox()
        self.bearing.setRange(0.0, 359.9)
        self.bearing.setDecimals(1)
        self.bearing.setSuffix(" deg")
        arow.addWidget(self.bearing)
        b = QPushButton("Record bearing")
        b.clicked.connect(self._record_bearing)
        arow.addWidget(b)
        self.btn_fix = QPushButton("Estimate intersection")
        self.btn_fix.setEnabled(False)
        self.btn_fix.clicked.connect(self._triangulate)
        arow.addWidget(self.btn_fix)
        b = QPushButton("Clear bearings")
        b.clicked.connect(self._clear_bearings)
        arow.addWidget(b)
        arow.addStretch(1)
        ah = QWidget()
        ah.setLayout(arow)
        adv.addWidget(ah)
        self.bearing_label = widgets.dim_label("No bearings recorded.")
        adv.addWidget(self.bearing_label)
        self.advanced_body.setVisible(False)
        body.addWidget(self.advanced_body)

        self.source_body.setVisible(False)
        box.layout().addWidget(self.source_body)
        return box

    # ==================================================================
    # Status / mode
    # ==================================================================
    def _on_status(self, status) -> None:
        self.status_strip.update_status(status)

    def _start_scan(self) -> None:
        self.app.start_default_scan()

    def _mode_changed(self) -> None:
        self.motion.select(self.mode_box.currentData())
        self._update_mode_label()

    def _update_mode_label(self) -> None:
        self.mode_label.setText("Mode: %s" % self.motion.describe())

    def _pause_toggled(self, paused: bool) -> None:
        self.btn_pause.setText("Resume" if paused else "Pause")
        self._log("List paused - the receiver keeps running."
                  if paused else "List resumed.")

    def update_speed(self, speed_kmh) -> None:
        """Feed a GPS speed if one becomes available."""
        self.motion.update_speed(speed_kmh)
        self._update_mode_label()

    # ==================================================================
    # Detections
    # ==================================================================
    def _on_detections(self, detections) -> None:
        if self.btn_pause.isChecked():
            return
        self._refresh_table(detections)

    def _visible(self, detections=None) -> list:
        items = list(detections if detections is not None
                     else self.app.signal_store.items())
        wanted = self.type_box.currentData()
        if wanted:
            items = [d for d in items if d.signal_class == wanted]
        if self.chk_active.isChecked():
            items = [d for d in items if d.is_active(STALE_S)]
        if self.sort_box.currentData() == "frequency":
            items.sort(key=lambda d: d.freq_hz)
        else:
            items.sort(key=lambda d: d.level_dbfs, reverse=True)
        return items[:MAX_ROWS]

    def _refresh_table(self, detections=None) -> None:
        items = self._visible(detections)
        self._shown = items
        total = len(self.app.signal_store)
        self.count_label.setText("%d shown / %d tracked" % (len(items), total))

        if self._auto_select and items and self.selected() is None:
            self._selected_id = items[0].id

        self.table.blockSignals(True)
        self.table.setRowCount(len(items))
        select_row = -1
        for r, d in enumerate(items):
            if d.id == self._selected_id:
                select_row = r
            t = trend(d.history, 15.0)
            prox = proximity_label(d.snr_db)
            cells = ["*" if d.is_active(STALE_S) else "",
                     "%.4f MHz" % d.frequency_mhz,
                     d.signal_class,
                     "%.1f dBFS" % d.level_dbfs,
                     "%.1f dB" % d.snr_db,
                     "~%.1f kHz" % (d.bandwidth_hz / 1e3),
                     prox,
                     t.direction,
                     _dt.datetime.fromtimestamp(d.last_seen).strftime("%H:%M:%S")]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setForeground(QColor(theme.GOOD))
                elif c in (3, 4, 5):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif c != 1:
                    item.setTextAlignment(Qt.AlignCenter)
                if c == 6:
                    item.setForeground(QColor(PROX_COLOURS.get(prox, theme.TEXT)))
                if c == 7:
                    item.setForeground(QColor(TREND_COLOURS.get(t.direction,
                                                                theme.TEXT_DIM)))
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        if select_row >= 0:
            self.table.selectRow(select_row)
        self._refresh_detail()

    def selected(self):
        for d in self.app.signal_store.items():
            if d.id == self._selected_id:
                return d
        return None

    def _on_row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if 0 <= row < len(self._shown):
            self._selected_id = self._shown[row].id
            self._auto_select = False
            self._refresh_detail()

    def _on_row_activated(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._shown):
            self.app.tune_to(self._shown[row].freq_hz)
            self._log("Tuned to %.4f MHz." % (self._shown[row].frequency_mhz))

    def _clear(self) -> None:
        self.app.signal_store.clear(notify=True)
        self._selected_id = None
        self._auto_select = True
        self._refresh_table([])
        self._log("Detections cleared.")

    # ==================================================================
    # Selected signal detail
    # ==================================================================
    def _refresh_detail(self) -> None:
        d = self.selected()
        if d is None:
            self.sel_title.setText("No signal selected")
            self.sel_subtitle.setText(
                "Start a scan; the strongest signal is selected automatically.")
            for w in (self.v_strength, self.v_snr, self.v_prox, self.v_trend):
                w.set("--")
            for label in self._detail_labels.values():
                label.setText("--")
            self.meter.set_values(-90.0, -90.0, -90.0)
            self.curve_level.setData([], [])
            self.curve_snr.setData([], [])
            return

        self.sel_title.setText("%.4f MHz   %s" % (d.frequency_mhz, d.signal_class))
        self.sel_subtitle.setText(
            "Active" if d.is_active(STALE_S) else "Not heard recently")

        prox = proximity_label(d.snr_db)
        t15 = trend(d.history, 15.0)
        self.v_strength.set("%.1f" % d.level_dbfs)
        self.v_snr.set("%.1f" % d.snr_db,
                       theme.GOOD if d.snr_db >= 20 else theme.WARN)
        self.v_prox.set(prox, PROX_COLOURS.get(prox, theme.TEXT))
        self.v_trend.set(t15.direction,
                         TREND_COLOURS.get(t15.direction, theme.TEXT_DIM))
        self.meter.set_values(d.level_dbfs, d.peak_level_dbfs, d.noise_dbfs)

        gain = d.gain_db
        set_ = self._detail_labels
        set_["Type"].setText(d.signal_class)
        set_["Confidence"].setText(d.confidence)
        set_["Average"].setText("%.1f dBFS" % d.average_level_db(60.0))
        set_["Peak"].setText("%.1f dBFS" % d.peak_level_dbfs)
        set_["Minimum"].setText("%.1f dBFS" % d.min_level_db())
        set_["Noise floor"].setText("%.1f dBFS" % d.noise_dbfs)
        set_["Bandwidth"].setText("~%.1f kHz" % (d.bandwidth_hz / 1e3))
        set_["Gain"].setText("AUTO" if isinstance(gain, str)
                             else "%.1f dB" % float(gain))
        set_["First seen"].setText(
            _dt.datetime.fromtimestamp(d.first_seen).strftime("%H:%M:%S"))
        set_["Last seen"].setText(
            _dt.datetime.fromtimestamp(d.last_seen).strftime("%H:%M:%S"))
        set_["Trend 5 s"].setText(trend(d.history, 5.0).text())
        set_["Trend 60 s"].setText(trend(d.history, 60.0).text())
        set_["Source estimate"].setText(self._source_estimate(d))
        self._refresh_history()

    def _source_estimate(self, d) -> str:
        tol = settings.frequency_match_tolerance_hz()
        matches = [s for s in self.sites.sites if s.matches_frequency(d.freq_hz, tol)]
        parts = []
        if matches:
            parts.append("possible fixed-site match (%s)" % matches[0].name)
        if self.sites.bearings:
            parts.append("%d bearing(s) recorded" % len(self.sites.bearings))
        if len(self.survey):
            parts.append("%d survey point(s)" % len(self.survey))
        return ", ".join(parts) if parts else "Unknown - no source data"

    def _refresh_history(self) -> None:
        d = self.selected()
        if d is None or not d.history:
            self.curve_level.setData([], [])
            self.curve_snr.setData([], [])
            return
        window = float(self.hist_window.currentData())
        rows = d.history_since(window, time.time())
        if not rows:
            rows = d.history[-2:]
        now = time.time()
        xs = [r[0] - now for r in rows]
        self.curve_level.setData(xs, [r[1] for r in rows])
        self.curve_snr.setData(xs, [r[2] for r in rows])

    # ==================================================================
    # Source panel behaviour
    # ==================================================================
    def _toggle_source(self, shown: bool) -> None:
        self.source_body.setVisible(shown)
        self.btn_source.setText("Hide source panel" if shown
                                else "Show source panel")
        if shown:
            self._refresh_source_panel()

    def _position_changed(self) -> None:
        settings.set_observer_position(self.lat.value(), self.lon.value())
        self.map_view.set_origin(self.lat.value(), self.lon.value())
        self._refresh_source_panel()

    def _range_changed(self) -> None:
        km = float(self.range_box.currentData())
        self.map_view.set_range_km(km)
        settings.set_radar_range_km(km)

    def _refresh_source_panel(self) -> None:
        d = self.selected()
        tol = settings.frequency_match_tolerance_hz()
        matches = []
        if d is not None:
            matches = [s for s in self.sites.sites
                       if s.matches_frequency(d.freq_hz, tol)]
        for s in self.sites.sites:
            s.last_heard = time.time() if s in matches else 0.0
            if s in matches and d is not None:
                s.level_dbfs, s.snr_db = d.level_dbfs, d.snr_db

        have_source = bool(self.sites.sites or self.sites.bearings
                           or len(self.survey))
        if not have_source:
            self.source_summary.setText(
                "Source location unknown.\n"
                "A normal single antenna measures signal strength, not "
                "direction. Load known transmitter sites, record a bearing "
                "with a directional antenna, or collect a drive survey to put "
                "anything on the map.")
        elif matches:
            self.source_summary.setText(
                "Possible fixed-site frequency match: %s. That means the "
                "frequencies agree within %.1f kHz - it is not proof the "
                "signal came from that site."
                % (", ".join(s.name for s in matches), tol / 1e3))
        else:
            bits = []
            if self.sites.sites:
                bits.append("%d known site(s)" % len(self.sites.sites))
            if self.sites.bearings:
                bits.append("%d bearing(s)" % len(self.sites.bearings))
            if len(self.survey):
                bits.append("%d survey point(s)" % len(self.survey))
            self.source_summary.setText(
                "No frequency match for the selected signal. Showing "
                + ", ".join(bits) + ".")

        self.map_view.set_origin(self.lat.value(), self.lon.value())
        self.map_view.set_data(sites=self.sites.sites,
                               bearings=self.sites.bearings,
                               fixes=self._fixes, sweep=[])
        self.map_view.set_coverage(self._survey_points(),
                                   self.survey.level_range())
        self.map_view.set_unlocated([], None)

        self.sites_label.setText(
            "%d known site(s). Frequency match tolerance %.1f kHz."
            % (len(self.sites.sites), tol / 1e3)
            if self.sites.sites else
            "No known sites loaded. CSV columns: name,kind,frequency_mhz,"
            "latitude,longitude,notes")
        self._refresh_survey_label()
        self._refresh_bearing_label()

    def _survey_points(self) -> list:
        """Adapt survey samples to what the map widget draws."""
        from ..core.sites import CoveragePoint
        return [CoveragePoint(lat=s.lat, lon=s.lon, freq_hz=s.freq_hz,
                              level_dbfs=s.level_dbfs, snr_db=s.snr_db,
                              notes="gain=%s" % s.gain_db)
                for s in self.survey.samples]

    # -- known sites -------------------------------------------------------
    def _load_sites(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load known sites", str(Path.cwd()), "CSV files (*.csv)")
        if not path:
            return
        try:
            count, warnings = self.sites.load_sites(path)
        except OSError as exc:
            QMessageBox.warning(self, "Could not read file", str(exc))
            return
        self._log("Loaded %d site(s)." % count)
        if warnings:
            QMessageBox.information(self, "Some rows were skipped",
                                    chr(10).join(warnings[:12]))
        self._refresh_source_panel()

    def _save_sites(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save known sites", str(Path.cwd() / "sites.csv"),
            "CSV files (*.csv)")
        if path:
            self.sites.save_sites(path)
            self._log("Saved %d site(s)." % len(self.sites.sites))

    def _write_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Write template", str(Path.cwd() / "sites_template.csv"),
            "CSV files (*.csv)")
        if path:
            p = write_template(path)
            QMessageBox.information(
                self, "Template written",
                "Wrote %s%sColumns: name,kind,frequency_mhz,latitude,"
                "longitude,notes%sRows starting with # are ignored."
                % (p, chr(10) * 2, chr(10) * 2))

    def _add_site(self) -> None:
        d = self.selected()
        freq = d.freq_hz if d is not None else (self.app.current_center_hz() or 100e6)
        self.sites.add_site(Site("New site %d" % (len(self.sites.sites) + 1),
                                 "Test", freq, self.lat.value(), self.lon.value(),
                                 notes="edit the coordinates, then Save Sites"))
        self._log("Added a placeholder site at your own position.")
        self._refresh_source_panel()

    def _remove_site(self) -> None:
        if self.sites.sites:
            self.sites.remove_site(len(self.sites.sites) - 1)
            self._refresh_source_panel()

    # -- drive survey ------------------------------------------------------
    def _record_survey(self, force: bool = False) -> None:
        d = self.selected()
        if d is None:
            self._log("Select a signal before recording a survey point.")
            return
        sample = self.survey.record(
            self.lat.value(), self.lon.value(),
            self.motion.last_speed_kmh or 0.0, d.freq_hz, d.level_dbfs,
            d.snr_db, self.app.current_gain(), force=force)
        if sample is not None:
            self._log("Survey point %d: %.1f dBFS at %.5f, %.5f"
                      % (len(self.survey), sample.level_dbfs, sample.lat,
                         sample.lon))
            self._refresh_source_panel()

    def _refresh_survey_label(self) -> None:
        if not len(self.survey):
            self.survey_label.setText(
                "No survey points recorded. In DRIVE mode a point is recorded "
                "every second for the selected signal.")
            return
        strongest = self.survey.strongest()
        weakest = self.survey.weakest()
        gains = self.survey.gains_used()
        note = ("   NOT COMPARABLE: %d different gain settings"
                % len(gains)) if len(gains) > 1 else ""
        self.survey_label.setText(
            "%d point(s), route %.2f km. Strongest %.1f dBFS, weakest %.1f dBFS. "
            "Reception was stronger where the points are amber - this maps "
            "reception, not the transmitter's position.%s"
            % (len(self.survey), self.survey.route_length_km(),
               strongest.level_dbfs if strongest else float("nan"),
               weakest.level_dbfs if weakest else float("nan"), note))

    def _save_survey(self) -> None:
        if not len(self.survey):
            self._log("No survey points to save.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save drive survey", str(Path.cwd() / "survey.csv"),
            "CSV files (*.csv)")
        if path:
            self.survey.save(path)
            self._log("Saved %d survey point(s)." % len(self.survey))

    def _load_survey(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load drive survey", str(Path.cwd()), "CSV files (*.csv)")
        if not path:
            return
        count, warnings = self.survey.load(path)
        self._log("Loaded %d survey point(s)." % count)
        if warnings:
            QMessageBox.information(self, "Some rows were skipped",
                                    chr(10).join(warnings[:12]))
        self._refresh_source_panel()

    def _clear_survey(self) -> None:
        self.survey.clear()
        self._refresh_source_panel()

    # -- manual DF ---------------------------------------------------------
    def _record_bearing(self) -> None:
        d = self.selected()
        freq = d.freq_hz if d is not None else 0.0
        rec = BearingRecord(
            label="%.4f MHz" % (freq / 1e6) if freq else "unspecified",
            freq_hz=freq, lat=self.lat.value(), lon=self.lon.value(),
            bearing_deg=float(self.bearing.value()),
            level_dbfs=d.level_dbfs if d is not None else float("nan"))
        rec.gain_db = self.app.current_gain()
        self.sites.add_bearing(rec)
        self._log("Recorded bearing %.1f deg." % rec.bearing_deg)
        self._refresh_source_panel()

    def _clear_bearings(self) -> None:
        self.sites.clear_bearings()
        self._fixes = []
        self._refresh_source_panel()

    def _bearing_geometry(self) -> tuple[bool, str]:
        recs = self.sites.bearings
        if len(recs) < 2:
            return False, "Record two bearings from different positions."
        a, b = recs[-2], recs[-1]
        sep = distance_km(a.position(), b.position())
        if sep < 0.05:
            return False, ("Both bearings were taken from the same place "
                           "(%.0f m apart)." % (sep * 1000.0))
        cut = angle_between(a.bearing_deg, b.bearing_deg)
        cut = min(cut, 180.0 - cut)
        if cut < 15.0:
            return False, ("Bearings are within %.0f deg of parallel." % cut)
        quality = "good" if cut >= 45.0 else ("fair" if cut >= 25.0 else "poor")
        return True, ("Baseline %.2f km, cut %.0f deg -> %s"
                      % (sep, cut, quality.upper()))

    def _refresh_bearing_label(self) -> None:
        ok, message = self._bearing_geometry()
        self.btn_fix.setEnabled(ok)
        n = len(self.sites.bearings)
        self.bearing_label.setText("%d bearing(s). %s" % (n, message))

    def _triangulate(self) -> None:
        ok, message = self._bearing_geometry()
        if not ok:
            QMessageBox.information(self, "Cannot estimate", message)
            return
        a, b = self.sites.bearings[-2], self.sites.bearings[-1]
        fix, reason = triangulate(a.position(), a.bearing_deg,
                                  b.position(), b.bearing_deg,
                                  max_distance_km=200.0)
        if fix is None:
            QMessageBox.information(self, "No estimate", reason)
            return
        self._fixes.append((fix.point, fix.quality,
                            "Estimated intersection (%s)" % fix.quality.upper()))
        self._log("Estimated intersection at %s, cut %.0f deg (%s). An "
                  "estimate, not an exact location."
                  % (fix.point, fix.cut_angle_deg, fix.quality))
        self._refresh_source_panel()

    # ==================================================================
    def _on_tick(self) -> None:
        if self.btn_pause.isChecked():
            return
        self._refresh_table()
        if self.motion.effective == DRIVE and self.selected() is not None:
            self._record_survey(force=False)
        self._update_mode_label()

    def _log(self, message: str) -> None:
        self._last_log = message
        self.log_line.setText("%s   %s"
                              % (_dt.datetime.now().strftime("%H:%M:%S"), message))

    # Interface expected by the main window.
    def update_frame(self, frame) -> None:
        pass

    def set_running(self, running: bool) -> None:
        pass
