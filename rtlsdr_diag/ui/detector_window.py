"""The full-screen detector: three swipeable pages and almost nothing else.

Only the *display* changes when you swipe. The receiver keeps running on its
worker thread throughout; changing page reprioritises which band the rotating
sweep visits most often, and never reopens the device.
"""
from __future__ import annotations

import time

from PySide6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, Qt,
                            QTimer, Property, Signal)
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
                               QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from ..core.detector import (MODE_SPECS, MODES, TETRA_MOBILE,
                             DetectorState)
from ..core.proximity import proximity_label
from ..core.smoothing import MODES as SMOOTH_MODES
from ..sdr.engine import MODE_SWEEP, AcqConfig
from .detector_page import BG, DIM, MUTED, TEXT, DetectorPage
from .log_panel import LogPanel
from .speech import Announcer, spell_frequency

SWIPE_MS = 240
DRAG_THRESHOLD = 60


class SwipeStack(QWidget):
    """Holds the pages side by side and slides between them."""

    page_changed = Signal(int)

    def __init__(self, pages, parent=None):
        super().__init__(parent)
        self.pages = pages
        for page in pages:
            page.setParent(self)
        self._index = 0
        self._offset = 0.0
        self._drag_from = None
        self._drag_offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(SWIPE_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self._relayout()

    # -- animated offset ---------------------------------------------------
    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float) -> None:
        self._offset = float(value)
        self._relayout()

    offset = Property(float, get_offset, set_offset)

    # -- geometry ----------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802
        self._relayout()

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        base = -self._index * w + self._offset
        for i, page in enumerate(self.pages):
            page.setGeometry(int(base + i * w), 0, w, h)

    @property
    def index(self) -> int:
        return self._index

    def go_to(self, index: int, animate: bool = True) -> None:
        index = max(0, min(len(self.pages) - 1, index))
        if index == self._index and abs(self._offset) < 0.5:
            self._animate_to(0.0)
            return
        # Convert the current visual offset into the new page's frame.
        delta = (self._index - index) * self.width()
        self._index = index
        self._offset += delta
        self._relayout()
        self._animate_to(0.0)
        self.page_changed.emit(index)

    def _animate_to(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(target)
        self._anim.start()

    def next_page(self) -> None:
        self.go_to(self._index + 1)

    def previous_page(self) -> None:
        self.go_to(self._index - 1)

    # -- drag --------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._drag_from = event.position()
        self._drag_offset = self._offset

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is None:
            return
        dx = event.position().x() - self._drag_from.x()
        # Resist dragging past the ends so the edges feel solid.
        if (self._index == 0 and dx > 0) or \
           (self._index == len(self.pages) - 1 and dx < 0):
            dx *= 0.35
        self.set_offset(self._drag_offset + dx)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is None:
            return
        dx = event.position().x() - self._drag_from.x()
        self._drag_from = None
        if dx <= -DRAG_THRESHOLD:
            self.next_page()
        elif dx >= DRAG_THRESHOLD:
            self.previous_page()
        else:
            if abs(dx) < 6:
                # A tap, not a drag: pass it to the page under the cursor.
                page = self.pages[self._index]
                local = page.mapFrom(self, event.position().toPoint())
                page.mousePressEvent(_FakeMouse(QPointF(local)))
            self._animate_to(0.0)

    def wheelEvent(self, event) -> None:  # noqa: N802
        # Horizontal wheel or touchpad two-finger swipe.
        dx = event.angleDelta().x()
        if abs(dx) < 40:
            return
        if dx < 0:
            self.next_page()
        else:
            self.previous_page()


class _FakeMouse:
    """Minimal stand-in so a tap can be forwarded to a page."""

    def __init__(self, pos: QPointF):
        self._pos = pos

    def position(self) -> QPointF:
        return self._pos


class PageIndicator(QWidget):
    """FM / DAB / TETRA with the current one lit."""

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        self.labels = labels
        self.index = 0
        self.setFixedHeight(46)

    def set_index(self, index: int) -> None:
        self.index = index
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(BG))
        w = self.width() / max(1, len(self.labels))
        for i, label in enumerate(self.labels):
            active = i == self.index
            font = QFont("Segoe UI", 11 if active else 10)
            font.setBold(active)
            font.setLetterSpacing(QFont.AbsoluteSpacing, 3.0)
            p.setFont(font)
            p.setPen(QPen(QColor(TEXT if active else DIM)))
            rect = self.rect().adjusted(int(i * w), 6, int((i + 1) * w - self.width()), -18)
            p.drawText(rect, Qt.AlignCenter, label)
            # underline for the active page
            if active:
                p.setPen(QPen(QColor("#35d6a4"), 2))
                cx = i * w + w / 2.0
                p.drawLine(int(cx - 18), self.height() - 12,
                           int(cx + 18), self.height() - 12)
        p.end()


class SettingsOverlay(QFrame):
    """A panel over the detector; closing it returns straight to the display."""

    closed = Signal()

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setObjectName("settingsOverlay")
        self.setStyleSheet(
            "#settingsOverlay { background: rgba(8,10,13,242); }"
            "QLabel { color: #e8eef5; font-size: 13px; }"
            "QPushButton { background: #1b2430; color: #e8eef5; border: 1px solid "
            "#3a4757; border-radius: 6px; padding: 8px 14px; font-weight: 600; }"
            "QPushButton:hover { background: #223044; }"
            "QComboBox, QSpinBox, QDoubleSpinBox { background: #151b23; "
            "color: #e8eef5; border: 1px solid #3a4757; border-radius: 6px; "
            "padding: 5px 8px; }"
            "QCheckBox { color: #e8eef5; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 30, 40, 30)
        lay.setSpacing(12)

        title = QLabel("SETTINGS")
        f = QFont("Segoe UI", 16)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 4.0)
        title.setFont(f)
        lay.addWidget(title)

        grid = QVBoxLayout()
        grid.setSpacing(10)

        self.gain_auto = QCheckBox("Gain chosen automatically for each band")
        self.gain_auto.setToolTip(
            "Each band gets the gain that suits it: FM needs the tuner's "
            "highest setting before a station resolves at all, while the "
            "same gain on the TETRA downlink overloads the front end. "
            "Untick to force one gain everywhere.")
        self.gain_auto.setChecked(True)
        self.gain_auto.toggled.connect(self._gain_changed)
        grid.addWidget(self.gain_auto)

        row = QHBoxLayout()
        row.addWidget(QLabel("Manual gain"))
        self.gain = QDoubleSpinBox()
        self.gain.setRange(0.0, 60.0)
        self.gain.setValue(40.0)
        self.gain.setSuffix(" dB")
        self.gain.setEnabled(False)
        self.gain.valueChanged.connect(self._gain_changed)
        row.addWidget(self.gain)
        row.addStretch(1)
        grid.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Frequency correction"))
        self.ppm = QSpinBox()
        self.ppm.setRange(-200, 200)
        self.ppm.setSuffix(" ppm")
        self.ppm.valueChanged.connect(
            lambda v: self.app.engine_update({"ppm": int(v)}))
        row.addWidget(self.ppm)
        row.addStretch(1)
        grid.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Detection threshold"))
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(3.0, 40.0)
        self.threshold.setValue(10.0)
        self.threshold.setSuffix(" dB SNR")
        self.threshold.valueChanged.connect(
            lambda v: self.app.engine_update({"snr_threshold_db": float(v)}))
        row.addWidget(self.threshold)
        row.addStretch(1)
        grid.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Meter smoothing"))
        self.smoothing = QComboBox()
        for m in SMOOTH_MODES:
            self.smoothing.addItem(m, m)
        self.smoothing.setCurrentIndex(1)
        self.smoothing.currentIndexChanged.connect(self._smoothing_changed)
        row.addWidget(self.smoothing)
        row.addStretch(1)
        grid.addLayout(row)

        self.alerts = QCheckBox("Audio alerts on strong signals")
        self.alerts.setChecked(False)
        grid.addWidget(self.alerts)

        self.speak = QCheckBox("Say out loud what is found")
        self.speak.setChecked(True)
        self.speak.setToolTip(
            "Announces the kind of transmitter, its strength and its "
            "frequency, so you do not have to read the screen.")
        grid.addWidget(self.speak)

        self.uplink_alert = QCheckBox(
            "Alert and log TETRA mobile (uplink) activity")
        self.uplink_alert.setChecked(True)
        self.uplink_alert.setToolTip(
            "Uplink bursts are brief. This beeps and appends the time, "
            "frequency, level and SNR to uplink_events.csv so you can check "
            "afterwards instead of watching the screen.")
        self.uplink_alert.toggled.connect(self._uplink_logging_changed)
        grid.addWidget(self.uplink_alert)

        self.focus_band = QCheckBox("Watch only the band on screen")
        self.focus_band.setChecked(False)
        self.focus_band.setToolTip(
            "Normally the receiver shares its time between all four bands, "
            "which can leave up to three seconds between two looks at any one "
            "of them. Tick this to spend every sweep on the page you are "
            "showing - the other pages stop updating.")
        self.focus_band.toggled.connect(self._focus_changed)
        grid.addWidget(self.focus_band)

        driver_row = QHBoxLayout()
        self.driver_btn = QPushButton("Set up the USB driver (Zadig)")
        self.driver_btn.setToolTip(
            "Downloads Zadig from its author's release page and opens it. "
            "Zadig asks Windows for Administrator rights itself, and you "
            "choose which device to bind - this program does not.")
        driver_row.addWidget(self.driver_btn)
        grid.addLayout(driver_row)

        self.driver_label = QLabel("")
        self.driver_label.setStyleSheet("color: #7b8794; font-size: 10px;")
        self.driver_label.setWordWrap(True)
        grid.addWidget(self.driver_label)

        self.fullscreen = QCheckBox("Full screen")
        self.fullscreen.setChecked(True)
        grid.addWidget(self.fullscreen)

        lay.addLayout(grid)
        lay.addStretch(1)

        self.device_label = QLabel("")
        self.device_label.setStyleSheet("color: #7b8794; font-size: 11px;")
        self.device_label.setWordWrap(True)
        lay.addWidget(self.device_label)

        close = QPushButton("Close")
        close.clicked.connect(self.closed.emit)
        lay.addWidget(close)

    def _gain_changed(self) -> None:
        auto = self.gain_auto.isChecked()
        self.gain.setEnabled(not auto)
        self.app.gain_override = None if auto else float(self.gain.value())
        self.app.apply_gain()

    def _uplink_logging_changed(self, enabled: bool) -> None:
        self.app.log_uplink = bool(enabled)

    def _focus_changed(self, enabled: bool) -> None:
        self.app.rotation.focused = bool(enabled)

    def _smoothing_changed(self) -> None:
        mode = self.smoothing.currentData()
        for state in self.app.detector_states.values():
            state.meter.set_mode(mode)

    def refresh(self, status) -> None:
        self.device_label.setText(
            "%s    %s" % (status.headline(), status.settings_line()))


class DetectorWindow(QMainWindow):
    """The application: three detector pages, a gear, and nothing else."""

    def __init__(self, app_core):
        super().__init__()
        self.core = app_core
        self.setWindowTitle("RF Detector")
        self.setStyleSheet("QMainWindow { background: %s; }" % BG)

        self.states = {key: DetectorState(spec) for key, spec in MODE_SPECS.items()}
        self.core.detector_states = self.states

        central = QWidget()
        central.setStyleSheet("background: %s;" % BG)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.pages = [DetectorPage(self.states[key]) for key in MODES]
        for key, page in zip(MODES, self.pages):
            page.lock_toggled.connect(
                lambda k=key: self._toggle_lock(k))
        self.stack = SwipeStack(self.pages)
        self.stack.page_changed.connect(self._page_changed)

        self.log_panel = LogPanel(self.core.observations, central)
        self.log_panel.save_requested.connect(self._save_log)
        self.log_panel.closed.connect(self._toggle_log)
        self.log_panel.hide()

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.stack, 1)
        body.addWidget(self.log_panel, 0)
        root.addLayout(body, 1)

        self.indicator = PageIndicator([MODE_SPECS[k].title for k in MODES])
        root.addWidget(self.indicator)

        self.setCentralWidget(central)

        # --- gear -------------------------------------------------------
        self.gear = QPushButton("⚙", central)
        self.gear.setFixedSize(44, 44)
        self.gear.setStyleSheet(
            "QPushButton { background: transparent; color: %s; border: none; "
            "font-size: 22px; } QPushButton:hover { color: %s; }" % (DIM, TEXT))
        self.gear.clicked.connect(self._open_settings)

        self.log_btn = QPushButton("☰", central)
        self.log_btn.setFixedSize(44, 44)
        self.log_btn.setStyleSheet(
            "QPushButton { background: transparent; color: %s; border: none; "
            "font-size: 20px; } QPushButton:hover { color: %s; }" % (DIM, TEXT))
        self.log_btn.setToolTip("Signal log (G)")
        self.log_btn.clicked.connect(self._toggle_log)

        self.listen_btn = QPushButton("🔇",
                                      central)
        self.listen_btn.setFixedSize(44, 44)
        self.listen_btn.setStyleSheet(
            "QPushButton { background: transparent; color: %s; border: none; "
            "font-size: 20px; } QPushButton:hover { color: %s; }" % (DIM, TEXT))
        self.listen_btn.clicked.connect(self._toggle_listen)

        self.status_dot = QLabel(central)
        self.status_dot.setStyleSheet("color: %s; font-size: 12px;" % MUTED)

        self.speech = Announcer(self)

        self.settings = SettingsOverlay(self.core, central)
        self.settings.closed.connect(self._close_settings)
        self.settings.hide()

        # --- keyboard ---------------------------------------------------
        for keys, slot in (
                (Qt.Key_Right, self.stack.next_page),
                (Qt.Key_Left, self.stack.previous_page),
                (Qt.Key_Space, lambda: self._toggle_lock(MODES[self.stack.index])),
        ):
            sc = QShortcut(QKeySequence(keys), self)
            sc.activated.connect(slot)
        QShortcut(QKeySequence(Qt.Key_Escape), self).activated.connect(
            self._escape)
        QShortcut(QKeySequence(Qt.Key_F11), self).activated.connect(
            self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key_S), self).activated.connect(
            self._open_settings)
        QShortcut(QKeySequence(Qt.Key_L), self).activated.connect(
            self._toggle_listen)
        QShortcut(QKeySequence(Qt.Key_G), self).activated.connect(
            self._toggle_log)

        # --- timers -----------------------------------------------------
        self._tick = QTimer(self)
        self._tick.setInterval(100)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        self.core.signal_store.subscribe(self._on_detections)
        self.core.register_status_listener(self._on_status)
        self.core.uplink_event.connect(self._on_uplink)
        self.core.zadig_status.connect(self._on_zadig)
        self.settings.driver_btn.clicked.connect(self._driver_setup)
        self.core.listening_changed.connect(self._on_listening_changed)
        self.core.listen_level.connect(self._on_listen_level)
        self._last_beep = 0.0
        self._tone_strength = -1.0
        self._spoken_strength = ""
        self._uplink_banner_until = 0.0
        self._page_changed(0)

    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_buttons()
        self.status_dot.setGeometry(14, 14, 340, 20)
        self.settings.setGeometry(0, 0, self.width(), self.height())

    def _layout_buttons(self) -> None:
        """Keep the corner controls clear of the log panel when it is open."""
        right = self.width()
        if self.log_panel.isVisible():
            right -= self.log_panel.width()
        self.gear.move(right - 56, 12)
        self.listen_btn.move(right - 106, 12)
        self.log_btn.move(right - 156, 12)

    # ------------------------------------------------------------------
    def _page_changed(self, index: int) -> None:
        self.indicator.set_index(index)
        # One receiver: it cannot keep playing a channel in a band that is no
        # longer on screen.
        if self.core.is_listening() and self.core.listening_band != MODES[index]:
            self.core.stop_listening()
        self.core.set_priority_band(MODES[index])
        self._refresh_listen_button()

    def _toggle_lock(self, key: str) -> None:
        locked = self.states[key].toggle_lock()
        self.pages[MODES.index(key)].update()
        self.core.log("%s %s" % (key, "locked" if locked else "auto"))

    def _toggle_listen(self) -> None:
        key = MODES[self.stack.index]
        if self.core.is_listening(key):
            self.core.stop_listening()
            return
        state = self.states[key]
        target = state.locked_freq_hz
        if target is None and state.current is not None:
            target = state.current.freq_hz
        if target is None:
            self.core.log("Nothing to listen to yet on %s - wait for a signal."
                          % MODE_SPECS[key].title)
            return
        state.listen_noise_dbfs = (state.current.noise_dbfs
                                   if state.current is not None else -80.0)
        problem = self.core.start_listening(key, target)
        if problem:
            self.core.log(problem)
        elif self.settings.speak.isChecked():
            self.speech.say("listen", "Listening, %s, %s"
                            % (MODE_SPECS[key].spoken or MODE_SPECS[key].title,
                               spell_frequency(target)), 0.0)

    def _on_listening_changed(self, band: str) -> None:
        for key, state in self.states.items():
            state.listening = (key == band)
        self._tone_strength = -1.0
        self._refresh_listen_button()
        for page in self.pages:
            page.update()

    def _on_listen_level(self, level_db: float) -> None:
        state = self.states.get(self.core.listening_band)
        if state is not None:
            state.push_listen_level(level_db)

    def _refresh_listen_button(self) -> None:
        key = MODES[self.stack.index]
        spec = MODE_SPECS[key]
        on = self.core.is_listening(key)
        if not spec.audio_mode:
            self.listen_btn.setEnabled(False)
            self.listen_btn.setText("🔇")
            self.listen_btn.setToolTip(spec.audio_note)
            return
        self.listen_btn.setEnabled(True)
        self.listen_btn.setText("🔊" if on
                                else "🔉")
        self.listen_btn.setToolTip(
            ("Stop listening (L)" if on else "Listen to this signal (L)")
            + ((" - " + spec.audio_note) if spec.audio_note else ""))

    def _on_zadig(self, state: str, detail: str) -> None:
        if state == "busy":
            self.settings.driver_label.setText(detail)
            self.core.log(detail)
        elif state == "ready":
            self.settings.driver_label.setText(
                "Zadig is ready. Press the button to open it, then bind the "
                "RTL-SDR to WinUSB.")
            self.settings.driver_btn.setText("Open Zadig (driver setup)")
        else:
            self.settings.driver_label.setText(detail)
            self.settings.driver_btn.setText("Retry the Zadig download")
            self.core.log(detail)

    def _driver_setup(self) -> None:
        """Download it if it is not here; open it if it is."""
        from ..core.provisioning import existing_zadig
        if existing_zadig() is None:
            self.settings.driver_label.setText("Fetching Zadig...")
            self.core.ensure_zadig()
            return
        problem = self.core.launch_zadig()
        if problem:
            self.settings.driver_label.setText(problem)
            self.core.log(problem)
        else:
            self.settings.driver_label.setText(
                "Zadig is open. Options > List All Devices, pick the RTL-SDR "
                "(often 'Bulk-In, Interface (Interface 0)'), choose WinUSB, "
                "then Replace Driver.")

    def _toggle_log(self) -> None:
        showing = not self.log_panel.isVisible()
        if showing:
            width = int(min(430, max(300, self.width() * 0.34)))
            self.log_panel.setFixedWidth(width)
            self.log_panel.refresh()
        self.log_panel.setVisible(showing)
        self.log_btn.setStyleSheet(
            "QPushButton { background: transparent; color: %s; border: none; "
            "font-size: 20px; } QPushButton:hover { color: %s; }"
            % (TEXT if showing else DIM, TEXT))
        self._layout_buttons()

    def _save_log(self) -> None:
        name = "signal_log_%s.csv" % time.strftime("%Y%m%d_%H%M%S")
        try:
            path = self.core.observations.save(name)
        except OSError as exc:
            self.core.log("Could not save the log: %s" % exc)
            return
        self.core.log("Signal log saved to %s (%d sightings)"
                      % (path, len(self.core.observations)))
        if self.settings.speak.isChecked():
            self.speech.say("saved", "Log saved, %d sightings"
                            % len(self.core.observations), 0.0)

    def _open_settings(self) -> None:
        self.settings.setGeometry(0, 0, self.width(), self.height())
        self.settings.refresh(self.core.acquisition_status())
        self.settings.show()
        self.settings.raise_()

    def _close_settings(self) -> None:
        self.settings.hide()
        if self.settings.fullscreen.isChecked() and not self.isFullScreen():
            self.showFullScreen()
        elif not self.settings.fullscreen.isChecked() and self.isFullScreen():
            self.showNormal()

    def _escape(self) -> None:
        if self.settings.isVisible():
            self._close_settings()
        elif self.isFullScreen():
            self.showNormal()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ------------------------------------------------------------------
    def _on_detections(self, detections) -> None:
        for key, state in self.states.items():
            if not state.listening:
                state.update(detections)
        for page in self.pages:
            page.update()

    def _on_uplink(self, detection) -> None:
        """A handset or vehicle radio transmitted in the uplink sub-band."""
        if not self.settings.uplink_alert.isChecked():
            return
        self._uplink_banner_until = time.time() + 8.0
        self.core.beep("Very Strong")
        QTimer.singleShot(160, lambda: self.core.beep("Strong"))
        if self.settings.speak.isChecked():
            self.speech.say(
                "uplink", "TETRA mobile radio, %s, %s"
                % (spell_frequency(detection.freq_hz),
                   proximity_label(detection.snr_db).lower()), 4.0)
        index = MODES.index(TETRA_MOBILE)
        self.pages[index].update()

    def _on_status(self, status) -> None:
        # Show the state, and which band the shared sweep is on right now, but
        # never a frequency range that contradicts the page on screen.
        state_text = status.headline().split(" MHz")[0]
        for token in (" SCANNING ", " TUNED "):
            if token in state_text:
                state_text = state_text.split(token)[0] + token.rstrip()
                break
        band = MODE_SPECS.get(self.core.rotation.current)
        if status.is_running and band is not None:
            state_text = "%s   ·   sweeping %s" % (state_text, band.title)
        self.status_dot.setText(state_text)
        colour = {"good": "#2ecc71", "warn": "#f0a020",
                  "bad": "#ef4444"}.get(status.colour_key(), MUTED)
        self.status_dot.setStyleSheet("color: %s; font-size: 12px;" % colour)
        if self.settings.isVisible():
            self.settings.refresh(status)

    def _on_tick(self) -> None:
        now = time.time()
        detections = self.core.signal_store.items()
        for state in self.states.values():
            # A parked band holds what it tuned to; the sweep is not running,
            # so the store would only go stale underneath it.
            if not state.listening:
                state.update(detections, now)
        page = self.pages[self.stack.index]
        if page.state.current is None:
            page.advance_scan_animation(0.1)
        page.update()
        if self.log_panel.isVisible():
            self.log_panel.refresh()
        self._maybe_beep(page.state, now)
        self._drive_tone()
        self._announce(page.state, now)

    def _announce(self, state, now: float) -> None:
        """Say the changes worth interrupting for, and nothing else."""
        if not self.settings.speak.isChecked():
            return
        if state.current is None:
            if self._spoken_strength:
                self._spoken_strength = ""
                self.speech.say("lost", "Signal lost", 10.0, now)
            return
        label = state.strength_label()
        if label == self._spoken_strength:
            return
        self._spoken_strength = label
        # Weak signals come and go constantly; announcing them would be chatter.
        if label in ("Medium", "Strong", "Very Strong"):
            self.speech.say("strength", "%s, %s"
                            % (state.spec.spoken or state.spec.title,
                               label.lower()), 6.0, now)

    def _drive_tone(self) -> None:
        """Keep the beeping in step with the meter the user is looking at."""
        state = self.states.get(self.core.listening_band)
        if state is None or state.spec.audio_mode != "ENV":
            return
        fraction = state.strength_fraction()
        if abs(fraction - self._tone_strength) < 0.02:
            return
        self._tone_strength = fraction
        self.core.engine_update({"tone_strength": fraction})

    def _maybe_beep(self, state, now: float) -> None:
        if not self.settings.alerts.isChecked() or state.current is None:
            return
        label = state.strength_label()
        interval = {"Medium": 2.5, "Strong": 1.2, "Very Strong": 0.6}.get(label)
        if interval is None:
            return
        if now - self._last_beep >= interval:
            self._last_beep = now
            self.core.beep(label)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._tick.stop()
        self.speech.stop()
        self.core.shutdown()
        super().closeEvent(event)
