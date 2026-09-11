"""Tests for the REST API auth model and the live dashboard shell (#4, C5)."""
import types
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
import pandas as pd

from canlab.core.rest_api import _build_app
import canlab.core.safety as safety


def _state():
    st = types.SimpleNamespace()
    st.frames_df = pd.DataFrame([{"Timestamp": 0.0, "ID": "0A6", "Bus": 0, "DLC": 8,
                                  **{f"B{i}": i for i in range(8)}}])
    st.dbc_signals = []; st.ai_memory = []
    st.is_connected = False
    st.can_bus = None
    return st


def _client():
    return TestClient(_build_app(lambda: _state(), token="secret-token"))


def test_dashboard_is_open_and_html():
    r = _client().get("/")
    assert r.status_code == 200
    assert "CANLAB LIVE" in r.text and "X-API-Token" in r.text


def test_data_endpoints_require_token():
    c = _client()
    assert c.get("/frames").status_code == 401
    assert c.get("/status").status_code == 401
    assert c.get("/frames", headers={"X-API-Token": "wrong"}).status_code == 401


def test_valid_token_returns_data():
    c = _client()
    r = c.get("/frames", headers={"X-API-Token": "secret-token"})
    assert r.status_code == 200
    assert r.json()[0]["ID"] == "0A6"


def _state_with_bus(bus):
    st = _state()
    st.can_bus = bus
    return st


def test_inject_503_when_no_bus():
    safety.set_armed(True)
    r = _client().post("/inject", headers={"X-API-Token": "secret-token"},
                       json={"id": "200", "data": "01 02"})
    assert r.status_code == 503        # no bus connected, distinct from the arm gate
    safety.set_armed(False)


def test_inject_409_when_disarmed_with_bus():
    from tests.doubles import RecordingBus
    bus = RecordingBus()
    safety.set_armed(False)
    c = TestClient(_build_app(lambda: _state_with_bus(bus), token="secret-token"))
    r = c.post("/inject", headers={"X-API-Token": "secret-token"},
               json={"id": "200", "data": "01 02"})
    assert r.status_code == 409 and not bus.sent


def test_inject_sends_when_armed():
    from tests.doubles import RecordingBus
    bus = RecordingBus()
    safety.set_armed(True)
    try:
        c = TestClient(_build_app(lambda: _state_with_bus(bus), token="secret-token"))
        r = c.post("/inject", headers={"X-API-Token": "secret-token"},
                   json={"id": "200", "data": "01 02"})
        assert r.status_code == 200 and len(bus.sent) == 1
        assert bus.sent[0].arbitration_id == 0x200
    finally:
        safety.set_armed(False)
