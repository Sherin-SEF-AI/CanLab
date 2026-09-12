"""Keep references to short-lived QThreads and stop them all on teardown."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class WorkerPool:
    """Holds QThread workers until they finish; ``stop_all`` joins the rest."""

    def __init__(self):
        self._workers: list = []

    def add(self, worker):
        self._workers.append(worker)
        worker.finished.connect(lambda w=worker: self._discard(w))
        return worker

    def _discard(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)

    def __len__(self) -> int:
        return len(self._workers)

    def stop_all(self, timeout_ms: int = 2000) -> None:
        for w in list(self._workers):
            try:
                if hasattr(w, "stop"):
                    w.stop()
                else:
                    w.requestInterruption()
                    w.quit()
                if w.isRunning():
                    w.wait(timeout_ms)
            except Exception:
                log.debug("worker stop failed", exc_info=True)
        self._workers.clear()
