"""The one place that says what is connected.

The answer used to be spread across the window: a dot in the status bar for
the bus, two tinted toolbar buttons for the servers, and the URL only in a
status message that had already scrolled away.
"""
import pytest

pytest.importorskip("PyQt6")

from canlab.ui.status_button import ConnectionStatus


@pytest.fixture
def button(qcore):
    b = ConnectionStatus()
    yield b
    b.deleteLater()
    qcore.processEvents()


def test_nothing_connected_says_so(button):
    assert button.connected() == []
    assert "nothing connected" in button.text()
    assert button.styleSheet() == "", "the button should not be tinted when idle"


def test_it_names_what_is_up(button):
    button.set_service("bus", True, "CANalyst-II at 500,000 bit/s")
    assert button.connected() == ["bus"]
    assert "BUS" in button.text()
    button.set_service("mcp", True, "http://127.0.0.1:8766/mcp")
    assert button.connected() == ["bus", "mcp"]
    assert "BUS" in button.text() and "MCP" in button.text()


def test_the_tooltip_carries_the_detail(button):
    button.set_service("bus", True, "can0 at 500,000 bit/s")
    tip = button.toolTip()
    assert "CAN bus: connected" in tip and "can0 at 500,000 bit/s" in tip
    assert "MCP server: not connected" in tip


def test_disconnecting_clears_it(button):
    button.set_service("mcp", True, "http://127.0.0.1:8766/mcp")
    button.set_service("mcp", False, "")
    assert button.connected() == []
    assert button.service("mcp").detail == ""


def test_the_panel_has_a_row_for_every_service(button, qcore):
    button.set_service("bus", True, "can0 at 500,000 bit/s")
    button.set_service("mcp", True, "http://127.0.0.1:8766/mcp",
                       copyable="http://127.0.0.1:8766/mcp")
    button._rebuild()
    qcore.processEvents()
    panel = button.menu().actions()[0].defaultWidget()
    text = " ".join(w.text() for w in panel.findChildren(type(panel.findChild(
        __import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel))))
    for expected in ("CAN bus", "MCP server", "REST API", "can0 at 500,000 bit/s",
                     "http://127.0.0.1:8766/mcp", "connected", "not connected"):
        assert expected in text


def test_a_url_can_be_copied(button, qcore):
    from PyQt6.QtGui import QGuiApplication

    url = "http://127.0.0.1:8766/mcp"
    button.set_service("mcp", True, url, copyable=url)
    button._copy(url)
    qcore.processEvents()
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:          # no clipboard under some platforms
        assert clipboard.text() == url


# ── what the window puts into it ─────────────────────────────────────────────

def test_the_window_reports_the_bus_and_the_servers(window, qcore):
    """The button is only worth having if it is kept in step with the window."""
    button = window.status_button
    window._update_status_button()
    qcore.processEvents()
    assert button.service("bus").up is bool(window._state.is_connected)
    assert button.service("mcp").up is (window._mcp_service is not None)
    assert button.service("rest").up is (window._rest_api_server is not None)


@pytest.fixture(scope="module")
def window(qcore):
    from canlab.mainwindow import MainWindow
    w = MainWindow()
    yield w
    w.close()
    w.deleteLater()
    qcore.processEvents()
