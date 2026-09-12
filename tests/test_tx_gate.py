"""The ARM-TX gate: nothing transmits while disarmed, disarm stops workers."""
import pandas as pd
import pytest

from canlab.core import safety
from canlab.core.safety import (BlockedIdError, BusNotArmedError, gated_send,
                                 require_tx_allowed)
from tests.doubles import FakeMsg, RecordingBus


@pytest.fixture(autouse=True)
def _disarm():
    safety.set_armed(False)
    safety.set_blocked_ids([])
    yield
    safety.set_armed(False)
    safety.set_blocked_ids([])


def test_gated_send_blocks_when_disarmed():
    bus = RecordingBus()
    with pytest.raises(BusNotArmedError):
        gated_send(bus, FakeMsg(0x123, b"\x01"))
    assert bus.sent == []


def test_gated_send_allows_when_armed():
    bus = RecordingBus()
    safety.set_armed(True)
    gated_send(bus, FakeMsg(0x123, b"\x01"))
    assert len(bus.sent) == 1


def test_blocked_id_refused_even_when_armed():
    safety.set_armed(True)
    safety.set_blocked_ids([0x123])
    bus = RecordingBus()
    with pytest.raises(BlockedIdError):
        gated_send(bus, FakeMsg(0x123, b"\x01"))
    assert bus.sent == []
    gated_send(bus, FakeMsg(0x200, b"\x01"))   # a different id still goes
    assert len(bus.sent) == 1
    require_tx_allowed(0x200)                    # no exception


def test_observer_fires_on_change():
    seen = []
    cb = lambda v: seen.append(v)
    safety.add_observer(cb)
    try:
        safety.set_armed(True)
        safety.set_armed(True)     # no change -> no second call
        safety.set_armed(False)
    finally:
        safety.remove_observer(cb)
    assert seen == [True, False]


def test_disarm_stops_registered_workers():
    class FakeWorker:
        def __init__(self): self.stopped = 0
        def stop(self): self.stopped += 1
    w = FakeWorker()
    safety.set_armed(True)
    safety.register_tx_worker(w)
    safety.set_armed(False)
    assert w.stopped == 1
    # a stopped worker is unregistered — disarming again does not re-stop it
    safety.set_armed(True)
    safety.set_armed(False)
    assert w.stopped == 1


def test_isotp_send_is_gated():
    from canlab.core.isotp import ISOTPSession
    bus = RecordingBus()
    sess = ISOTPSession(bus, tx_id=0x7E0, rx_id=0x7E8)
    assert sess.send(bytes([0x3E, 0x00]), timeout=0.05) is None
    assert bus.sent == []                        # disarmed: not a single frame left
    safety.set_armed(True)
    bus.feed(FakeMsg(0x7E8, bytes([0x02, 0x7E, 0x00])))
    sess.send(bytes([0x3E, 0x00]), timeout=0.2)
    assert len(bus.sent) == 1


def test_injection_worker_disarmed_sends_nothing():
    from canlab.core.injection import InjectionWorker
    sig = {"message_id": "1A0", "signal_name": "S", "start_bit": 0, "length": 8,
           "scale": 1, "offset": 0, "byte_order": "little", "value_type": "unsigned"}
    bus = RecordingBus()
    w = InjectionWorker(bus, sig, 5, period_ms=1)
    w.run()                                       # synchronous; gate raises, loop exits
    assert bus.sent == []
    assert w not in safety._workers


def test_fuzzer_bounded_when_armed_and_blocked_when_disarmed():
    from canlab.core.fuzzer import FuzzWorker
    safety.set_armed(True)
    bus = RecordingBus()
    FuzzWorker(bus, 0x200, rate_hz=100.0, max_iter=3).run()
    assert len(bus.sent) == 3
    safety.set_armed(False)
    bus2 = RecordingBus()
    FuzzWorker(bus2, 0x200, rate_hz=100.0, max_iter=3).run()
    assert bus2.sent == []


def test_replay_worker_gated():
    from canlab.core.replay import ReplayWorker
    df = pd.DataFrame([{"Timestamp": 0.0, "ID": "1A0", **{f"B{i}": 0 for i in range(8)}},
                       {"Timestamp": 0.001, "ID": "1A0", **{f"B{i}": 0 for i in range(8)}}])
    bus = RecordingBus()
    ReplayWorker(bus, df, speed=100.0).run()
    assert bus.sent == []                         # disarmed
    safety.set_armed(True)
    bus2 = RecordingBus()
    ReplayWorker(bus2, df, speed=100.0).run()
    assert len(bus2.sent) == 2


def test_clear_dtc_style_gate_via_gated_send():
    # The GUI clear-DTC / send-once paths call gated_send directly.
    bus = RecordingBus()
    with pytest.raises(BusNotArmedError):
        gated_send(bus, FakeMsg(0x7DF, bytes([0x04, 0x14, 0xFF, 0xFF, 0xFF])))
    assert bus.sent == []
