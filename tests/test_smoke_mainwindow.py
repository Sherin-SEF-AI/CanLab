"""End-to-end smoke test: build the real window, load the sample, use every tab.

This is the test that catches wiring breakage the unit tests cannot see —
a removed signal a tab still connects to, a renamed helper, a tab that fails
to build. It runs headless (QT_QPA_PLATFORM=offscreen, set in conftest).
"""
import time

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from PyQt6.QtWidgets import QApplication

from canlab.core import safety
from canlab.core.log_parser import parse_log_file
from canlab.core.state import get_state
from tests.doubles import FakeMsg

SAMPLE = "canlab/sample_data/sample_kona_drive.csv"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def window(app):
    """One window for the module — AppState is a process-wide singleton, so a
    second window would leave the first one's tabs wired to the same signals."""
    from canlab.mainwindow import MainWindow
    w = MainWindow()
    yield w
    w.close()
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
