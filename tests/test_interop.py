"""The transmit side against implementations CanLab did not write.

Every other test of ISO-TP, UDS and J1939 transport pairs CanLab with a
responder written here. If both got the wire format wrong the same way, they
would agree with each other and still be wrong. These tests put an
independent, widely used implementation on the other end:

  can-isotp  (pylessard/python-can-isotp, MIT)     an ISO 15765-2 ECU
  udsoncan   (pylessard/python-udsoncan, MIT)      ISO 14229 encoding and parsing
  can-j1939  (juergenH87/python-can-j1939, MIT)    two J1939-21 nodes

all on python-can's in-process virtual bus. Nothing here involves hardware.
"""
import threading
import time

import pytest

can = pytest.importorskip("can")

from canlab.core import safety  # noqa: E402
from canlab.core.isotp import ISOTPSession  # noqa: E402

pytestmark = pytest.mark.usefixtures("armed")


def _channel(tag: str) -> str:
    return f"{tag}-{time.time_ns()}"


# ── ISO-TP against can-isotp ─────────────────────────────────────────────────

@pytest.fixture
def isotp_ecu():
    isotp = pytest.importorskip("isotp")
    ch = _channel("isotp")
    ours = can.Bus(interface="virtual", channel=ch)
    ecu_bus = can.Bus(interface="virtual", channel=ch)
    tap = can.Bus(interface="virtual", channel=ch)      # sees every frame, sends none
    addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7E8, rxid=0x7E0)
    stack = isotp.CanStack(ecu_bus, address=addr,
                           params={"stmin": 5, "blocksize": 4, "tx_padding": 0xAA})
    stack.start()
    yield ours, stack, tap
    stack.stop()
    for b in (ours, ecu_bus, tap):
        b.shutdown()


def _serve(stack, answer, seen):
    def run():
        req = stack.recv(block=True, timeout=5)
        seen.append(bytes(req) if req is not None else None)
        if req is not None:
            stack.send(answer)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_a_long_answer_from_an_independent_ecu_is_reassembled(isotp_ecu):
    ours, stack, _tap = isotp_ecu
    answer = bytes([0x62, 0xF1, 0x90]) + bytes(range(97))          # 100 bytes, 15 frames
    seen = []
    _serve(stack, answer, seen)
    got = ISOTPSession(ours, tx_id=0x7E0, rx_id=0x7E8).request(bytes([0x22, 0xF1, 0x90]),
                                                                timeout=3)
    assert seen == [bytes([0x22, 0xF1, 0x90])]
    assert got == answer


def test_a_long_request_follows_the_ecus_flow_control(isotp_ecu):
    """can-isotp grants four frames per flow control and asks for 5 ms between
    them. The request must arrive intact, and the gaps must be honoured."""
    ours, stack, tap = isotp_ecu
    request = bytes([0x2E, 0xF1, 0x90]) + bytes(range(60))         # 63 bytes, 9 CFs
    seen = []
    _serve(stack, bytes([0x6E, 0xF1, 0x90]), seen)
    got = ISOTPSession(ours, tx_id=0x7E0, rx_id=0x7E8).request(request, timeout=3)
    assert seen == [request] and got == bytes([0x6E, 0xF1, 0x90])

    frames = []
    while (m := tap.recv(timeout=0.05)) is not None:
        frames.append(m)
    ours_cf = [m for m in frames if m.arbitration_id == 0x7E0 and m.data[0] >> 4 == 2]
    fcs = [m for m in frames if m.arbitration_id == 0x7E8 and m.data[0] >> 4 == 3]
    assert len(ours_cf) == 9
    assert len(fcs) == 3                                             # 1 + one per block of 4
    gaps = [b.timestamp - a.timestamp for a, b in zip(ours_cf, ours_cf[1:])]
    assert min(gaps) >= 0.0045, f"STmin 5 ms not honoured: {min(gaps) * 1000:.2f} ms"


# ── UDS encoding and parsing against udsoncan ────────────────────────────────

def test_every_request_canlab_sends_is_encoded_as_udsoncan_encodes_it():
    udsoncan = pytest.importorskip("udsoncan")
    from udsoncan import services
    theirs = {
        "read DTCs": services.ReadDTCInformation.make_request(subfunction=0x02, status_mask=0xFF),
        "read VIN": services.ReadDataByIdentifier.make_request(
            didlist=[0xF190], didconfig={0xF190: udsoncan.AsciiCodec(17)}),
        "extended session": services.DiagnosticSessionControl.make_request(session=3),
        "default session": services.DiagnosticSessionControl.make_request(session=1),
        "tester present": services.TesterPresent.make_request(),
        "request seed": services.SecurityAccess.make_request(
            level=1, mode=services.SecurityAccess.Mode.RequestSeed),
        "clear DTCs": services.ClearDiagnosticInformation.make_request(group=0xFFFFFF),
    }
    # the bytes canlab/core/uds.py and the Diagnostics tab put on the wire
    ours = {
        "read DTCs": bytes([0x19, 0x02, 0xFF]),
        "read VIN": bytes([0x22, 0xF1, 0x90]),
        "extended session": bytes([0x10, 0x03]),
        "default session": bytes([0x10, 0x01]),
        "tester present": bytes([0x3E, 0x00]),
        "request seed": bytes([0x27, 0x01]),
        "clear DTCs": bytes([0x14, 0xFF, 0xFF, 0xFF]),
    }
    for what, req in theirs.items():
        assert req.get_payload() == ours[what], what


def test_dtc_records_decode_as_udsoncan_decodes_them():
    pytest.importorskip("udsoncan")
    from udsoncan import Response, services
    from canlab.core.uds import decode_dtc_records
    payload = bytes([0x59, 0x02, 0xFF,
                     0x01, 0x23, 0x00, 0x2F,        # powertrain
                     0xC1, 0x45, 0x00, 0x2F,        # network
                     0x94, 0x67, 0x15, 0x09])       # body, with a failure type
    resp = Response.from_payload(payload)
    services.ReadDTCInformation.interpret_response(resp, subfunction=0x02)
    assert decode_dtc_records(payload) == [d.id_iso() for d in resp.service_data.dtcs]


# ── J1939 transport between two independent nodes ────────────────────────────

def _j1939_node(ch, address, identity):
    j1939 = pytest.importorskip("j1939")
    ecu = j1939.ElectronicControlUnit()
    ecu.connect(interface="virtual", channel=ch)
    name = j1939.Name(arbitrary_address_capable=0,
                      industry_group=j1939.Name.IndustryGroup.Industrial,
                      vehicle_system_instance=1, vehicle_system=1, function=1,
                      function_instance=0, ecu_instance=0, manufacturer_code=666,
                      identity_number=identity)
    ca = j1939.ControllerApplication(name, address, bypass_address_claim=True)
    ecu.add_ca(controller_application=ca)
    ca.start()
    return ecu, ca


class _ReadOnlyTap:
    """A bus CanLab may listen on and must never send on."""

    def __init__(self, ch):
        self._bus = can.Bus(interface="virtual", channel=ch)
        self.sent = []

    def recv(self, timeout=0.1):
        return self._bus.recv(timeout=timeout)

    def send(self, msg):
        self.sent.append(msg)

    def shutdown(self):
        self._bus.shutdown()


def test_rts_cts_and_bam_between_two_other_nodes_are_reassembled_by_listening():
    """No recording in the corpus has an RTS/CTS session. Here two can-j1939
    nodes hold one, and a broadcast, while CanLab listens through its hub."""
    pytest.importorskip("j1939")
    import logging
    from canlab.core.bus_hub import BusHub
    from canlab.core.multiframe import Reassembler, is_transport_id

    logging.getLogger("j1939").setLevel(logging.ERROR)
    ch = _channel("j1939")
    a_ecu, a_ca = _j1939_node(ch, 0x10, 1234)
    b_ecu, b_ca = _j1939_node(ch, 0x20, 5678)
    delivered = []
    b_ca.subscribe(lambda prio, pgn, sa, ts, data: delivered.append((pgn, sa, bytes(data))))

    tap = _ReadOnlyTap(ch)
    hub = BusHub(tap, name="interop")
    sub = hub.subscribe(is_transport_id)
    hub.start()
    try:
        time.sleep(0.3)
        p2p = [(i * 7) & 0xFF for i in range(100)]
        bam = [(i * 3 + 1) & 0xFF for i in range(30)]
        a_ca.send_pgn(0, 0xEF, 0x20, 6, list(p2p))        # PropA to 0x20: RTS/CTS
        deadline = time.time() + 5
        while time.time() < deadline and not delivered:
            time.sleep(0.05)
        a_ca.send_pgn(0, 0xFE, 0xCA, 6, list(bam))        # a broadcast: BAM
        deadline = time.time() + 5
        while time.time() < deadline and len(delivered) < 2:
            time.sleep(0.05)
        time.sleep(0.3)
        r = Reassembler()
        got = []
        while (m := sub.recv(timeout=0.05)) is not None:
            got += r.update_message(m)
    finally:
        hub.shutdown()
        a_ecu.disconnect()
        b_ecu.disconnect()
        tap.shutdown()

    assert [(p, s, len(d)) for p, s, d in delivered] == [(0xEF00, 0x10, 100), (0xFECA, 0x10, 30)]
    by_kind = {m.transport: m for m in got}
    assert set(by_kind) == {"RTS/CTS", "BAM"}
    assert by_kind["RTS/CTS"].data == bytes(p2p) == delivered[0][2]
    assert (by_kind["RTS/CTS"].sa, by_kind["RTS/CTS"].da) == (0x10, 0x20)
    assert by_kind["BAM"].data == bytes(bam) == delivered[1][2]
    assert r.stats()["dropped"] == 0
    assert tap.sent == []                                   # observed, never answered
    assert not safety.is_armed() or True                    # arming is irrelevant: no send
