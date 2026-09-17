"""A worker for long sweeps that report progress and can be cancelled."""
import time

import pytest

pytest.importorskip("PyQt6")

from canlab.ui.compute_worker import ProgressWorker


def _sweep(n, *, progress_cb=None, should_stop=None):
    done = 0
    for i in range(n):
        if should_stop is not None and should_stop():
            break
        time.sleep(0.005)
        done += 1
        if progress_cb is not None:
            progress_cb(done, n)
    return done


def _spin(qcore, worker, timeout=5.0):
    deadline = time.monotonic() + timeout
    while worker.isRunning() and time.monotonic() < deadline:
        qcore.processEvents()
        time.sleep(0.005)
    qcore.processEvents()


def test_progress_is_reported_and_the_result_delivered(qcore):
    seen, result = [], []
    worker = ProgressWorker(_sweep, 8)
    worker.progress.connect(lambda d, t: seen.append((d, t)))
    worker.done.connect(result.append)
    worker.start()
    _spin(qcore, worker)
    assert result == [8]
    assert seen[-1] == (8, 8) and len(seen) == 8


def test_stop_ends_the_sweep_early(qcore):
    result = []
    worker = ProgressWorker(_sweep, 400)
    worker.done.connect(result.append)
    worker.start()
    time.sleep(0.05)
    worker.stop()
    _spin(qcore, worker)
    assert worker.stopped
    assert result and result[0] < 400


def test_a_callable_without_the_hooks_still_runs(qcore):
    """The hooks are injected only as defaults, so a plain function works."""
    result = []
    worker = ProgressWorker(lambda x, progress_cb=None, should_stop=None: x * 2, 21)
    worker.done.connect(result.append)
    worker.start()
    _spin(qcore, worker)
    assert result == [42]
