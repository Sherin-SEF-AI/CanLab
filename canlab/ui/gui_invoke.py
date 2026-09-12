"""Run a callable on the GUI thread and wait for its result.

Servers that live in the window (REST, MCP) answer requests on their own
threads, and some of those requests change application state: load a
capture, add a signal. AppState is a QObject whose signals fan out to
widgets, so those changes have to happen on the GUI thread. This hands the
callable over with a blocking queued connection and returns what it returned,
or re-raises what it raised.

Calling it from the GUI thread itself runs the callable directly, since
blocking on the thread you are on would never return.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot


class _Job:
    __slots__ = ("fn", "result", "error")

    def __init__(self, fn):
        self.fn = fn
        self.result = None
        self.error = None


class GuiInvoker(QObject):
    _call = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._call.connect(self._run, Qt.ConnectionType.BlockingQueuedConnection)

    @pyqtSlot(object)
    def _run(self, job: _Job) -> None:
        try:
            job.result = job.fn()
        except Exception as e:           # handed back to the calling thread
            job.error = e

    def __call__(self, fn):
        if QThread.currentThread() is self.thread():
            return fn()
        job = _Job(fn)
        self._call.emit(job)
        if job.error is not None:
            raise job.error
        return job.result
