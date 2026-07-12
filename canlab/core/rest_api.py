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
  $("rows").innerHTML=fr.slice().reverse().map(r=>"<tr>"+cols.map(c=>{
    let v=r[c]; if(v==null)v="";
    else if(c==="Timestamp")v=(+v).toFixed(3);
    else if(c[0]==="B"&&v!=="")v=(+v).toString(16).padStart(2,"0").toUpperCase();
    return `<td class="${c[0]==='B'?'b':''}">${v}</td>`;}).join("")+"</tr>").join("");
 }catch(e){$("msg").textContent=String(e);$("msg").className="err";}
}
setInterval(tick,1000); tick();
</script></body></html>"""


def _build_app(state_getter, token: str):
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

    @app.get("/frames", dependencies=auth)
    def get_frames(n: int = 200):
        state = state_getter()
        if state.frames_df.empty:
            return JSONResponse(content=[])
        n = max(0, min(int(n), len(state.frames_df)))
        tail = state.frames_df.tail(n)
        return JSONResponse(content=tail.to_dict(orient="records"))

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
            "repo_url":     state.repo_url,
            "fingerprint":  state.fingerprint,
        })

    @app.get("/memory", dependencies=auth)
    def get_memory():
        state = state_getter()
        return JSONResponse(content=state.ai_memory)

    @app.post("/inject", dependencies=auth)
    def inject_frame(req: InjectRequest):
        import can
        from core.safety import is_armed
        state = state_getter()
        if state.can_bus is None:
            raise HTTPException(status_code=503, detail="CAN bus not connected")
        if not is_armed():
            raise HTTPException(status_code=409,
                                detail="Bus transmit is disarmed; enable ARM TX in the app")
        try:
            arb_id = int(req.id, 16)
            data   = bytes(int(b, 16) for b in req.data.split())
            msg    = can.Message(arbitration_id=arb_id, data=data,
                                 is_extended_id=bool(req.extended))
            state.can_bus.send(msg)
            return {"ok": True}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    return app


class RestAPIServer:
    def __init__(self, state_getter, host: str = "127.0.0.1", port: int = 8765,
                 token: str = None, allow_remote: bool = False):
        # Refuse to expose an unauthenticated-by-address, frame-injecting API on
        # a non-loopback interface unless the caller explicitly opts in.
        if not allow_remote:
            try:
                if not ipaddress.ip_address(host).is_loopback:
                    raise ValueError(
                        f"Refusing to bind REST API (with /inject) to non-loopback "
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

    def start(self):
        app = _build_app(self._state_getter, self.token)
        if app is None:
            raise ImportError("fastapi or uvicorn not installed")

        import uvicorn
        config      = uvicorn.Config(app, host=self._host, port=self._port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, daemon=True, name="canlab-rest-api"
        )
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.should_exit = True
        self._server = None
        self._thread = None
