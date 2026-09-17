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


# ── marks from a phone, and the injection endpoint that must not ride along ──

def _marking_state():
    from canlab.core.annotations import AnnotationSet
    st = _state()
    st.annotations = AnnotationSet()
    return st


def _on_mark_for(st):
    def on_mark(label, action, at):
        marks = st.annotations
        if action == "toggle":
            action = "end" if any(a.label == label and not a.closed
                                  for a in marks.items) else "begin"
        if action == "begin":
            marks.begin(label, at)
        elif action == "end":
            marks.end(label, at)
        else:
            marks.add(label, at - 0.5, at + 0.5)
        return action
    return on_mark


def test_a_mark_toggles_an_interval_open_then_closed():
    """The capture kit's phone endpoint: one tap begins, the next ends."""
    st = _marking_state()
    c = TestClient(_build_app(lambda: st, token="t", expose_inject=False,
                              on_mark=_on_mark_for(st)))
    h = {"X-API-Token": "t"}
    first = c.post("/mark", json={"label": "brake", "at": 10.0}, headers=h)
    assert first.status_code == 200 and first.json()["action"] == "begin"
    second = c.post("/mark", json={"label": "brake", "at": 12.5}, headers=h)
    assert second.json()["action"] == "end"
    (mark,) = st.annotations.items
    assert mark.label == "brake" and mark.start == 10.0 and mark.end == 12.5


def test_a_point_mark_becomes_a_short_closed_interval():
    """Scoring only counts closed intervals with frames on both sides, so a
    momentary event has to be given a width."""
    st = _marking_state()
    c = TestClient(_build_app(lambda: st, token="t", expose_inject=False,
                              on_mark=_on_mark_for(st)))
    c.post("/mark", json={"label": "horn", "action": "point", "at": 30.0},
           headers={"X-API-Token": "t"})
    (mark,) = st.annotations.items
    assert mark.closed and mark.end - mark.start == pytest.approx(1.0)


def test_marks_still_need_the_token():
    st = _marking_state()
    c = TestClient(_build_app(lambda: st, token="t", expose_inject=False,
                              on_mark=_on_mark_for(st)))
    assert c.post("/mark", json={"label": "brake"}).status_code == 401
    assert st.annotations.items == []


def test_inject_is_absent_when_the_app_is_built_without_it():
    """A phone-reachable marking server must not also be an injection server."""
    st = _marking_state()
    c = TestClient(_build_app(lambda: st, token="t", expose_inject=False,
                              on_mark=_on_mark_for(st)))
    r = c.post("/inject", json={"id": "018", "data": "00"}, headers={"X-API-Token": "t"})
    assert r.status_code == 404


def test_the_desktop_app_keeps_inject_and_has_no_mark_without_a_handler():
    c = _client()
    h = {"X-API-Token": "secret-token"}
    assert c.post("/inject", json={"id": "018", "data": "00"}, headers=h).status_code != 404
    assert c.post("/mark", json={"label": "x"}, headers=h).status_code == 404


def test_a_non_loopback_bind_is_refused_in_both_modes():
    from canlab.core.rest_api import RestAPIServer
    with pytest.raises(ValueError, match="/inject"):
        RestAPIServer(lambda: _state(), host="0.0.0.0")
    with pytest.raises(ValueError, match="frames and marks"):
        RestAPIServer(lambda: _state(), host="0.0.0.0", expose_inject=False)
