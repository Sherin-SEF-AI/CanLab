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


def _build_app(state_getter, token: str):
    try:
        from fastapi import FastAPI, HTTPException, Header, Depends
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel
    except ImportError:
        return None

    def require_token(x_api_token: str = Header(None)):
        if not token or not x_api_token or not secrets.compare_digest(x_api_token, token):
            raise HTTPException(status_code=401,
                                detail="Missing or invalid X-API-Token header")

    app = FastAPI(title="CANLAB REST API", version="1.1",
                  dependencies=[Depends(require_token)])

    class InjectRequest(BaseModel):
        id:   str            # hex, e.g. "018"
        data: str            # hex bytes space-separated, e.g. "01 02 03 04 05 06 07 08"
        extended: bool = False

    @app.get("/frames")
    def get_frames(n: int = 200):
        state = state_getter()
        if state.frames_df.empty:
            return JSONResponse(content=[])
        n = max(0, min(int(n), len(state.frames_df)))
        tail = state.frames_df.tail(n)
        return JSONResponse(content=tail.to_dict(orient="records"))

    @app.get("/signals")
    def get_signals():
        state = state_getter()
        return JSONResponse(content=state.dbc_signals)

    @app.get("/status")
    def get_status():
        state = state_getter()
        return JSONResponse(content={
            "connected":    state.is_connected,
            "frame_count":  len(state.frames_df),
            "repo_url":     state.repo_url,
            "fingerprint":  state.fingerprint,
        })

    @app.get("/memory")
    def get_memory():
        state = state_getter()
        return JSONResponse(content=state.ai_memory)

    @app.post("/inject")
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
