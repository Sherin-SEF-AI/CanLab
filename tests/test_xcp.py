"""
Tests for the read-only XCP-over-CAN client (core.xcp).

Uses a scripted fake bus that replies to XCP commands with positive (0xFF) or
error (0xFE) response frames. python-can is installed in this venv, but
conftest.py injects a minimal fake `can` module when it isn't, so these tests
run with no hardware library present.
"""
import pytest

from core.xcp import XCPClient, XCPError, XCP_ERROR_CODES


class FakeMsg:
    def __init__(self, arbitration_id, data):
        self.arbitration_id = arbitration_id
        self.data = bytes(data)


class ScriptedBus:
    """
    Fake python-can bus. Records TX frames and, keyed on the command byte
    (frame byte 0), queues a scripted DTO response. Memory reads are served from
    a byte-addressed dict so SHORT_UPLOAD/UPLOAD return real bytes.

    ``fail_cmd`` (command id -> error code) forces an ERR (0xFE) response.
    """

    CRO_ID = 0x7E0
    DTO_ID = 0x7E8

    def __init__(self, memory=None, byte_order="little", fail_cmd=None,
                 max_cto=8):
        self.sent = []
        self._rx = []
        self._memory = dict(memory or {})   # {address: byte}
        self._byte_order = byte_order
        self._fail_cmd = dict(fail_cmd or {})
        self._max_cto = max_cto
        self._mta = 0

    # -- helpers ---------------------------------------------------------
    def _addr(self, frame, off):
        return int.from_bytes(frame[off:off + 4], self._byte_order)

    def _read(self, addr, n):
        return bytes(self._memory.get(addr + i, 0) for i in range(n))

    # -- bus API ---------------------------------------------------------
    def send(self, msg):
        frame = bytes(msg.data)
        self.sent.append((msg.arbitration_id, frame))
        cmd = frame[0]

        if cmd in self._fail_cmd:
            self._rx.append(FakeMsg(self.DTO_ID, bytes([0xFE, self._fail_cmd[cmd]])))
            return

        if cmd == 0xFF:  # CONNECT
            bo_bit = 0x01 if self._byte_order == "big" else 0x00
            max_dto = (8).to_bytes(2, self._byte_order)
            resp = bytes([0xFF, 0x00, bo_bit, self._max_cto]) + max_dto + bytes([0x01, 0x01])
            self._rx.append(FakeMsg(self.DTO_ID, resp))
        elif cmd == 0xFE:  # DISCONNECT
            self._rx.append(FakeMsg(self.DTO_ID, bytes([0xFF])))
        elif cmd == 0xFD:  # GET_STATUS
            self._rx.append(FakeMsg(self.DTO_ID, bytes([0xFF, 0x00, 0x00, 0x00, 0x12, 0x34])))
        elif cmd == 0xFB:  # GET_COMM_MODE_INFO
            self._rx.append(FakeMsg(self.DTO_ID, bytes([0xFF, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x11])))
        elif cmd == 0xF6:  # SET_MTA
            self._mta = self._addr(frame, 4)
            self._rx.append(FakeMsg(self.DTO_ID, bytes([0xFF])))
        elif cmd == 0xF5:  # UPLOAD from current MTA
            n = frame[1]
            data = self._read(self._mta, n)
            self._mta += n
            self._rx.append(FakeMsg(self.DTO_ID, bytes([0xFF]) + data))
        elif cmd == 0xF4:  # SHORT_UPLOAD
            n = frame[1]
            addr = self._addr(frame, 4)
            data = self._read(addr, n)
            self._rx.append(FakeMsg(self.DTO_ID, bytes([0xFF]) + data))
        else:
            # generic positive ack
            self._rx.append(FakeMsg(self.DTO_ID, bytes([0xFF])))

    def recv(self, timeout=0.05):
        return self._rx.pop(0) if self._rx else None


def make_client(**kw):
    bus = ScriptedBus(**kw)
    return XCPClient(bus, cro_id=ScriptedBus.CRO_ID, dto_id=ScriptedBus.DTO_ID,
                     timeout=0.5), bus


def test_connect_succeeds_and_negotiates():
    cli, bus = make_client()
    info = cli.connect()
    assert cli.connected is True
    assert info["byte_order"] == "little"
    assert info["max_cto"] == 8
    # First frame sent was CONNECT (0xFF) with mode byte.
    assert bus.sent[0][1][0] == 0xFF


def test_connect_negotiates_big_endian():
    cli, _ = make_client(byte_order="big")
    info = cli.connect()
    assert info["byte_order"] == "big"
    assert cli.byte_order == "big"


def test_short_upload_returns_scripted_bytes():
    mem = {0x1000: 0xDE, 0x1001: 0xAD, 0x1002: 0xBE, 0x1003: 0xEF}
    cli, _ = make_client(memory=mem)
    cli.connect()
    assert cli.short_upload(0x1000, 4) == bytes([0xDE, 0xAD, 0xBE, 0xEF])


def test_read_memory_via_short_upload():
    mem = {0x2000 + i: i for i in range(4)}
    cli, _ = make_client(memory=mem)
    cli.connect()
    assert cli.read_memory(0x2000, 4) == bytes([0, 1, 2, 3])


def test_read_memory_chunks_across_max_cto():
    # 20 bytes with MAX_CTO=8 -> 7 bytes/frame -> 3 transfers, reassembled intact.
    mem = {0x3000 + i: (i & 0xFF) for i in range(20)}
    cli, bus = make_client(memory=mem, max_cto=8)
    cli.connect()
    out = cli.read_memory(0x3000, 20)
    assert out == bytes(i & 0xFF for i in range(20))
    short_uploads = [f for _, f in bus.sent if f[0] == 0xF4]
    assert len(short_uploads) == 3   # 7 + 7 + 6


def test_read_memory_via_set_mta_upload():
    mem = {0x4000 + i: (0x40 + i) for i in range(6)}
    cli, bus = make_client(memory=mem)
    cli.connect()
    out = cli.read_memory(0x4000, 6, use_short_upload=False)
    assert out == bytes(0x40 + i for i in range(6))
    assert any(f[0] == 0xF6 for _, f in bus.sent)   # SET_MTA used
    assert any(f[0] == 0xF5 for _, f in bus.sent)   # UPLOAD used


def test_poll_measurement_decodes_integer_little_endian():
    mem = {0x5000: 0x01, 0x5001: 0x02}
    cli, _ = make_client(memory=mem, byte_order="little")
    cli.connect()
    assert cli.poll_measurement(0x5000, 2) == 0x0201


def test_poll_measurement_decodes_integer_big_endian():
    mem = {0x5000: 0x01, 0x5001: 0x02}
    cli, _ = make_client(memory=mem, byte_order="big")
    cli.connect()
    assert cli.poll_measurement(0x5000, 2) == 0x0102


def test_negative_response_raises_xcperror():
    # Slave rejects SHORT_UPLOAD with ERR_ACCESS_DENIED (0x24).
    cli, _ = make_client(fail_cmd={0xF4: 0x24})
    cli.connect()
    with pytest.raises(XCPError) as ei:
        cli.short_upload(0x1000, 4)
    assert ei.value.code == 0x24
    assert ei.value.name == "ERR_ACCESS_DENIED"


def test_connect_error_is_raised():
    cli, _ = make_client(fail_cmd={0xFF: 0x10})
    with pytest.raises(XCPError) as ei:
        cli.connect()
    assert ei.value.code == 0x10
    assert cli.connected is False


def test_get_status_and_comm_mode_info():
    cli, _ = make_client()
    cli.connect()
    status = cli.get_status()
    assert status["session_config_id"] == 0x3412  # 0x12,0x34 little-endian
    info = cli.get_comm_mode_info()
    assert info["xcp_driver_version"] == 0x11


def test_disconnect_clears_state():
    cli, bus = make_client()
    cli.connect()
    cli.disconnect()
    assert cli.connected is False
    assert any(f[0] == 0xFE for _, f in bus.sent)


def test_timeout_raises_xcperror():
    cli, bus = make_client()
    cli.connect()
    bus._rx.clear()          # drain, then make recv always return None
    bus.send = lambda msg: None
    with pytest.raises(XCPError):
        cli.short_upload(0x1000, 1)


def test_error_codes_table_present():
    assert XCP_ERROR_CODES[0x20] == "ERR_CMD_UNKNOWN"
