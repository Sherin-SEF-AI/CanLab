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
import threading

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


class ProgressWorker(ComputeWorker):
    """A ComputeWorker for callables that report progress and can be stopped.

    The callable receives two extra keyword arguments: ``progress_cb(done,
    total)``, which forwards to the ``progress`` signal, and ``should_stop()``,
    which returns True once ``stop()`` has been called. That is the contract
    ``run_correlation_sweep`` and ``build_index`` already follow, so a long
    sweep can drive a progress bar and honour a Cancel button without knowing
    anything about Qt.
    """

    progress = pyqtSignal(int, int)   # done, total

    def __init__(self, fn, *args, parent=None, **kwargs):
        super().__init__(fn, *args, parent=parent, **kwargs)
        self._stop = threading.Event()
        self._kwargs.setdefault("progress_cb", self.progress.emit)
        self._kwargs.setdefault("should_stop", self._stop.is_set)

    def stop(self) -> None:
        self._stop.set()
        self.requestInterruption()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()
