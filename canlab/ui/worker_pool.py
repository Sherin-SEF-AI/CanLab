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

    def stop_all(self, timeout_ms: int = 5000) -> list:
        """Stop every worker and wait for it. Returns the ones that would not.

        The list used to be cleared whether or not the threads had actually
        stopped. A worker whose ``run`` is a long pandas loop ignores both
        ``quit`` and ``requestInterruption`` until it next looks, so clearing
        the list let the interpreter tear down while a thread was still inside
        numpy, which segfaults rather than raising.
        """
        stubborn = []
        for w in list(self._workers):
            try:
                if hasattr(w, "stop"):
                    w.stop()
                else:
                    w.requestInterruption()
                    w.quit()
                if w.isRunning() and not w.wait(timeout_ms):
                    stubborn.append(w)
                    log.warning("worker %s did not stop within %d ms",
                                type(w).__name__, timeout_ms)
            except Exception:
                log.debug("worker stop failed", exc_info=True)
        # Anything still running keeps its reference, so it is not collected
        # out from under itself.
        self._workers = stubborn
        return stubborn
