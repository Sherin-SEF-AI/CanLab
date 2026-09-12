"""Coalesce bursty signals into at most one callback per interval.

Live capture emits ``frames_updated`` several times a second; a listener that
rebuilds a table or re-runs analysis on every emission cannot keep up. Wrap the
slot in a Coalescer and it runs once per interval no matter how often it is
poked, with the trailing call guaranteed.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer

DEFAULT_MS = 250


class Coalescer(QObject):
    def __init__(self, callback, interval_ms: int = DEFAULT_MS, parent=None):
        super().__init__(parent)
        self._callback = callback
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._fire)

    def poke(self) -> None:
        """Request a run; starts the interval if one is not already pending."""
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:
        """Run now, cancelling any pending run."""
        self._timer.stop()
        self._fire()

    def stop(self) -> None:
        self._timer.stop()

    def _fire(self) -> None:
        self._callback()
