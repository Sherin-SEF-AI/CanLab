"""
Minimal REST API server (FastAPI) that runs in a background thread.
Exposes:
  GET  /frames         → last N frames as JSON
  GET  /signals        → current DBC signals
  GET  /status         → connection + frame count
  POST /inject         → inject a raw CAN frame
  GET  /memory         → AI memory entries
"""
import threading
import json


def _build_app(state_getter):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel
    except ImportError:
        return None

    app = FastAPI(title="CANLAB REST API", version="1.0")

    class InjectRequest(BaseModel):
        id:   str   # hex, e.g. "018"
        data: str   # 8 hex bytes space-separated, e.g. "01 02 03 04 05 06 07 08"

    @app.get("/frames")
    def get_frames(n: int = 200):
        state = state_getter()
        if state.frames_df.empty:
            return JSONResponse(content=[])
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
        state = state_getter()
        if state.can_bus is None:
            raise HTTPException(status_code=503, detail="CAN bus not connected")
        try:
            arb_id = int(req.id, 16)
            data   = bytes(int(b, 16) for b in req.data.split())
            msg    = can.Message(arbitration_id=arb_id, data=data, is_extended_id=False)
            state.can_bus.send(msg)
            return {"ok": True}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    return app


class RestAPIServer:
    def __init__(self, state_getter, host: str = "127.0.0.1", port: int = 8765):
        self._state_getter = state_getter
        self._host   = host
        self._port   = port
        self._server = None
        self._thread = None

    def start(self):
        app = _build_app(self._state_getter)
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
