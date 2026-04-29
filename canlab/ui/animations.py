"""Reusable animation widgets and helpers for CANLAB."""
import math
from PyQt6.QtWidgets import QWidget, QLabel
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    pyqtProperty, QRectF,
)
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QFont

from theme import COLORS


# ── Rotating arc spinner ──────────────────────────────────────────────────────

class SpinnerWidget(QWidget):
    """Small rotating arc shown during long-running operations."""

    def __init__(self, size: int = 24, color: str | None = None, parent=None):
        super().__init__(parent)
        self._angle   = 0
        self._color   = color or COLORS["green"]
        self._running = False
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._running = True
        self._timer.start()
        self.show()

    def stop(self):
        self._running = False
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        margin = 3
        rect = QRectF(margin, margin, w - 2 * margin, w - 2 * margin)
        pen = QPen(QColor(self._color), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, self._angle * 16, 270 * 16)
        p.end()


# ── Pulsing dot (live CAN indicator) ─────────────────────────────────────────

class PulsingDot(QWidget):
    """Small circle that pulses between bright and dim to indicate live state."""

    def __init__(self, color: str | None = None, size: int = 10, parent=None):
        super().__init__(parent)
        self._color   = color or COLORS["green"]
        self._alpha   = 255
        self._dir     = -6
        self._active  = False
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    def set_active(self, active: bool):
        self._active = active
        if active:
            self._timer.start()
        else:
            self._timer.stop()
            self._alpha = 80
        self.update()

    def _tick(self):
        self._alpha += self._dir * 4
        if self._alpha <= 60:
            self._alpha = 60
            self._dir = 1
        elif self._alpha >= 255:
            self._alpha = 255
            self._dir = -1
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self._color)
        c.setAlpha(self._alpha if self._active else 60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(c))
        r = self.width() // 2 - 1
        p.drawEllipse(1, 1, r * 2, r * 2)
        p.end()


# ── Animated integer counter label ────────────────────────────────────────────

class CountUpLabel(QLabel):
    """Label that animates from its current numeric value to a target."""

    def __init__(self, text: str = "0", suffix: str = "", parent=None):
        super().__init__(text, parent)
        self._target  = 0
        self._current = 0
        self._suffix  = suffix
        self._timer   = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def animate_to(self, value: int):
        self._target  = value
        self._current = max(0, self._current)
        self._timer.start()

    def _tick(self):
        diff = self._target - self._current
        if abs(diff) <= 1:
            self._current = self._target
            self._timer.stop()
        else:
            self._current += max(1, diff // 6)
        self.setText(f"{self._current:,} {self._suffix}".strip())


# ── Button pulse helper ───────────────────────────────────────────────────────

class ButtonPulse:
    """Cycles a button's stylesheet to create a pulsing glow while active."""

    _STEPS = [
        f"QPushButton {{ color:{COLORS['amber']}; border:1px solid {COLORS['amber']}; background:{COLORS['panel_bg']}; }}",
        f"QPushButton {{ color:{COLORS['amber']}; border:2px solid {COLORS['amber']}; background:#2a1800; }}",
        f"QPushButton {{ color:#ffffff;            border:2px solid #ffdd88;          background:#3a2200; }}",
        f"QPushButton {{ color:{COLORS['amber']}; border:2px solid {COLORS['amber']}; background:#2a1800; }}",
    ]

    def __init__(self, button):
        self._btn   = button
        self._step  = 0
        self._orig  = button.styleSheet()
        self._timer = QTimer()
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._step = 0
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self._btn.setStyleSheet(self._orig)

    def _tick(self):
        self._btn.setStyleSheet(self._STEPS[self._step % len(self._STEPS)])
        self._step += 1


# ── Scan-line flash (for text areas receiving new data) ───────────────────────

def flash_widget(widget: QWidget, color: str = COLORS["green"], duration_ms: int = 300):
    """Briefly set a widget's background to `color` then fade back."""
    orig = widget.styleSheet()
    base_bg = COLORS["panel_bg"]
    widget.setStyleSheet(orig + f"; background: {color}22;")
    QTimer.singleShot(duration_ms, lambda: widget.setStyleSheet(orig))


# ── Typewriter cursor blink ───────────────────────────────────────────────────

class TypewriterCursor:
    """Appends a blinking block cursor to a QTextEdit while streaming."""

    _CURSOR = " █"

    def __init__(self, text_edit):
        self._te      = text_edit
        self._visible = False
        self._timer   = QTimer()
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._visible = True
        self._timer.start()

    def stop(self):
        self._timer.stop()
        # Remove trailing cursor block if present
        txt = self._te.toPlainText()
        if txt.endswith(self._CURSOR):
            self._te.setPlainText(txt[: -len(self._CURSOR)])

    def _tick(self):
        txt = self._te.toPlainText()
        if self._visible:
            if not txt.endswith(self._CURSOR):
                self._te.moveCursor(self._te.textCursor().MoveOperation.End)
                self._te.insertPlainText(self._CURSOR)
        else:
            if txt.endswith(self._CURSOR):
                # Remove via document manipulation to avoid scroll jump
                cur = self._te.textCursor()
                cur.movePosition(cur.MoveOperation.End)
                for _ in range(len(self._CURSOR)):
                    cur.deletePreviousChar()
                self._te.setTextCursor(cur)
        self._visible = not self._visible
