"""
Minimal REST API server (FastAPI) that runs in a background thread.

All endpoints require an ``X-API-Token`` header matching the per-session token
(printed to the user when the server starts). This matters most for
``POST /inject``, which puts frames on the bus and would otherwise let any local
process inject with no authentication, bypassing the safety disclaimer entirely.

The server binds to 127.0.0.1 by default. Binding to a non-loopback address is
refused unless ``allow_remote=True`` is passed explicitly.

Exposes:
  GET  /frames         → last N frames as JSON
  GET  /signals        → current DBC signals
  GET  /status         → connection + frame count
  POST /inject         → inject a raw CAN frame (token required)
  GET  /memory         → AI memory entries
"""
import threading
import secrets
import ipaddress


_DASHBOARD_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>CANLAB Live</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{margin:0;font:13px ui-monospace,Menlo,Consolas,monospace;background:#0d0d0d;color:#d0d0d0}
 header{padding:10px 14px;background:#111;border-bottom:1px solid #1f3a24;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 h1{font-size:15px;margin:0;color:#3fd07a;letter-spacing:1px}
 input{background:#0d0d0d;color:#d0d0d0;border:1px solid #333;border-radius:3px;padding:4px 6px;font:inherit}
 .stat{color:#888}.stat b{color:#3fd07a}
 table{border-collapse:collapse;width:100%}
 th,td{padding:3px 8px;text-align:left;border-bottom:1px solid #181818;white-space:nowrap}
 th{position:sticky;top:0;background:#111;color:#3fd07a}
 td.b{color:#9fd0ff}#wrap{overflow:auto;max-height:calc(100vh - 52px)}
 .err{color:#ff6b6b}
</style></head><body>
<header>
 <h1>CANLAB LIVE</h1>
 <label>X-API-Token <input id="tok" size="34" placeholder="paste token shown on start"></label>
 <span class="stat">frames <b id="fc">-</b></span>
 <span class="stat">bus <b id="cx">-</b></span>
 <span class="stat" id="msg"></span>
</header>
<div id="wrap"><table><thead><tr id="hdr"></tr></thead><tbody id="rows"></tbody></table></div>
<script>
const $=id=>document.getElementById(id);
const cols=["Timestamp","ID","Bus","DLC","B0","B1","B2","B3","B4","B5","B6","B7"];
$("hdr").innerHTML=cols.map(c=>`<th>${c}</th>`).join("");
$("tok").value=localStorage.getItem("canlab_tok")||"";
async function tick(){
 const t=$("tok").value.trim(); localStorage.setItem("canlab_tok",t);
 if(!t){$("msg").textContent="enter token";$("msg").className="err";return;}
 try{
  const h={"X-API-Token":t};
  const st=await fetch("/status",{headers:h}); if(st.status===401){$("msg").textContent="invalid token";return;}
  const s=await st.json(); $("fc").textContent=s.frame_count; $("cx").textContent=s.connected?"connected":"off"; $("msg").textContent="";
  const fr=await(await fetch("/frames?n=60",{headers:h})).json();
  const rows=$("rows"); rows.replaceChildren();
  for(const r of fr.slice().reverse()){
    const tr=document.createElement("tr");
    for(const c of cols){
      let v=r[c]; if(v==null)v="";
      else if(c==="Timestamp")v=(+v).toFixed(3);
      else if(c[0]==="B"&&v!=="")v=(+v).toString(16).padStart(2,"0").toUpperCase();
      const td=document.createElement("td");
      if(c[0]==="B"&&c!=="Bus")td.className="b";
      td.textContent=String(v);        // textContent: never parse frame data as HTML
      tr.appendChild(td);
    }
    rows.appendChild(tr);
  }
 }catch(e){$("msg").textContent=String(e);$("msg").className="err";}
}
setInterval(tick,1000); tick();
</script></body></html>"""


def _json_safe(records: list) -> list:
    """Make DataFrame records serialisable.

    Padding bytes are NaN, which is not JSON, and pandas hands back NumPy
    scalars the encoder also refuses. Either one used to return a 500 for any
    frame shorter than eight bytes.
    """
    import math

    import numpy as np

    out = []
    for row in records:
        clean = {}
        for key, value in row.items():
            if isinstance(value, (np.integer, np.floating, np.bool_)):
                value = value.item()
            if isinstance(value, float) and math.isnan(value):
                value = None
            clean[key] = value
        out.append(clean)
    return out


def _build_app(state_getter, token: str, *, expose_inject: bool = True, on_mark=None):
    """Build the FastAPI app.

    `expose_inject` gates the one endpoint that can put a frame on a bus. The
    headless capture kit runs this server on a phone-reachable address purely
    so a passenger can mark events, and an injection endpoint has no business
    being reachable from a phone, so the kit builds the app without it.

    `on_mark(label, action, at)` is called for POST /mark when given; it
    returns the action actually performed ("begin" or "end" for a toggle).
    """
    try:
        from fastapi import FastAPI, HTTPException, Header, Depends
        from fastapi.responses import JSONResponse, HTMLResponse
        from pydantic import BaseModel
    except ImportError:
        return None

    def require_token(x_api_token: str = Header(None)):
        if not token or not x_api_token or not secrets.compare_digest(x_api_token, token):
            raise HTTPException(status_code=401,
                                detail="Missing or invalid X-API-Token header")

    # Data endpoints require the token (per-route); the "/" dashboard shell is
    # open (it's just static HTML that asks the user to paste the token).
    app = FastAPI(title="CANLAB REST API", version="1.2")
    auth = [Depends(require_token)]

    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        return _DASHBOARD_HTML

    class InjectRequest(BaseModel):
        id:   str            # hex, e.g. "018"
        data: str            # hex bytes space-separated, e.g. "01 02 03 04 05 06 07 08"
        extended: bool = False

    class MarkRequest(BaseModel):
        label: str
        action: str = "toggle"       # begin | end | point | toggle
        at: float | None = None      # capture clock; server time when absent

    if on_mark is not None:
        @app.post("/mark", dependencies=auth)
        def mark(req: MarkRequest):
            """Record that something happened, for ranking bytes against it later."""
            import time as _time
            label = req.label.strip()
            if not label:
                raise HTTPException(status_code=400, detail="label is required")
            if req.action not in ("begin", "end", "point", "toggle"):
                raise HTTPException(status_code=400,
                                    detail="action must be begin, end, point or toggle")
            at = float(req.at) if req.at is not None else _time.time()
            try:
                performed = on_mark(label, req.action, at)
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))
            return {"ok": True, "label": label, "action": performed or req.action, "at": at}

    @app.get("/frames", dependencies=auth)
    def get_frames(n: int = 200):
        import pandas as pd
        state = state_getter()
        frames = state.frames_df
        if frames.empty:
            return JSONResponse(content=[])
        tail = frames.tail(max(0, min(int(n), len(frames))))
        clean = tail.astype(object).where(pd.notna(tail), None)
        return JSONResponse(content=_json_safe(clean.to_dict(orient="records")))

    @app.get("/signals", dependencies=auth)
    def get_signals():
        state = state_getter()
        return JSONResponse(content=state.dbc_signals)

    @app.get("/status", dependencies=auth)
    def get_status():
        state = state_getter()
        return JSONResponse(content={
            "connected":    state.is_connected,
            "frame_count":  len(state.frames_df),
        })

    @app.get("/memory", dependencies=auth)
    def get_memory():
        state = state_getter()
        return JSONResponse(content=state.ai_memory)

    if not expose_inject:
        return app

    @app.post("/inject", dependencies=auth)
    def inject_frame(req: InjectRequest):
        import can
        from canlab.core.safety import gated_send, BusNotArmedError, BlockedIdError
        state = state_getter()
        if state.can_bus is None:
            raise HTTPException(status_code=503, detail="CAN bus not connected")
        try:
            arb_id = int(req.id, 16)
            data   = bytes(int(b, 16) for b in req.data.split())
            msg    = can.Message(arbitration_id=arb_id, data=data,
                                 is_extended_id=bool(req.extended))
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            gated_send(state.can_bus, msg)
        except (BusNotArmedError, BlockedIdError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True}

    return app


class RestAPIServer:
    def __init__(self, state_getter, host: str = "127.0.0.1", port: int = 8765,
                 token: str = None, allow_remote: bool = False, *,
                 expose_inject: bool = True, on_mark=None):
        # Refuse to expose the API on a non-loopback interface unless the
        # caller explicitly opts in. Frames and marks still travel with the
        # token, and with /inject exposed the stakes are higher still.
        self._expose_inject = expose_inject
        self._on_mark = on_mark
        if not allow_remote:
            try:
                if not ipaddress.ip_address(host).is_loopback:
                    what = "with /inject" if expose_inject else "frames and marks"
                    raise ValueError(
                        f"Refusing to bind REST API ({what}) to non-loopback "
                        f"address {host!r}; pass allow_remote=True to override."
                    )
            except ValueError:
                if host not in ("localhost",):
                    raise
        self._state_getter = state_getter
        self._host   = host
        self._port   = port
        self.token   = token or secrets.token_urlsafe(24)
        self._server = None
        self._thread = None
        self._socket = None

    def start(self):
        """Start the server, raising if the port cannot be bound.

        The socket is bound here rather than inside the server thread: a failed
        bind used to kill that thread silently while the app reported the API
        as running and handed the user a token for a server that did not exist.
        """
        app = _build_app(self._state_getter, self.token,
                         expose_inject=self._expose_inject, on_mark=self._on_mark)
        if app is None:
            raise ImportError("fastapi or uvicorn not installed")

        import socket

        import uvicorn

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self._host, self._port))
            sock.listen(128)
        except OSError as e:
            sock.close()
            raise OSError(
                f"Cannot bind {self._host}:{self._port} — {e}") from e
        sock.setblocking(False)

        config = uvicorn.Config(app, log_level="error")
        self._server = uvicorn.Server(config)
        self._socket = sock
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [sock]},
            daemon=True, name="canlab-rest-api")
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        if self._server:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._server = None
        self._thread = None
        self._socket = None
