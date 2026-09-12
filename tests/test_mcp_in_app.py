"""The MCP server inside the window: an assistant works on the live state.

A client on its own thread talks to the server the window started, while the
test pumps the Qt event loop the way a running application would. A signal
the client adds must show up in AppState on the GUI thread; a capture it
loads must become the window's capture.
"""
import asyncio
import gc
import os
import threading
import time

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("mcp.server.fastmcp")

from canlab.core.log_parser import parse_log_file
from canlab.core.mcp_service import AppBackend, McpService, client_snippets
from canlab.core.mcp_tools import CanLabTools
from canlab.core.state import get_state

SAMPLE = os.path.join("canlab", "sample_data", "sample_kona_drive.csv")


@pytest.fixture(scope="module")
def window(qcore):
    from canlab.mainwindow import MainWindow
    w = MainWindow()
    yield w
    w.close()
    w.deleteLater()
    qcore.processEvents()
    gc.collect()
    qcore.processEvents()


def _run_client_while_pumping(app, url, token, coro_factory, timeout=60):
    """Run an MCP client on a worker thread; pump Qt until it is done."""
    box = {}

    def worker():
        try:
            box["result"] = asyncio.run(coro_factory())
        except BaseException as e:      # surfaced to the test below
            box["error"] = e

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    deadline = time.monotonic() + timeout
    while th.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not th.is_alive(), "client did not finish"
    if "error" in box:
        raise box["error"]
    return box["result"]


def test_window_serves_live_state(window, qcore, tmp_path):
    import httpx
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from canlab.settings_dialog import SettingsDialog, settings
    state = get_state()
    state.load_frames(parse_log_file(SAMPLE), "sample")
    state.replace_dbc_signals([])
    qcore.processEvents()

    st = settings()
    port = _free_port()
    st.setValue(SettingsDialog.S_MCP_PORT, port)
    st.setValue(SettingsDialog.S_MCP_TOKEN, "tok")
    st.setValue(SettingsDialog.S_MCP_REMOTE, False)
    window._start_mcp()
    assert window._mcp_service is not None and window._mcp_service.running
    assert window._act_mcp.text() == f"MCP :{port}"
    url = window._mcp_service.url

    other = tmp_path / "other.csv"
    other.write_text(open(SAMPLE).read())

    async def session():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer tok"}) as http:
            async with streamable_http_client(url, http_client=http) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    status = await s.call_tool("status", {})
                    top = await s.call_tool("list_ids", {"limit": 1})
                    cid = top.structuredContent["result"][0]["id"]
                    added = await s.call_tool("add_dbc_signal", {
                        "message_id": cid, "signal_name": "FROM_ASSISTANT",
                        "start_bit": 0, "length": 8})
                    dup = await s.call_tool("add_dbc_signal", {
                        "message_id": cid, "signal_name": "FROM_ASSISTANT",
                        "start_bit": 0, "length": 8})
                    ann = await s.call_tool("add_annotation",
                                            {"label": "x", "start_s": 0.0, "end_s": 1.0})
                    loaded = await s.call_tool("load_log", {"path": str(other)})
                    return status, cid, added, dup, ann, loaded

    import json
    status, cid, added, dup, ann, loaded = _run_client_while_pumping(
        qcore, url, "tok", session)
    st_json = json.loads(status.content[0].text)
    assert st_json["mode"] == "application" and st_json["frames"] > 0
    assert json.loads(added.content[0].text)["added"] is True
    assert json.loads(dup.content[0].text)["added"] is False
    assert json.loads(ann.content[0].text)["added"] is True
    assert json.loads(loaded.content[0].text)["frames"] > 0

    # The write landed on the GUI thread's state, as one undoable step.
    qcore.processEvents()
    names = [s["signal_name"] for s in state.dbc_signals]
    assert names == ["FROM_ASSISTANT"] and state.dbc_signals[0]["message_id"] == cid
    assert state.can_undo_dbc()
    assert state.annotations.items[-1].label == "x"
    assert state.sources[-1]["name"] == "other.csv"

    window._stop_mcp()
    assert window._mcp_service is None and window._act_mcp.text() == "MCP"


def test_port_in_use_is_reported(qcore):
    import socket
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    svc = McpService(CanLabTools(), port=port)
    with pytest.raises(OSError):
        svc.start()
    assert not svc.running
    blocker.close()


def test_remote_bind_needs_opt_in():
    svc = McpService(CanLabTools(), host="0.0.0.0", port=_free_port())
    with pytest.raises(ValueError):
        svc.start()


def test_app_backend_without_qt():
    """The backend only needs an invoker; a plain call is one."""
    state = get_state()
    state.load_frames(parse_log_file(SAMPLE), "sample")
    state.replace_dbc_signals([])
    calls = []

    def invoke(fn):
        calls.append(fn)
        return fn()

    b = AppBackend(state, invoke)
    tools = CanLabTools(b)
    assert tools.status()["mode"] == "application"
    cid = tools.list_ids(limit=1)[0]["id"]
    assert tools.add_dbc_signal(cid, "A", 0, 8)["added"]
    assert tools.remove_dbc_signal(cid, "A")["removed"]
    assert not tools.remove_dbc_signal(cid, "A")["removed"]
    assert len(calls) == 3 and state.dbc_signals == []


def test_client_snippets_name_every_client():
    sn = client_snippets("http://127.0.0.1:8766/mcp", "abc")
    assert sn["claude_code"].startswith("claude mcp add --transport http canlab http://127.0.0.1:8766/mcp")
    assert "Bearer abc" in sn["claude_code"]
    import json
    cfg = json.loads(sn["claude_desktop"])
    args = cfg["mcpServers"]["canlab"]["args"]
    assert "--attach" in args and "http://127.0.0.1:8766/mcp" in args and "abc" in args
    assert sn["codex"].startswith("[mcp_servers.canlab]") and '"--attach"' in sn["codex"]
    assert "https://<tunnel-host>/mcp" in sn["chatgpt"] and "8766" in sn["chatgpt"]
    no_tok = client_snippets("http://127.0.0.1:8766/mcp")
    assert "--header" not in no_tok["claude_code"] and "--token" not in no_tok["claude_desktop"]


def test_settings_mcp_tab(qcore):
    from canlab.settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    dlg.show_tab("MCP")
    assert dlg._tabs.tabText(dlg._tabs.currentIndex()) == "MCP"
    dlg.mcp_port_spin.setValue(9001)
    dlg.mcp_client_combo.setCurrentIndex(0)
    assert "9001/mcp" in dlg.mcp_snippet.toPlainText()
    dlg._mcp_generate_token()
    tok = dlg.mcp_token_edit.text()
    assert len(tok) >= 24 and tok in dlg.mcp_snippet.toPlainText()
    cfg = dlg.get_mcp_config()
    assert cfg == {"port": 9001, "autostart": False, "allow_remote": False, "token": tok}
    assert "OpenAI" in [dlg.provider_combo.itemText(i) for i in range(dlg.provider_combo.count())]
    dlg.provider_combo.setCurrentText("OpenAI")
    assert dlg.model_combo.currentText() == "gpt-5"
    dlg.model_combo.setCurrentText("gpt-5.5-custom")
    assert dlg.get_ai_model() == "gpt-5.5-custom"
    dlg.deleteLater()


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
