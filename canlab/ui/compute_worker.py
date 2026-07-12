"""Reusable background-compute QThread.

Runs a plain callable off the GUI thread and delivers the result (or the error)
back via signals, so heavy analysis doesn't freeze the UI. The callable must be
self-contained (pure compute) — do NOT touch Qt widgets from inside it.

Usage::

    self._worker = ComputeWorker(compute_fn, arg1, arg2)
    self._worker.done.connect(self._on_done)      # receives the return value
    self._worker.failed.connect(self._on_failed)  # receives an error string
    self._worker.start()

Store the worker on ``self`` so it isn't garbage-collected mid-run.
"""
from PyQt6.QtCore import QThread, pyqtSignal


class ComputeWorker(QThread):
    done   = pyqtSignal(object)   # the callable's return value
    failed = pyqtSignal(str)      # error message

    def __init__(self, fn, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn     = fn
        self._args   = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.done.emit(result)
