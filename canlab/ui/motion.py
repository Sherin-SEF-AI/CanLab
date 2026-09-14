"""Animation primitives, and the gates that decide when not to animate.

Qt stylesheets have no transitions, so everything here is driven by
``QPropertyAnimation``. Three rules are built into ``animate`` rather than left
to each call site, because each of them is a bug the old timer-based
animations actually had:

**One animation per widget and property.** Re-triggering cancels the one in
flight instead of stacking a second onto the same value. The animation is
found by object name on the target, so there is no registry to keep in step.

**Parented to the target.** The animation dies with the widget. A bare
``QTimer.singleShot`` closure over a widget, which is how the old flash
worked, calls into freed memory if the widget goes away inside its delay.

**Gated.** ``Motion.off()`` decides centrally whether to animate at all:

- *headless* is decided once at import from ``QT_QPA_PLATFORM``. The whole
  test suite and both demo recorders run offscreen, so they get end states
  immediately and no screenshot is ever captured mid-transition.
- *reduce* is the user's preference.
- *busy* is set while a live capture is running. The GUI thread is already
  committed to a 250 ms drain, a 300 ms frames coalescer and a 200 ms sniffer
  tick, and decorative motion must not compete with them.

Safety-bearing motion passes ``critical=True`` and ignores *busy*: the armed
glow and the connected pulse have to keep moving precisely because the bus is
live. Suppressing them would remove the signal at the moment it matters.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import (
    QAbstractAnimation, QEasingCurve, QObject, QPropertyAnimation, QVariantAnimation,
)

# Durations, in milliseconds.
DURATION = {"fast": 110, "base": 180, "slow": 260, "count": 400, "pulse": 1200}


class Motion:
    """The global gates. Cheap to read; consulted before every animation."""

    reduce = False      # the user's preference, from Settings
    busy = False        # a live capture is running
    _headless = os.environ.get("QT_QPA_PLATFORM") == "offscreen"

    @classmethod
    def off(cls, critical: bool = False) -> bool:
        if cls._headless or cls.reduce:
            return True
        return cls.busy and not critical

    @classmethod
    def set_capturing(cls, capturing: bool) -> None:
        cls.busy = bool(capturing)


def _duration(dur) -> int:
    return dur if isinstance(dur, int) else DURATION.get(dur, DURATION["base"])


def animate(target, prop: bytes, to, *, dur="base", frm=None,
            ease=QEasingCurve.Type.OutCubic, instant: bool = False,
            critical: bool = False, loop: int = 1, on_done=None):
    """Animate a Qt property, replacing any animation already on it.

    Returns the animation, or None when motion is off and the end state was
    applied directly.
    """
    name = f"__anim_{prop.decode()}"
    existing = target.findChild(QAbstractAnimation, name)
    if existing is not None:
        # stop() on a DeleteWhenStopped animation already schedules deletion;
        # calling deleteLater() as well would be a double free.
        existing.stop()

    if instant or Motion.off(critical):
        target.setProperty(prop.decode(), to)
        if on_done is not None:
            on_done()
        return None

    anim = QPropertyAnimation(target, prop, target)
    anim.setObjectName(name)
    anim.setDuration(_duration(dur))
    anim.setEasingCurve(ease)
    anim.setStartValue(target.property(prop.decode()) if frm is None else frm)
    anim.setEndValue(to)
    anim.setLoopCount(loop)
    if on_done is not None:
        anim.finished.connect(on_done)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def interpolate(owner: QObject, start, end, setter, *, dur="base",
                ease=QEasingCurve.Type.OutCubic, critical: bool = False,
                on_done=None):
    """Drive an arbitrary setter from start to end.

    For values that are not Qt properties, such as a splitter's pane width or
    a number rendered into a label.
    """
    if Motion.off(critical):
        setter(end)
        if on_done is not None:
            on_done()
        return None
    anim = QVariantAnimation(owner)
    anim.setDuration(_duration(dur))
    anim.setEasingCurve(ease)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.valueChanged.connect(setter)
    anim.finished.connect(lambda: setter(end))
    if on_done is not None:
        anim.finished.connect(on_done)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


class SplitterAnimator(QObject):
    """Animates one pane of a QSplitter, giving it a Qt property to drive.

    A splitter owns its children's geometry, so animating a panel's own
    ``maximumWidth`` puts two things in charge of the same number and the drag
    handle and the animation disagree. This changes the splitter's own sizes
    instead, taking the difference out of the pane that stretches, so the
    splitter stays the single owner.
    """

    def __init__(self, splitter, index: int, absorber: int, parent=None):
        super().__init__(parent or splitter)
        self._splitter = splitter
        self._index = index
        self._absorber = absorber

    def width(self) -> int:
        sizes = self._splitter.sizes()
        return sizes[self._index] if self._index < len(sizes) else 0

    def set_width(self, value) -> None:
        value = max(0, int(value))
        sizes = self._splitter.sizes()
        if self._index >= len(sizes):
            return
        delta = value - sizes[self._index]
        sizes[self._index] = value
        if self._absorber < len(sizes):
            sizes[self._absorber] = max(1, sizes[self._absorber] - delta)
        self._splitter.setSizes(sizes)

    def animate_to(self, value: int, *, instant: bool = False, on_done=None):
        if instant or Motion.off():
            self.set_width(value)
            if on_done is not None:
                on_done()
            return None
        return interpolate(self, self.width(), max(0, int(value)),
                           self.set_width, dur="slow", on_done=on_done)
