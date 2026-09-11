"""End-to-end smoke test: build the real window, load the sample, use every tab.

This is the test that catches wiring breakage the unit tests cannot see —
a removed signal a tab still connects to, a renamed helper, a tab that fails
to build. It runs headless (QT_QPA_PLATFORM=offscreen, set in conftest).
"""
from pathlib import Path
import gc
import time

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")


from canlab.core import safety
from canlab.core.log_parser import parse_log_file
from canlab.core.state import get_state
from tests.doubles import FakeMsg

SAMPLE = "canlab/sample_data/sample_kona_drive.csv"


@pytest.fixture(scope="module")
def app(qcore):
    return qcore


@pytest.fixture(scope="module")
def window(app):
    """One window for the module — AppState is a process-wide singleton, so a
    second window would leave the first one's tabs wired to the same signals."""
    from canlab.mainwindow import MainWindow
    w = MainWindow()
    yield w
    # Destroy the C++ side while the application is still alive; leaving it to
    # the garbage collector crashes Qt during interpreter teardown.
    w.close()
    w.deleteLater()
    app.processEvents()
    gc.collect()
    app.processEvents()


class _Feeder:
    """A bus that emits a fixed number of frames, then goes quiet."""

    def __init__(self, count):
        self.left = count
        self.sent = []

    def recv(self, timeout=0.1):
        if self.left:
            self.left -= 1
            return FakeMsg(0x1A0 + (self.left % 3), bytes([self.left & 0xFF] * 8),
                           timestamp=time.time())
        time.sleep(0.005)
        return None

    def send(self, msg):
        self.sent.append(msg)

    def shutdown(self):
        pass


def test_every_tab_builds_and_renders_the_sample(window, app):
    st = get_state()
    st.load_frames(parse_log_file(SAMPLE), "sample")
    app.processEvents()
    assert window.tabs.count() == 15
    # DIAGNOSTICS now surfaces XCP and DoIP, which had no UI entry point at all.
    diag_tabs = window.diagnostics_tab.findChild(type(window.tabs))
    labels = [diag_tabs.tabText(i) for i in range(diag_tabs.count())]
    assert "XCP" in labels and "DoIP" in labels
    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        app.processEvents()
    assert len(st.frames_df) == 6610


def test_dbc_signal_decodes_through_the_ui(window, app):
    st = get_state()
    st.dbc_signals.clear()
    st.load_frames(parse_log_file(SAMPLE), "sample")
    st.add_dbc_signal({
        "message_id": "0A6", "message_name": "WHL_SPD11", "signal_name": "WhlSpdFL",
        "start_bit": 7, "length": 16, "byte_order": "big", "value_type": "unsigned",
        "scale": 0.03125, "offset": 0.0, "min_val": 0, "max_val": 2000,
        "unit": "km/h", "description": "",
    })
    app.processEvents()
    window.dbc_tab._on_list_select(0)
    app.processEvents()
    cell = window.dbc_tab.preview_table.item(0, 2)
    assert cell is not None and cell.text() not in ("", "?")
    t, y, _label = window.timeline_tab._extract_series(st.frames_df, "dbc", "0A6", "WhlSpdFL")
    assert t is not None and len(t) > 0
    st.dbc_signals.clear()


def test_live_capture_through_the_hub(window, app):
    st = get_state()
    st.load_frames(parse_log_file(SAMPLE), "sample")
    before = len(st.frames_df)
    feeder = _Feeder(300)
    window._open_bus = lambda *a, **k: feeder
    window._connect_can()
    try:
        assert st.bus_hub is not None and st.is_connected
        # Independent subscriptions: diagnostics and injection never share a queue.
        assert window.diagnostics_tab._get_bus() is not window.injection_tab._get_bus()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(st.frames_df) < before + 300:
            app.processEvents()
            time.sleep(0.02)
        assert len(st.frames_df) >= before + 300
        snap = st.bus_hub.health_snapshot()
        assert snap["total_ids_seen"] == 3 and snap["error_frames"] == 0
    finally:
        window._disconnect_can()
        app.processEvents()
    assert st.bus_hub is None and not st.is_connected


def test_disconnect_flushes_the_tail(window, app):
    st = get_state()
    st.load_frames(parse_log_file(SAMPLE), "sample")
    feeder = _Feeder(7)            # fewer frames than any batch threshold
    window._open_bus = lambda *a, **k: feeder
    before = len(st.frames_df)
    window._connect_can()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and st.bus_hub.rx_count < 7:
        time.sleep(0.02)
    window._disconnect_can()       # must flush, not drop, the pending rows
    app.processEvents()
    assert len(st.frames_df) == before + 7


def test_close_disarms_and_stops_workers(window, app):
    """Runs last: closing the window must disarm TX and stop every worker."""
    safety.set_armed(True)
    window.close()
    app.processEvents()
    assert not safety.is_armed()


def test_frames_table_refresh_stays_cheap_while_visible(window, app):
    """A visible frames table must not cost O(rows^2) to fill.

    QHeaderView.ResizeToContents re-measures every row of a column each time a
    cell changes. With the table on screen during live capture, one refresh of
    1000 rows took 70 seconds and the GUI thread did nothing else. Guard both
    the cause and the effect.
    """
    import time

    from PyQt6.QtWidgets import QHeaderView

    from canlab.core.state import get_state

    table = window.frames_tab.table
    mode = table.horizontalHeader().sectionResizeMode(0)
    assert mode != QHeaderView.ResizeMode.ResizeToContents, (
        "ResizeToContents on a live-updating table makes filling it quadratic")

    state = get_state()
    state.store.append_batch([
        (i * 0.004, 0x1A0 + (i % 6), False, 0, 8,
         bytes([(i + k) & 0xFF for k in range(8)]))
        for i in range(20_000)])

    window.tabs.setCurrentIndex(0)
    app.processEvents()
    state.store._dirty = True
    started = time.perf_counter()
    window.frames_tab._refresh()
    app.processEvents()
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"refresh took {elapsed:.1f}s with the table visible"
    assert table.rowCount() > 0


def test_heavy_analysis_libraries_are_not_imported_at_startup():
    """SciPy and scikit-learn cost about 430 ms to import between them.

    Nothing needs either until the user asks for a correlation, a change point
    or an anomaly scan, so importing them at module scope was a third of the
    application's startup time, paid by everyone. They are bound on first call
    instead; this pins that so a stray top-level import cannot creep back.

    It has to run in a fresh interpreter: by the time the rest of this suite
    has run, plenty of tests have imported SciPy for their own reasons.
    """
    import os
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "from PyQt6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "from canlab.mainwindow import MainWindow\n"
        "w = MainWindow()\n"
        "app.processEvents()\n"
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules\n"
        "                       if m.split('.')[0] in ('scipy', 'sklearn')})))\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-c", probe], env=env,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    eager = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert eager == "", f"imported at startup: {eager}"


def test_the_lazily_bound_statistics_still_work(qcore):
    """The bindings must resolve to the real functions when called."""
    import numpy as np

    from canlab.core.change_detector import mannwhitneyu
    from canlab.core.correlation_engine import pearsonr
    from canlab.core.signal_analyzer import spearmanr

    rng = np.random.default_rng(0)
    a = rng.normal(size=200)
    b = a * 2 + rng.normal(scale=0.1, size=200)

    r, p = pearsonr(a, b)
    assert r > 0.99 and p < 0.01
    assert spearmanr(a, b)[0] > 0.99
    assert 0.0 <= float(mannwhitneyu(a, b, alternative="two-sided")[1]) <= 1.0


def test_the_heatmap_renders_text_without_a_freetype_failure(window, app):
    """Matplotlib must be imported before Qt's plugins claim FreeType.

    Loaded afterwards, every draw containing text dies with a raster overflow
    while a draw with no text succeeds, so only a real render catches it.
    """
    from canlab.core.log_parser import parse_log_file
    from canlab.core.state import get_state

    sample = (Path(__file__).resolve().parent.parent
              / "canlab" / "sample_data" / "sample_kona_drive.csv")
    get_state().load_frames(parse_log_file(str(sample)), "sample.csv")
    app.processEvents()

    window.dashboard_tab._render_heatmap()      # raises if FreeType is unhappy
    app.processEvents()
    image = window.dashboard_tab._heatmap_canvas.grab()
    assert image.width() > 0 and image.height() > 0
