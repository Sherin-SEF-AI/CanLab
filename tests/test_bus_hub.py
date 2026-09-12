"""The receive dispatcher: one rx thread, per-consumer queues, gated transmit."""
import time

import pytest

from canlab.core import safety
from canlab.core.bus_hub import BusHub
from canlab.core.safety import BusNotArmedError
from tests.doubles import FakeMsg, RecordingBus


class ScriptedRxBus(RecordingBus):
    """Serves a fixed list of frames to recv(), then None forever."""

    def __init__(self, frames=()):
        super().__init__()
        self._frames = list(frames)

    def recv(self, timeout=0.1):
        if self._frames:
            return self._frames.pop(0)
        time.sleep(0.005)
        return None


def _hub(frames=(), **kw):
    bus = ScriptedRxBus(frames)
    hub = BusHub(bus, name="test", **kw)
    return hub, bus


def _wait_for(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def _disarm():
    safety.set_armed(False)
    yield
    safety.set_armed(False)


def test_subscriptions_receive_only_matching_ids():
    frames = [FakeMsg(0x1A0, b"\x01"), FakeMsg(0x7E8, b"\x02"), FakeMsg(0x1A0, b"\x03")]
    hub, _bus = _hub(frames)
    only_1a0 = hub.subscribe(0x1A0)
    responses = hub.subscribe(range(0x7E8, 0x7F0))
    everything = hub.subscribe()
    hub.start()
    try:
        assert _wait_for(lambda: hub.rx_count == 3)
        got_a = [only_1a0.recv(0.2) for _ in range(2)]
        assert [m.arbitration_id for m in got_a] == [0x1A0, 0x1A0]
        assert only_1a0.recv(0.05) is None          # the 0x7E8 frame was filtered out
        assert responses.recv(0.2).arbitration_id == 0x7E8
        assert responses.recv(0.05) is None
        assert [everything.recv(0.2).arbitration_id for _ in range(3)] == [0x1A0, 0x7E8, 0x1A0]
    finally:
        hub.shutdown()


def test_drain_returns_canonical_rows_in_order():
    frames = [FakeMsg(0x1A0, bytes(range(8))), FakeMsg(0x18FEF100, b"\xAA" * 3, is_extended_id=True)]
    hub, _bus = _hub(frames)
    hub.start()
    try:
        assert _wait_for(lambda: hub.rx_count == 2)
        rows = hub.drain()
        # (timestamp, arb_id, extended, bus, dlc, data)
        assert [r[1] for r in rows] == [0x1A0, 0x18FEF100]
        assert rows[0][4] == 8 and rows[0][5] == bytes(range(8))
        assert rows[1][4] == 3 and rows[1][2] is True
        assert hub.drain() == []                     # drained once only

        # the store turns them into the canonical schema
        from canlab.core.frame_store import FrameStore
        store = FrameStore()
        store.append_batch(rows)
        df = store.materialize()
        assert df["ID"].tolist() == ["1A0", "18FEF100"]
        assert df["B7"].iloc[0] == 7 and df["DLC"].iloc[1] == 3
        assert df["B3"].iloc[1] != df["B3"].iloc[1]   # NaN past the payload
    finally:
        hub.shutdown()


def test_error_frames_feed_health_and_are_not_stored():
    err = FakeMsg(0x40, b"")                         # CAN_ERR_BUSOFF class bit
    err.is_error_frame = True
    plain = FakeMsg(0x1A0, b"\x01")
    hub, _bus = _hub([err, plain])
    hub.start()
    try:
        assert _wait_for(lambda: hub.rx_count == 1)
        assert [r[1] for r in hub.drain()] == [0x1A0]
        snap = hub.health_snapshot()
        assert snap["error_frames"] == 1 and snap["bus_off"] == 1
    finally:
        hub.shutdown()


def test_hub_send_is_gated_and_subscription_send_routes_through_it():
    hub, bus = _hub()
    sub = hub.subscribe()
    with pytest.raises(BusNotArmedError):
        sub.send(FakeMsg(0x200, b"\x01"))
    assert bus.sent == []
    safety.set_armed(True)
    sub.send(FakeMsg(0x200, b"\x01"))
    assert len(bus.sent) == 1
    hub.shutdown()


def test_triggers_are_evaluated_once_per_frame():
    fired = []
    rules = [{"id": "1A0", "byte": 0, "op": "==", "value": 1, "label": "hit", "enabled": True}]
    hub, _bus = _hub([FakeMsg(0x1A0, b"\x01\x00"), FakeMsg(0x1A0, b"\x02\x00")],
                     on_trigger=lambda rule, msg: fired.append(rule["label"]),
                     triggers_getter=lambda: rules)
    hub.start()
    try:
        assert _wait_for(lambda: hub.rx_count == 2)
        assert fired == ["hit"]
    finally:
        hub.shutdown()


def test_full_queue_drops_oldest_and_counts():
    hub, _bus = _hub()
    sub = hub.subscribe(maxsize=2)
    for i in range(5):
        sub.offer(FakeMsg(0x100 + i, b"\x00"))
    assert sub.dropped == 3
    assert [sub.recv(0.05).arbitration_id for _ in range(2)] == [0x103, 0x104]
    hub.shutdown()


def test_shutdown_joins_thread_and_closes_subscriptions():
    hub, bus = _hub([FakeMsg(0x1A0, b"\x01")])
    sub = hub.subscribe()
    hub.start()
    assert _wait_for(lambda: hub.rx_count == 1)
    hub.shutdown()
    assert hub._thread is None
    assert sub.closed and sub.recv(0.01) is None
    assert bus.shutdown_calls == 1


def test_isotp_over_a_subscription_while_other_traffic_flows():
    """The race this replaced: a diagnostic transfer must not lose its frames
    to the capture loop."""
    from canlab.core.isotp import ISOTPSession
    safety.set_armed(True)
    noise = [FakeMsg(0x1A0, b"\x00") for _ in range(20)]
    reply = FakeMsg(0x7E8, bytes([0x02, 0x50, 0x03, 0, 0, 0, 0, 0]))
    hub, bus = _hub(noise + [reply])
    capture = hub.subscribe()               # the "live table" consumer
    diag = hub.subscribe(0x7E8)             # the diagnostic consumer
    hub.start()
    try:
        assert _wait_for(lambda: hub.rx_count == 21)
        payload = ISOTPSession(_TxOnly(hub, diag), tx_id=0x7E0, rx_id=0x7E8).send(
            bytes([0x10, 0x03]), timeout=0.5)
        assert payload == bytes([0x50, 0x03])       # response not stolen by capture
        assert capture.recv(0.05) is not None       # capture still got its frames
    finally:
        hub.shutdown()


class _TxOnly:
    """Bus view whose recv comes from one subscription and send from the hub."""

    def __init__(self, hub, sub):
        self._hub, self._sub = hub, sub

    def send(self, msg):
        self._hub.send(msg)

    def recv(self, timeout=0.1):
        return self._sub.recv(timeout)
