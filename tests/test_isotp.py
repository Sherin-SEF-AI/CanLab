"""Tests for the ISO-TP multi-frame transmit fix (#7)."""
from core.isotp import ISOTPSession


class FakeMsg:
    def __init__(self, arbitration_id, data):
        self.arbitration_id = arbitration_id
        self.data = bytes(data)


class ScriptedBus:
    """Records TX frames; replies with FC after the FF and a SF after the CFs."""

    def __init__(self, response=None):
        self.sent = []
        self._rx = []
        self._response = response

    def send(self, msg):
        self.sent.append((msg.arbitration_id, bytes(msg.data)))
        pci = msg.data[0] & 0xF0
        if pci == 0x00 and self._response:    # Single Frame request -> reply now
            self._rx.append(self._response)
            self._response = None
        elif pci == 0x10:                     # First Frame -> reply Flow Control (CTS)
            self._rx.append(FakeMsg(0x7E8, bytes([0x30, 0x00, 0x00, 0, 0, 0, 0, 0])))
        elif pci == 0x20 and self._response:  # after a CF -> queue the response once
            self._rx.append(self._response)
            self._response = None

    def recv(self, timeout=0.05):
        return self._rx.pop(0) if self._rx else None


def test_single_frame_send():
    bus = ScriptedBus(response=FakeMsg(0x7E8, bytes([0x02, 0x50, 0x01, 0, 0, 0, 0, 0])))
    sess = ISOTPSession(bus, tx_id=0x7E0, rx_id=0x7E8)
    resp = sess.send(bytes([0x10, 0x03]), timeout=0.5)
    assert bus.sent[0][1][0] == 0x02                 # SF, length 2
    assert resp == bytes([0x50, 0x01])


def test_multi_frame_send_transmits_all_bytes():
    # 12-byte request must produce FF + at least one CF and reassemble intact.
    payload = bytes(range(12))
    bus = ScriptedBus(response=FakeMsg(0x7E8, bytes([0x02, 0x50, 0x01, 0, 0, 0, 0, 0])))
    sess = ISOTPSession(bus, tx_id=0x7E0, rx_id=0x7E8)
    resp = sess.send(payload, timeout=0.5)

    pcis = [d[0] for _, d in bus.sent]
    assert (pcis[0] & 0xF0) == 0x10, "first frame is a First Frame"
    assert any((p & 0xF0) == 0x20 for p in pcis), "at least one Consecutive Frame sent"

    reassembled = bus.sent[0][1][2:8]
    for _, d in bus.sent[1:]:
        if (d[0] & 0xF0) == 0x20:
            reassembled += d[1:8]
    assert reassembled[:12] == payload
    assert resp == bytes([0x50, 0x01])


def test_stmin_decode():
    assert ISOTPSession._stmin_seconds(0x00) == 0.0
    assert ISOTPSession._stmin_seconds(0x0A) == 0.010
    assert abs(ISOTPSession._stmin_seconds(0xF1) - 0.0001) < 1e-9
    assert ISOTPSession._stmin_seconds(0xFF) == 0.0
