"""UDS/OBD transport: what actually goes on the wire, and 0x78 handling.

The bugs these pin were invisible before: callers passed payloads that already
contained an ISO-TP length byte, so every physically addressed request went out
as a malformed First Frame and every OBD request was double-framed. The frames
still left the adapter, which is why nothing surfaced until someone checked the
bytes.
"""
import pytest

from canlab.core import safety
from canlab.core.isotp import ISOTPSession, is_response_pending
from canlab.core import uds as uds_mod
from canlab.core.uds import DESTRUCTIVE_SERVICES, UDSScanner, decode_dtc_records
from tests.doubles import FakeMsg, RecordingBus

pytestmark = pytest.mark.usefixtures("armed")


def sf(arb, payload):
    """A single-frame response carrying ``payload``."""
    return FakeMsg(arb, bytes([len(payload)]) + bytes(payload)
                   + bytes(7 - len(payload)))


def test_obd_request_is_a_correct_single_frame():
    bus = RecordingBus()
    ISOTPSession(bus, 0x7E0, 0x7E8).request(bytes([0x01, 0x0C]), timeout=0.01)
    assert bus.sent[0].data == bytes([0x02, 0x01, 0x0C, 0, 0, 0, 0, 0])
    assert bus.sent[0].arbitration_id == 0x7E0


def test_physical_uds_request_is_a_single_frame_not_a_first_frame():
    bus = RecordingBus()
    ISOTPSession(bus, 0x7E0, 0x7E8).request(bytes([0x22, 0xF1, 0x90]), timeout=0.01)
    pci = bus.sent[0].data[0]
    assert pci >> 4 == 0x0, "a 3-byte request must be a Single Frame"
    assert bus.sent[0].data == bytes([0x03, 0x22, 0xF1, 0x90, 0, 0, 0, 0])


def test_response_payload_excludes_the_pci_byte():
    bus = RecordingBus()
    bus.feed(sf(0x7E8, [0x41, 0x0C, 0x1A, 0xF8]))
    got = ISOTPSession(bus, 0x7E0, 0x7E8).request(bytes([0x01, 0x0C]), timeout=0.3)
    assert got == bytes([0x41, 0x0C, 0x1A, 0xF8])


def test_response_pending_is_waited_out():
    bus = RecordingBus()
    bus.feed(sf(0x7E8, [0x7F, 0x22, 0x78]),
             sf(0x7E8, [0x7F, 0x22, 0x78]),
             sf(0x7E8, [0x62, 0xF1, 0x90, 0x41]))
    got = ISOTPSession(bus, 0x7E0, 0x7E8).request(bytes([0x22, 0xF1, 0x90]),
                                                  timeout=0.3, p2_star=0.3)
    assert got == bytes([0x62, 0xF1, 0x90, 0x41])
    assert is_response_pending(bytes([0x7F, 0x22, 0x78]))
    assert not is_response_pending(bytes([0x62, 0xF1, 0x90]))


def test_a_real_negative_response_is_still_returned():
    bus = RecordingBus()
    bus.feed(sf(0x7E8, [0x7F, 0x22, 0x31]))     # requestOutOfRange
    got = ISOTPSession(bus, 0x7E0, 0x7E8).request(bytes([0x22, 0xF1, 0x90]), timeout=0.3)
    assert got == bytes([0x7F, 0x22, 0x31])


def test_functional_request_accepts_any_ecu_response_id():
    bus = RecordingBus()
    bus.feed(sf(0x7EA, [0x41, 0x0D, 0x50]))
    sess = ISOTPSession(bus, 0x7DF, range(0x7E8, 0x7F0))
    assert sess.request(bytes([0x01, 0x0D]), timeout=0.3) == bytes([0x41, 0x0D, 0x50])
    assert sess.last_rx_id == 0x7EA


def test_fd_escape_single_frame_for_long_payloads():
    bus = RecordingBus()
    payload = bytes(range(0x20, 0x30))          # 16 bytes: too long for a classic SF
    ISOTPSession(bus, 0x7E0, 0x7E8, tx_dl=64).request(payload, timeout=0.01)
    frame = bus.sent[0].data
    assert frame[0] == 0x00 and frame[1] == 16   # escape SF: PCI 0, then length
    assert frame[2:18] == payload


def test_obd_poller_decodes_a_pid_end_to_end():
    from canlab.core.obd2_poller import OBD2Poller
    bus = RecordingBus()
    bus.feed(sf(0x7E8, [0x41, 0x0C, 0x1A, 0xF8]))   # 1726 rpm raw -> 1726
    poller = OBD2Poller(bus, pids=[0x0C], interval_ms=50)
    seen = []
    poller.pid_value.connect(lambda pid, val, unit: seen.append((pid, val, unit)))
    poller._running = True

    def stop_after_first(*_a):
        poller._running = False
    poller.pid_value.connect(stop_after_first)
    poller.run()
    assert seen and seen[0][0] == 0x0C
    assert seen[0][1] == pytest.approx(1726.0)
    assert bus.sent[0].data[:3] == bytes([0x02, 0x01, 0x0C])


def test_destructive_services_cover_the_state_changing_ones():
    for svc in (0x11, 0x14, 0x27, 0x28, 0x2E, 0x2F, 0x31, 0x34, 0x3D, 0x85, 0x87):
        assert svc in DESTRUCTIVE_SERVICES, f"0x{svc:02X} must be treated as destructive"


@pytest.fixture
def fast_scan(monkeypatch):
    """Shrink the per-service probe so the scan test isn't dominated by waits."""
    monkeypatch.setattr(uds_mod, "SERVICE_PROBE_TIMEOUT", 0.01)
    monkeypatch.setattr(uds_mod, "SERVICE_PROBE_GAP", 0.0)


def test_service_scan_skips_destructive_services_by_default(fast_scan):
    bus = RecordingBus()
    scanner = UDSScanner(bus, mode="SERVICES")
    scanner._running = True
    results = []
    scanner.service_result.connect(lambda ecu, svc, ok, data: results.append((svc, ok)))
    scanner._scan_services()
    sent_services = {m.data[1] for m in bus.sent if len(m.data) > 1}
    for svc in DESTRUCTIVE_SERVICES:
        assert svc not in sent_services, f"destructive 0x{svc:02X} was probed"
    assert 0x22 in sent_services        # a read-only service still is


def test_service_scan_probes_everything_when_explicitly_unsafe(fast_scan):
    bus = RecordingBus()
    scanner = UDSScanner(bus, mode="SERVICES", allow_unsafe=True)
    scanner._running = True
    scanner._scan_services()
    sent = {m.data[1] for m in bus.sent if len(m.data) > 1}
    assert 0x11 in sent and 0x31 in sent


def test_dtc_records_decode_to_standard_codes():
    payload = bytes([0x59, 0x02, 0xFF,
                     0x01, 0x23, 0x00, 0x2F,     # P0123
                     0xC1, 0x45, 0x00, 0x2F])    # U0145
    assert decode_dtc_records(payload) == ["P0123-00", "U0145-00"]
    assert decode_dtc_records(bytes([0x59, 0x02, 0xFF])) == []
    assert decode_dtc_records(b"") == []


def test_nothing_is_transmitted_while_disarmed():
    safety.set_armed(False)
    try:
        bus = RecordingBus()
        assert ISOTPSession(bus, 0x7E0, 0x7E8).request(bytes([0x01, 0x0C]),
                                                       timeout=0.05) is None
        assert bus.sent == []
    finally:
        safety.set_armed(True)


# ── OBD-II the way J1979 intends ─────────────────────────────────────────────

def _mask(*pids, base=0x00):
    """A supported-PIDs mask for the window starting at ``base``."""
    m = 0
    for pid in pids:
        m |= 1 << (32 - (pid - base))
    return list(m.to_bytes(4, "big"))


class _ScriptedCar:
    """Answers like a car that supports four PIDs and has one stored and one
    pending code. Anything else gets no reply, as on a real bus."""

    ANSWERS = {
        (0x01, 0x00): [0x41, 0x00, *_mask(0x0C, 0x0D, 0x20)],
        (0x01, 0x20): [0x41, 0x20, *_mask(0x2F, 0x33, base=0x20)],
        (0x01, 0x0C): [0x41, 0x0C, 0x1A, 0xF8],
        (0x01, 0x0D): [0x41, 0x0D, 0x3C],
        (0x01, 0x2F): [0x41, 0x2F, 0x80],
        (0x01, 0x33): [0x41, 0x33, 0x65],
        (0x03,): [0x43, 0x01, 0x01, 0x33],
        (0x07,): [0x47, 0x01, 0x03, 0x01],
    }

    def __init__(self):
        self.requests = []

    def __call__(self, msg):
        n = msg.data[0] & 0x0F
        req = tuple(msg.data[1:1 + n])
        self.requests.append(req)
        key = req[:2] if req[0] == 0x01 else req[:1]
        payload = self.ANSWERS.get(key)
        return sf(0x7E8, payload) if payload else None


def test_pid_scan_asks_what_is_supported_and_reads_only_that():
    car = _ScriptedCar()
    scanner = UDSScanner(RecordingBus(on_send=car), mode="PID")
    got, notes = [], []
    scanner.pid_result.connect(lambda pid, name, value, unit: got.append((pid, value, unit)))
    scanner.status.connect(notes.append)
    scanner._scan_pids()
    assert car.requests == [(0x01, 0x00), (0x01, 0x20), (0x01, 0x0C), (0x01, 0x0D),
                            (0x01, 0x2F), (0x01, 0x33)]
    assert got == [(0x0C, 1726.0, "rpm"), (0x0D, 60.0, "km/h"),
                   (0x2F, 50.2, "%"), (0x33, 101.0, "kPa")]
    assert any("4 PIDs supported, reading 4" in n for n in notes)


def test_pid_scan_stops_when_nothing_speaks_mode_01():
    bus = RecordingBus()
    scanner = UDSScanner(bus, mode="PID")
    notes = []
    scanner.status.connect(notes.append)
    scanner._scan_pids()
    assert len(bus.sent) == 1                       # one question, not seventy-eight
    assert any("nothing speaks OBD-II" in n for n in notes)


def test_dtcs_are_read_with_modes_03_and_07_before_uds():
    car = _ScriptedCar()
    scanner = UDSScanner(RecordingBus(on_send=car), mode="DTC")
    got = []
    scanner.dtc_result.connect(got.append)
    scanner._read_dtc()
    assert got == [["P0133", "P0301 (pending)"]]
    assert scanner.dtc_answered
    assert (0x19, 0x02, 0xFF) not in car.requests    # OBD-II answered, no UDS needed


def test_no_answer_is_not_reported_as_no_codes():
    bus = RecordingBus()
    scanner = UDSScanner(bus, mode="DTC")
    got = []
    scanner.dtc_result.connect(got.append)
    scanner._read_dtc()
    assert got == [[]] and not scanner.dtc_answered
    tried = [bytes(m.data[1:1 + m.data[0]]) for m in bus.sent]
    assert tried == [b"\x03", b"\x07", b"\x19\x02\xff"]

    from types import SimpleNamespace
    from canlab.tabs.diagnostics_tab import DiagnosticsTab
    shown = {}
    fake = SimpleNamespace(
        _dtc_worker=scanner,
        dtc_text=SimpleNamespace(setPlainText=lambda t: shown.update(text=t)),
        uds_log=SimpleNamespace(append=lambda t: None))
    DiagnosticsTab._on_dtc_result(fake, [])
    assert "not a clean result" in shown["text"]
    scanner.dtc_answered = True
    DiagnosticsTab._on_dtc_result(fake, [])
    assert shown["text"] == "No DTCs stored or pending."
