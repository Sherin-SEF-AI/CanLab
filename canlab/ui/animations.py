"""Reusable animation widgets and helpers for CANLAB."""
from PyQt6.QtWidgets import QWidget, QLabel
from PyQt6.QtCore import (
    QEasingCurve, Qt, QTimer, QRectF, QVariantAnimation,
)
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush

from canlab.theme import COLORS


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
    """Label that animates from its current numeric value to a target.

    It used to step by ``max(1, diff // 6)`` on a 16 ms timer. Counting up
    that works; counting down, ``diff`` is negative, ``diff // 6`` floors to a
    large negative, and ``max(1, ...)`` returns 1 -- so the value moved one
    step *away* from the target on every tick, the timer never reached its
    stop condition, and the status bar counted upward forever showing a number
    that was simply false. Loading a small capture after a large one was
    enough to trigger it, as was Trim capture.

    A QVariantAnimation eases correctly in both directions, takes the same
    time regardless of distance, and stops on its own.
    """

    DURATION_MS = 400

    def __init__(self, text: str = "0", suffix: str = "", parent=None):
        super().__init__(text, parent)
        self._current = 0
        self._target = 0
        self._suffix = suffix
        self._anim: QVariantAnimation | None = None

    def animate_to(self, value: int):
        """Ease to `value`, reusing this label's one animation.

        The animation is parented to the label and never self-deletes. It used
        to be created per call with DeleteWhenStopped *and* a parent, which
        gives the object two owners: destroying the label frees the animation,
        and the stop that destruction triggers frees it again. That is a
        segfault with no Python traceback, surfacing at whatever unrelated
        point the event loop next runs.
        """
        from canlab.ui.motion import Motion

        target = max(0, int(value))
        if self._anim is not None:
            self._anim.stop()
        # This counter is the frame total in the status bar. It sat outside the
        # reduce-motion and live-capture gates that every other animation
        # obeys, so a capture running at full rate was still easing a number
        # four hundred milliseconds at a time.
        if target == self._current or Motion.off():
            self._show(target)
            return
        anim = self._animation()
        self._target = target
        anim.setStartValue(int(self._current))
        anim.setEndValue(target)
        anim.start()

    def _animation(self) -> QVariantAnimation:
        if self._anim is None:
            anim = QVariantAnimation(self)
            anim.setDuration(self.DURATION_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            # Bound methods, not closures over a loop variable: Qt drops these
            # connections when the label is destroyed.
            anim.valueChanged.connect(self._on_step)
            anim.finished.connect(self._on_finished)
            self._anim = anim
        return self._anim

    def _on_step(self, value) -> None:
        self._show(int(value))

    def _on_finished(self) -> None:
        self._show(self._target)

    def _show(self, value: int) -> None:
        self._current = value
        self.setText(f"{value:,} {self._suffix}".strip())


class ButtonPulse:
    """Draw attention to a button while a long job runs.

    It used to swap four complete stylesheet strings on a 180 ms timer. Each
    assignment re-parses the sheet and invalidates the widget's whole styled
    subtree, which is an expensive way to change one colour. This animates the
    button's own opacity instead, through the shared motion module, so it
    obeys the reduce-motion and live-capture gates like everything else.
    """

    def __init__(self, button):
        self._button = button
        self._effect = None
        self._anim = None

    def start(self):
        from PyQt6.QtWidgets import QGraphicsOpacityEffect

        from canlab.ui.motion import DURATION, Motion, animate
        if self._anim is not None or Motion.off():
            return
        if self._effect is None:
            self._effect = QGraphicsOpacityEffect(self._button)
            self._button.setGraphicsEffect(self._effect)
        self._effect.setOpacity(1.0)
        self._anim = animate(self._effect, b"opacity", 0.45,
                             dur=DURATION["pulse"] // 2, frm=1.0, loop=-1)

    def stop(self):
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if self._effect is not None:
            self._effect.setOpacity(1.0)


def flash_widget(widget, color: str = None, duration_ms: int = 300):
    """Briefly tint a widget to say something happened.

    The old version appended to the widget's stylesheet and restored it from a
    bare QTimer.singleShot, so a widget destroyed inside the delay was written
    to after deletion. This drives a colour property through the motion
    module, which parents the animation to the widget.
    """
    from PyQt6.QtWidgets import QGraphicsOpacityEffect

    from canlab.ui.motion import Motion, animate
    if Motion.off():
        return
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setOpacity(0.35)
    animate(effect, b"opacity", 1.0, dur=duration_ms, frm=0.35)


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
