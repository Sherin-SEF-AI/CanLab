"""Background analysis threads: stopping them, and not dropping them.

The signals tab classifies every ID on a worker thread. Loading a second
capture while the first was still being classified rebound ``self._worker`` to
a new thread, which dropped the only Python reference to a QThread that was
still inside pandas. Qt then destroys the thread object under the running
thread, which aborts the process.

On the 155 000 frame capture the classifier takes about six seconds, almost all
of it probing one message for checksums, so the window for this is not small.
"""
import time

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pandas")

import pandas as pd

from canlab.core.signal_analyzer import analyze_all
from canlab.core.signal_embedding import build_index
from canlab.ui.worker_pool import WorkerPool


def sample(n_ids: int = 6, per_id: int = 400) -> pd.DataFrame:
    rows = []
    for i in range(n_ids):
        for k in range(per_id):
            rows.append({
                "Timestamp": k * 0.01, "ID": f"{0x100 + i:03X}",
                "Bus": "0", "DLC": 8, "Extended": False,
                **{f"B{b}": (k * (b + 1)) % 256 for b in range(8)},
            })
    return pd.DataFrame(rows)


# ── the loops can be abandoned ───────────────────────────────────────────────

def test_analyze_all_stops_when_told_to():
    df = sample()
    full = analyze_all(df)
    assert len(full) > 1

    calls = {"n": 0}

    def stop_after_one():
        calls["n"] += 1
        return calls["n"] > 3
    partial = analyze_all(df, should_stop=stop_after_one)
    assert len(partial) < len(full), "the walk ran to completion anyway"


def test_analyze_all_without_a_stop_check_is_unchanged():
    df = sample()
    assert len(analyze_all(df, should_stop=None)) == len(analyze_all(df))


def test_the_stop_check_reaches_the_slow_part():
    """Nearly all the time goes into the checksum probe inside one message. A
    check that only ran between messages would not help a capture whose slow
    message is the first one."""
    df = sample(n_ids=1, per_id=2000)
    seen = {"n": 0}

    def count():
        seen["n"] += 1
        return False
    analyze_all(df, should_stop=count)
    assert seen["n"] > 1, "the check was consulted once per message only"


def test_build_index_stops_when_told_to():
    df = sample()
    assert len(build_index(df)) > 1
    assert build_index(df, should_stop=lambda: True) == {}


# ── the pool does not let go of a thread that is still running ───────────────

class _Stubborn:
    """Stands in for a QThread that ignores every request to stop."""

    def __init__(self):
        self.interrupted = False
        self.finished = _Signal()

    def requestInterruption(self):      # noqa: N802 - Qt spelling
        self.interrupted = True

    def quit(self):
        pass

    def isRunning(self):                # noqa: N802 - Qt spelling
        return True

    def wait(self, _ms):
        return False                     # never stops


class _Obedient(_Stubborn):
    def isRunning(self):                # noqa: N802 - Qt spelling
        return False


class _Signal:
    def connect(self, _fn):
        pass


def test_a_worker_that_will_not_stop_keeps_its_reference():
    """Clearing the list would let Python collect the wrapper while the C++
    thread is still running, which is the crash this whole file is about."""
    pool = WorkerPool()
    stubborn = pool.add(_Stubborn())
    left = pool.stop_all(timeout_ms=1)
    assert stubborn.interrupted
    assert left == [stubborn]
    assert len(pool) == 1, "a running thread was dropped"


def test_a_worker_that_stops_is_released():
    pool = WorkerPool()
    pool.add(_Obedient())
    assert pool.stop_all(timeout_ms=1) == []
    assert len(pool) == 0


# ── the tab ──────────────────────────────────────────────────────────────────

def test_a_second_classify_stops_the_first(qcore):
    from canlab.core.state import get_state
    from canlab.tabs.signals_tab import SignalsTab

    state = get_state()
    state.load_frames(sample(n_ids=12, per_id=600), "big")
    tab = SignalsTab()
    tab.show()
    qcore.processEvents()

    tab._run_classify()
    first = tab._worker
    assert first is not None
    tab._run_classify()
    second = tab._worker
    assert second is not first, "the same worker was reused"
    assert not first.isRunning(), "the first worker was left running"

    tab.cleanup()
    deadline = time.monotonic() + 10
    while tab._worker is not None and time.monotonic() < deadline:
        qcore.processEvents()
    assert tab._worker is None
    tab.deleteLater()
    qcore.processEvents()


def test_cleanup_leaves_nothing_running(qcore):
    from canlab.core.state import get_state
    from canlab.tabs.signals_tab import SignalsTab

    get_state().load_frames(sample(), "small")
    tab = SignalsTab()
    tab.show()
    qcore.processEvents()
    tab._run_classify()
    tab.cleanup()
    assert len(tab._pool) == 0, "a worker outlived the tab that owns it"
    tab.deleteLater()
    qcore.processEvents()


def test_cleanup_stops_the_live_watch_timer(qcore):
    from canlab.core.state import get_state
    from canlab.tabs.signal_intelligence_tab import SignalIntelligenceTab

    get_state().load_frames(sample(), "small")
    tab = SignalIntelligenceTab()
    tab._watch.fit(sample())
    tab.chk_watch_autofit.setChecked(False)
    tab.btn_watch_toggle.setChecked(True)
    assert tab.watch_active and get_state().live_watch_running
    tab.cleanup()
    assert not tab.watch_active and not get_state().live_watch_running
    assert len(tab._pool) == 0
    tab.deleteLater()
    qcore.processEvents()
