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


def _shortcut_actions(window):
    """Every action reachable from the window that carries a shortcut."""
    from PyQt6.QtWidgets import QMenu

    found = []
    for holder in [window] + window.menuBar().findChildren(QMenu):
        found += [a for a in holder.actions() if not a.shortcut().isEmpty()]
    return found


def test_keyboard_shortcuts_are_registered_without_conflicts(window, app):
    """The application shipped with exactly one shortcut, Preferences."""
    sequences = [a.shortcut().toString() for a in _shortcut_actions(window)]
    assert len(sequences) >= 18, f"only {len(sequences)} shortcuts"
    duplicates = {s for s in sequences if sequences.count(s) > 1}
    assert not duplicates, f"two actions share a shortcut: {sorted(duplicates)}"


def test_alt_number_selects_the_matching_tab(window, app):
    """Alt+1 is the first tab, Alt+9 the ninth, Alt+0 the tenth."""
    actions = {a.shortcut().toString(): a for a in _shortcut_actions(window)}
    for key, expected in (("Alt+1", 0), ("Alt+5", 4), ("Alt+9", 8), ("Alt+0", 9)):
        assert key in actions, f"{key} not bound"
        actions[key].trigger()
        app.processEvents()
        assert window.tabs.currentIndex() == expected, key


def test_ctrl_f_focuses_the_frame_filter(window, app):
    """Ctrl+F should reach the filter box from any tab, not just FRAMES."""
    window.tabs.setCurrentIndex(window.tabs.count() - 1)
    app.processEvents()
    actions = {a.shortcut().toString(): a for a in _shortcut_actions(window)}
    actions["Ctrl+F"].trigger()
    app.processEvents()
    assert window.tabs.tabText(window.tabs.currentIndex()).startswith("FRAMES")
    assert window.frames_tab.filter_id.hasFocus()


def test_ctrl_tab_cycles_and_wraps(window, app):
    actions = {a.shortcut().toString(): a for a in _shortcut_actions(window)}
    last = window.tabs.count() - 1
    window.tabs.setCurrentIndex(last)
    app.processEvents()
    actions["Ctrl+Tab"].trigger()
    app.processEvents()
    assert window.tabs.currentIndex() == 0, "forward cycle did not wrap"
    actions["Ctrl+Shift+Tab"].trigger()
    app.processEvents()
    assert window.tabs.currentIndex() == last, "backward cycle did not wrap"


def test_the_window_is_not_wider_than_a_laptop_screen(window, app):
    """The window could not be made narrower than 1662 px.

    Fifteen tab labels in one row needed 1162 px, nine non-wrapping help
    labels added their full sentence widths, and two fixed-width side panels
    could give nothing back. Together they set a floor that did not fit a
    1440x900 screen, never mind a smaller one.
    """
    window.show()
    app.processEvents()
    window.showNormal()
    for i in range(window.tabs.count()):          # every page must be laid out
        window.tabs.setCurrentIndex(i)
        app.processEvents()
    floor = window.minimumSizeHint()
    assert floor.width() <= 1440, f"minimum width {floor.width()} px"
    assert floor.height() <= 900, f"minimum height {floor.height()} px"


def test_long_help_text_wraps(window, app):
    """A non-wrapping sentence sets a minimum width on its whole tab."""
    from PyQt6.QtWidgets import QLabel

    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        app.processEvents()
    unwrapped = [label.text()[:60] for label in window.findChildren(QLabel)
                 if len(label.text()) > 90 and " " in label.text()
                 and not label.wordWrap()]
    assert not unwrapped, f"long labels without word wrap: {unwrapped}"


def test_signals_table_populates_with_a_dbc_defined(window, app):
    """Populating this table imports canid inline, per row.

    The merge from main brought that import back in its old-layout form
    (`from core.canid import ...`), which raises the moment a row is built. No
    other test reached it, because it only runs once classification returns.
    """
    import time

    state = get_state()
    sample = (Path(__file__).resolve().parent.parent
              / "canlab" / "sample_data" / "sample_kona_drive.csv")
    state.load_frames(parse_log_file(str(sample)), "sample.csv")
    state.add_dbc_signal({
        "message_id": "0A6", "message_name": "W", "signal_name": "S",
        "start_bit": 7, "length": 16, "byte_order": "big",
        "value_type": "unsigned", "scale": 1.0, "offset": 0.0,
        "min_val": 0, "max_val": 100, "unit": "", "description": ""})
    app.processEvents()

    window.signals_tab._run_classify()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and window.signals_tab.table.rowCount() == 0:
        app.processEvents()
        time.sleep(0.02)
    assert window.signals_tab.table.rowCount() > 0, "classification produced no rows"


def test_close_disarms_and_stops_workers(window, app):
    """Runs last: closing the window must disarm TX and stop every worker."""
    safety.set_armed(True)
    window.close()
    app.processEvents()
    assert not safety.is_armed()
