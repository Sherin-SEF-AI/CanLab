"""The MCP server that runs inside the CanLab window.

It serves the same tools as ``canlab-mcp`` but over the application's own
state: whatever capture is loaded, the bus being recorded right now, the
signals in the DBC Builder. An assistant connected here sees what the user
sees, and a signal it defines shows up in the builder immediately.

Reads come straight from AppState (the frame store takes its own lock).
Writes go through ``invoke``, a callable the window supplies that runs a
function on the GUI thread and waits, because AppState fans changes out to
widgets through Qt signals.

Nothing here is Qt: the invoker is injected, so the service can be tested
with a plain function.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
import time
from pathlib import Path

import pandas as pd

from canlab.mcp_server import DEFAULT_HOST, DEFAULT_PORT, MCP_PATH, build_server, http_app

log = logging.getLogger(__name__)


class AppBackend:
    """The running application as the MCP tools' data source."""

    def __init__(self, state, invoke=None):
        self.state = state
        self.invoke = invoke or (lambda fn: fn())

    def frames(self) -> pd.DataFrame:
        df = self.state.frames_snapshot()
        if df is None or df.empty:
            raise ValueError("Nothing is loaded in CanLab. Open a log or connect a "
                             "bus in the window, or call load_log(path).")
        return df

    def describe(self) -> dict:
        st = self.state
        return {"mode": "application", "connected": bool(st.is_connected),
                "sources": [s.get("name") for s in st.sources],
                "selected_id": st.selected_id or None,
                "live_rate_hz": round(float(st.frame_rate), 1) if st.is_connected else None}

    def load(self, path: str) -> pd.DataFrame:
        from canlab.core.log_parser import parse_log_file
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"no such file: {path}")
        df = parse_log_file(str(p))
        if df.empty:
            raise ValueError(f"no frames found in {path}")
        self.invoke(lambda: self.state.load_frames(df, p.name))
        return df

    def signals(self) -> list[dict]:
        return list(self.state.dbc_signals)

    def add_signals(self, sigs: list[dict]) -> int:
        return self.invoke(lambda: self.state.add_dbc_signals(sigs))

    def remove_signal(self, message_id: str, signal_name: str) -> bool:
        def _do():
            for i, s in enumerate(self.state.dbc_signals):
                if s.get("message_id") == message_id and s.get("signal_name") == signal_name:
                    self.state.remove_dbc_signal(i)
                    return True
            return False
        return self.invoke(_do)

    def annotations(self):
        return self.state.annotations

    def add_annotation(self, label: str, start: float, end: float) -> None:
        self.invoke(lambda: self.state.annotations.add(label, start, end))


class McpService:
    """Streamable HTTP on a background thread; start() returns once it answers."""

    def __init__(self, tools, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 token: str = "", allow_remote: bool = False):
        self.tools = tools
        self.host = host
        self.port = int(port)
        self.token = token or ""
        self.allow_remote = bool(allow_remote)
        self._server = None
        self._thread = None
        self._socket = None

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{host}:{self.port}{MCP_PATH}"

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        import uvicorn
        if self.running:
            return
        if not self.allow_remote and not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError(f"refusing to bind {self.host}: tick 'allow other machines' "
                             "in Settings > MCP to expose the server")
        # Bind here, not in the thread, so a port in use fails right now with
        # a message instead of a server that silently never answers.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
            sock.listen(16)
        except OSError:
            sock.close()
            raise
        mcp = build_server(self.tools, host=self.host, port=self.port,
                           allow_remote=self.allow_remote)
        app = http_app(mcp, self.token)
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        self._socket = sock
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [sock]},
                                        name="canlab-mcp", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 5.0
        while not self._server.started and time.monotonic() < deadline:
            if not self._thread.is_alive():
                break
            time.sleep(0.02)
        if not self._server.started:
            self.stop()
            raise RuntimeError("MCP server did not start; see the log")
        log.info("MCP server listening on %s", self.url)

    def stop(self, timeout: float = 3.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._server = self._thread = self._socket = None


# ── what to paste into each client ───────────────────────────────────────────

def _bridge_command() -> tuple[str, list[str]]:
    """The canlab-mcp executable next to this interpreter, or python -m."""
    import shutil
    import sys
    exe = shutil.which("canlab-mcp") or str(Path(sys.executable).with_name("canlab-mcp"))
    if Path(exe).exists():
        return exe, []
    return sys.executable, ["-m", "canlab.mcp_server"]


def client_snippets(url: str, token: str = "") -> dict[str, str]:
    """Ready-to-paste configuration for each assistant, for the server at url.

    Claude Code and Codex speak HTTP or stdio; Claude Desktop only launches
    stdio servers, so it gets the bridge. ChatGPT connects from the cloud, so
    it needs a public HTTPS URL, which a tunnel provides.
    """
    cmd, base_args = _bridge_command()
    bridge_args = base_args + ["--attach", url] + (["--token", token] if token else [])
    header = f' --header "Authorization: Bearer {token}"' if token else ""
    desktop = json.dumps({"mcpServers": {"canlab": {"command": cmd, "args": bridge_args}}},
                         indent=2)
    codex_args = ", ".join(json.dumps(a) for a in bridge_args)
    codex = (f'[mcp_servers.canlab]\ncommand = {json.dumps(cmd)}\n'
             f'args = [{codex_args}]\n')
    chatgpt = (
        "ChatGPT connects from OpenAI's servers, so it cannot reach this machine's\n"
        "loopback address. Publish the server over HTTPS first, for example:\n"
        f"    cloudflared tunnel --url http://127.0.0.1:{url.split(':')[-1].split('/')[0]}\n"
        "or  ngrok http <port>\n"
        "Tick 'Allow connections from other machines' here and restart the MCP\n"
        "server; then in ChatGPT: Settings > Connectors > Create, paste\n"
        "    https://<tunnel-host>/mcp\n"
        "with Authentication set to 'No authentication' (ChatGPT cannot send a\n"
        "bearer token), and enable Developer mode to use every tool. Without\n"
        "a token, anyone who learns the tunnel URL can read the capture and\n"
        "edit the signal list while the tunnel is up. Nothing can transmit."
    )
    return {
        "claude_code": f"claude mcp add --transport http canlab {url}{header}",
        "claude_desktop": desktop,
        "codex": codex,
        "chatgpt": chatgpt,
    }
