"""
XCP-over-CAN read/measurement client (ASAM MCD-1 XCP).

This is a **read-only** subset of XCP: it lets you CONNECT to an ECU/slave and
read (measure) internal variables by memory address. It deliberately implements
*no* memory-write / programming commands (DOWNLOAD, PROGRAM, MODIFY_BITS, …), so
it never puts a value onto the ECU and therefore never needs CanLab's transmit
safety gate (``core.safety.require_armed``). CONNECT / UPLOAD do send CAN frames,
but they are pure reads of ECU state — the same category as UDS ReadDataByIdentifier.

Transport (XCP on CAN):
    CRO (Command Request Object) : tester -> slave, arbitration id = ``cro_id``
    DTO (Data Transfer Object)   : slave  -> tester, arbitration id = ``dto_id``

Every CTO (command / response) frame:
    byte 0            : packet identifier
                          command  (tester->slave):  CONNECT=0xFF, DISCONNECT=0xFE, …
                          response (slave->tester):  0xFF = positive (RES)
                                                     0xFE = error    (ERR)
                                                     0xFD = event    (EV)
                                                     0xFC = service  (SERV)
    bytes 1..MAX_CTO  : parameters / data

Multi-byte address & length fields use the slave's byte order, which is
negotiated in the CONNECT response (COMM_MODE_BASIC bit 0: 0 = little/Intel,
1 = big/Motorola). Default is little-endian; :class:`XCPClient` exposes a
``byte_order`` flag and updates it from the CONNECT response.

Structured after ``core.uds`` / ``core.isotp`` / ``core.obd2_poller``:
``can`` is imported lazily inside methods so the module imports with no hardware
library present, and the QThread worker mirrors ``OBD2Poller``.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# XCP command (CTO) packet identifiers  (tester -> slave)
# ---------------------------------------------------------------------------
CMD_CONNECT            = 0xFF
CMD_DISCONNECT         = 0xFE
CMD_GET_STATUS         = 0xFD
CMD_SYNCH              = 0xFC
CMD_GET_COMM_MODE_INFO = 0xFB
CMD_GET_ID             = 0xFA
CMD_SET_MTA            = 0xF6
CMD_UPLOAD             = 0xF5
CMD_SHORT_UPLOAD       = 0xF4
CMD_BUILD_CHECKSUM     = 0xF3

# DAQ (dynamic data acquisition) commands — optional polling path (see below)
CMD_SET_DAQ_PTR        = 0xE2
CMD_WRITE_DAQ          = 0xE1
CMD_SET_DAQ_LIST_MODE  = 0xE0
CMD_START_STOP_DAQ_LIST = 0xDE
CMD_START_STOP_SYNCH   = 0xDD
CMD_FREE_DAQ           = 0xD6
CMD_ALLOC_DAQ          = 0xD5
CMD_ALLOC_ODT          = 0xD4
CMD_ALLOC_ODT_ENTRY    = 0xD3

# ---------------------------------------------------------------------------
# Response packet identifiers  (slave -> tester)
# ---------------------------------------------------------------------------
PID_RES  = 0xFF   # positive response
PID_ERR  = 0xFE   # error
PID_EV   = 0xFD   # event
PID_SERV = 0xFC   # service request

# START_STOP_DAQ_LIST modes
DAQ_STOP  = 0x00
DAQ_START = 0x01
DAQ_SELECT = 0x02

# ---------------------------------------------------------------------------
# XCP error codes (ERR packet byte 1)
# ---------------------------------------------------------------------------
XCP_ERROR_CODES: Dict[int, str] = {
    0x00: "ERR_CMD_SYNCH",
    0x10: "ERR_CMD_BUSY",
    0x11: "ERR_DAQ_ACTIVE",
    0x12: "ERR_PGM_ACTIVE",
    0x20: "ERR_CMD_UNKNOWN",
    0x21: "ERR_CMD_SYNTAX",
    0x22: "ERR_OUT_OF_RANGE",
    0x23: "ERR_WRITE_PROTECTED",
    0x24: "ERR_ACCESS_DENIED",
    0x25: "ERR_ACCESS_LOCKED",
    0x26: "ERR_PAGE_NOT_VALID",
    0x27: "ERR_MODE_NOT_VALID",
    0x28: "ERR_SEGMENT_NOT_VALID",
    0x29: "ERR_SEQUENCE",
    0x2A: "ERR_DAQ_CONFIG",
    0x30: "ERR_MEMORY_OVERFLOW",
    0x31: "ERR_GENERIC",
    0x32: "ERR_VERIFY",
    0x33: "ERR_RESOURCE_TEMPORARY_NOT_ACCESSIBLE",
}


class XCPError(Exception):
    """
    Raised when the slave returns an ERR packet (0xFE) or a protocol/timeout
    failure occurs. ``code`` is the XCP error code byte (or ``None`` for
    non-ERR failures such as timeouts) and ``name`` is its decoded mnemonic.
    """

    def __init__(self, message: str, code: Optional[int] = None):
        self.code = code
        self.name = XCP_ERROR_CODES.get(code, "UNKNOWN") if code is not None else None
        if code is not None:
            message = f"{message} (0x{code:02X} {self.name})"
        super().__init__(message)


class _FakeMsg:
    """Lightweight stand-in for can.Message (matches core.uds._FakeMsg)."""
    __slots__ = ("arbitration_id", "data")

    def __init__(self, arb_id: int, data: bytes):
        self.arbitration_id = arb_id
        self.data = bytes(data)


class XCPClient:
    """
    Plain (non-Qt) blocking XCP-over-CAN client, read-only subset.

    Parameters
    ----------
    bus         : a python-can Bus (anything with ``send(msg)`` / ``recv(timeout)``)
    cro_id      : arbitration id for Command Request Objects (tester -> slave)
    dto_id      : arbitration id for Data Transfer Objects (slave -> tester)
    timeout     : per-command response timeout, seconds
    byte_order  : 'little' (default / Intel) or 'big' (Motorola); overwritten by
                  the CONNECT response's negotiated order.
    extended_id : use 29-bit CAN identifiers for the CRO.

    Typical use::

        cli = XCPClient(bus, cro_id=0x7E0, dto_id=0x7E8)
        cli.connect()
        raw = cli.read_memory(0x40000000, 4)
        val = cli.poll_measurement(0x40000000, 4)
        cli.disconnect()
    """

    def __init__(self, bus, cro_id: int, dto_id: int, timeout: float = 1.0,
                 byte_order: str = "little", extended_id: bool = False):
        self._bus = bus
        self.cro_id = cro_id
        self.dto_id = dto_id
        self.timeout = timeout
        self.byte_order = byte_order      # 'little' | 'big'
        self.extended_id = extended_id
        self.connected = False
        # Populated by connect(): sensible XCP defaults until negotiated.
        self.max_cto = 8
        self.max_dto = 8
        self.resource = 0
        self.comm_mode_basic = 0

    # ------------------------------------------------------------------ I/O
    def _send(self, payload: bytes) -> None:
        """Transmit a single CRO frame (padded to 8 bytes)."""
        import can
        frame = bytes(payload)[:8]
        msg = can.Message(
            arbitration_id=self.cro_id,
            data=frame,
            is_extended_id=self.extended_id,
        )
        self._bus.send(msg)

    def _recv(self, timeout: Optional[float] = None):
        """
        Receive the next DTO frame addressed to us, skipping unrelated traffic.
        Event (0xFD) and service-request (0xFC) packets are skipped while we wait
        for a command response. Returns the frame or None on timeout.
        """
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            resp = self._bus.recv(timeout=0.05)
            if resp is None:
                continue
            if resp.arbitration_id != self.dto_id:
                continue
            data = bytes(resp.data)
            if data and data[0] in (PID_EV, PID_SERV):
                # asynchronous slave notification — not our command reply
                continue
            return resp
        return None

    def _command(self, payload: bytes, timeout: Optional[float] = None) -> bytes:
        """
        Send a command and return the response payload *including* the leading
        0xFF positive-response byte. Raises XCPError on an ERR packet or timeout.
        """
        self._send(payload)
        resp = self._recv(timeout)
        if resp is None:
            raise XCPError(f"No response to command 0x{payload[0]:02X} within "
                           f"{self.timeout:g}s")
        data = bytes(resp.data)
        if not data:
            raise XCPError("Empty response frame")
        if data[0] == PID_ERR:
            code = data[1] if len(data) > 1 else None
            raise XCPError(f"XCP command 0x{payload[0]:02X} failed", code=code)
        if data[0] != PID_RES:
            raise XCPError(f"Unexpected response packet id 0x{data[0]:02X} "
                           f"to command 0x{payload[0]:02X}")
        return data

    # ----------------------------------------------------------- byte order
    def _addr_bytes(self, address: int) -> bytes:
        return int(address & 0xFFFFFFFF).to_bytes(4, self.byte_order)

    def _word(self, hi_lo: bytes) -> int:
        return int.from_bytes(hi_lo, self.byte_order)

    # -------------------------------------------------------- session control
    def connect(self, mode: int = 0x00) -> dict:
        """
        CONNECT (0xFF). ``mode`` 0x00 = normal, 0x01 = user-defined.
        Parses COMM_MODE_BASIC (updates ``byte_order``), MAX_CTO and MAX_DTO,
        marks the session connected, and returns the decoded fields.
        """
        data = self._command(bytes([CMD_CONNECT, mode & 0xFF]))
        # CONNECT response: FF, RESOURCE, COMM_MODE_BASIC, MAX_CTO,
        #                   MAX_DTO(word), XCP_PROTO_VER, XCP_TRANSPORT_VER
        self.resource = data[1] if len(data) > 1 else 0
        self.comm_mode_basic = data[2] if len(data) > 2 else 0
        # COMM_MODE_BASIC bit0: 0 = little-endian (Intel), 1 = big-endian (Motorola)
        self.byte_order = "big" if (self.comm_mode_basic & 0x01) else "little"
        self.max_cto = data[3] if len(data) > 3 else 8
        if len(data) >= 6:
            self.max_dto = self._word(data[4:6])
        self.connected = True
        return {
            "resource": self.resource,
            "comm_mode_basic": self.comm_mode_basic,
            "byte_order": self.byte_order,
            "max_cto": self.max_cto,
            "max_dto": self.max_dto,
            "protocol_version": data[6] if len(data) > 6 else None,
            "transport_version": data[7] if len(data) > 7 else None,
        }

    def disconnect(self) -> None:
        """DISCONNECT (0xFE). Best-effort; always clears the connected flag."""
        try:
            if self.connected:
                self._command(bytes([CMD_DISCONNECT]))
        finally:
            self.connected = False

    def get_status(self) -> dict:
        """
        GET_STATUS (0xFD). Returns the current session status, resource
        protection status and session configuration id.
        """
        data = self._command(bytes([CMD_GET_STATUS]))
        return {
            "session_status": data[1] if len(data) > 1 else 0,
            "protection_status": data[2] if len(data) > 2 else 0,
            "session_config_id": self._word(data[4:6]) if len(data) >= 6 else 0,
        }

    def get_comm_mode_info(self) -> dict:
        """
        GET_COMM_MODE_INFO (0xFB). Returns optional communication-mode details
        (block-mode support, max block size, min separation time, queue size).
        """
        data = self._command(bytes([CMD_GET_COMM_MODE_INFO]))
        return {
            "comm_mode_optional": data[2] if len(data) > 2 else 0,
            "max_bs": data[4] if len(data) > 4 else 0,
            "min_st": data[5] if len(data) > 5 else 0,
            "queue_size": data[6] if len(data) > 6 else 0,
            "xcp_driver_version": data[7] if len(data) > 7 else 0,
        }

    # ----------------------------------------------------------- memory reads
    def set_mta(self, address: int, ext: int = 0) -> None:
        """SET_MTA (0xF6): set the Memory Transfer Address for a later UPLOAD."""
        payload = bytes([CMD_SET_MTA, 0x00, 0x00, ext & 0xFF]) + self._addr_bytes(address)
        self._command(payload)

    def upload(self, length: int) -> bytes:
        """
        UPLOAD (0xF5): read ``length`` elements from the current MTA. The MTA is
        auto-incremented by the slave. ``length`` must fit one CTO
        (``<= max_cto - 1``); use :meth:`read_memory` for larger blocks.
        """
        data = self._command(bytes([CMD_UPLOAD, length & 0xFF]))
        return data[1:1 + length]

    def short_upload(self, address: int, length: int, ext: int = 0) -> bytes:
        """
        SHORT_UPLOAD (0xF4): read ``length`` elements from ``address`` in one
        round trip (sets the MTA implicitly). ``length`` must fit one CTO.
        """
        payload = bytes([CMD_SHORT_UPLOAD, length & 0xFF, 0x00, ext & 0xFF]) \
            + self._addr_bytes(address)
        data = self._command(payload)
        return data[1:1 + length]

    def read_memory(self, address: int, length: int, ext: int = 0,
                    use_short_upload: bool = True) -> bytes:
        """
        Read ``length`` bytes starting at ``address``, transparently chunking to
        respect MAX_CTO. By default uses SHORT_UPLOAD per chunk; set
        ``use_short_upload=False`` to use SET_MTA once followed by UPLOAD chunks.

        Returns the raw bytes exactly as sent by the slave (no byte-swapping).
        """
        if length <= 0:
            return b""
        # One positive-response frame carries at most (MAX_CTO - 1) data bytes,
        # since byte 0 is the 0xFF response id.
        chunk_max = max(1, self.max_cto - 1)
        out = bytearray()

        if not use_short_upload:
            self.set_mta(address, ext=ext)
            remaining = length
            while remaining > 0:
                n = min(chunk_max, remaining)
                out += self.upload(n)
                remaining -= n
            return bytes(out[:length])

        offset = 0
        while offset < length:
            n = min(chunk_max, length - offset)
            out += self.short_upload(address + offset, n, ext=ext)
            offset += n
        return bytes(out[:length])

    def poll_measurement(self, address: int, length: int, ext: int = 0,
                         signed: bool = False) -> int:
        """
        Read ``length`` bytes at ``address`` and decode them as a single integer
        using the negotiated byte order. Returns the raw integer value (no scaling).
        """
        raw = self.read_memory(address, length, ext=ext)
        return int.from_bytes(raw, self.byte_order, signed=signed)

    # --------------------------------------------------------- DAQ (optional)
    # Minimal DAQ helpers. DAQ (Data AcQuisition) lets the slave push periodic
    # samples on its own instead of the tester polling with SHORT_UPLOAD. Full
    # DAQ configuration (ODT layout, event channels, timestamp handling) is
    # involved and slave-specific; polling via read_memory / poll_measurement is
    # the primary, well-tested path. These wrappers exist for callers that know
    # their slave's DAQ layout. Reading the DTO frames the slave then emits is
    # left to the caller / worker.
    def free_daq(self) -> None:
        """FREE_DAQ (0xD6): clear all dynamic DAQ list configuration."""
        self._command(bytes([CMD_FREE_DAQ]))

    def alloc_daq(self, count: int) -> None:
        """ALLOC_DAQ (0xD5): allocate ``count`` dynamic DAQ lists."""
        self._command(bytes([CMD_ALLOC_DAQ, 0x00]) + int(count).to_bytes(2, self.byte_order))

    def set_daq_ptr(self, daq_list: int, odt: int, odt_entry: int) -> None:
        """SET_DAQ_PTR (0xE2): point at a DAQ list / ODT / ODT entry to write."""
        payload = (bytes([CMD_SET_DAQ_PTR, 0x00])
                   + int(daq_list).to_bytes(2, self.byte_order)
                   + bytes([odt & 0xFF, odt_entry & 0xFF]))
        self._command(payload)

    def write_daq(self, address: int, size: int, ext: int = 0,
                  bit_offset: int = 0xFF) -> None:
        """WRITE_DAQ (0xE1): add one element (address/size) to the pointed ODT."""
        payload = bytes([CMD_WRITE_DAQ, bit_offset & 0xFF, size & 0xFF, ext & 0xFF]) \
            + self._addr_bytes(address)
        self._command(payload)

    def start_stop_daq_list(self, mode: int, daq_list: int) -> bytes:
        """START_STOP_DAQ_LIST (0xDE): start/stop/select a single DAQ list."""
        payload = bytes([CMD_START_STOP_DAQ_LIST, mode & 0xFF]) \
            + int(daq_list).to_bytes(2, self.byte_order)
        return self._command(payload)


# ---------------------------------------------------------------------------
# Qt worker
# ---------------------------------------------------------------------------
# PyQt6 is imported lazily so the pure XCPClient above (and the whole module) can
# be imported and unit-tested with neither python-can nor PyQt6 present, matching
# the "import inside" convention of core.isotp. When PyQt6 is unavailable the
# QThread base collapses to object and XCPPollWorker simply can't be started.
try:  # pragma: no cover - trivial import guard
    from PyQt6.QtCore import QThread, pyqtSignal
    _HAVE_QT = True
except Exception:  # pragma: no cover
    _HAVE_QT = False

    class QThread:  # type: ignore
        def __init__(self, *a, **k):
            raise RuntimeError("PyQt6 is required to use XCPPollWorker")

    def pyqtSignal(*a, **k):  # type: ignore
        return None


class XCPPollWorker(QThread):
    """
    Periodically read a fixed set of XCP measurements and emit each sweep.

    Modeled on ``core.obd2_poller.OBD2Poller`` / ``core.uds.UDSScanner``:
    it owns an :class:`XCPClient`, CONNECTs once, then loops reading every
    measurement via ``poll_measurement`` (repeated SHORT_UPLOAD — the primary,
    hardware-agnostic polling path) and emits ``sample`` with a
    ``{name: raw_int}`` dict per sweep.

    Parameters
    ----------
    bus          : python-can Bus
    cro_id/dto_id: XCP command / data arbitration ids
    measurements : list of (name, address, size) tuples
    interval_ms  : delay between sweeps
    timeout      : per-command timeout passed to XCPClient
    """

    sample   = pyqtSignal(dict)   # {name: raw_int}
    status   = pyqtSignal(str)
    error    = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, bus, cro_id: int, dto_id: int,
                 measurements: List[Tuple[str, int, int]],
                 interval_ms: int = 200, timeout: float = 1.0, parent=None):
        super().__init__(parent)
        self._bus = bus
        self._cro_id = cro_id
        self._dto_id = dto_id
        self._measurements = list(measurements)
        self._interval = max(20, interval_ms) / 1000.0
        self._timeout = timeout
        self._running = True

    def stop(self):
        self._running = False
        self.quit()
        self.wait(2000)

    def run(self):
        client = XCPClient(self._bus, self._cro_id, self._dto_id,
                           timeout=self._timeout)
        try:
            info = client.connect()
            self.status.emit(
                f"XCP connected (byte_order={info['byte_order']}, "
                f"MAX_CTO={info['max_cto']})")
        except Exception as e:
            self.error.emit(f"XCP connect failed: {e}")
            self.finished.emit()
            return

        try:
            while self._running:
                sweep: Dict[str, int] = {}
                for name, address, size in self._measurements:
                    if not self._running:
                        break
                    try:
                        sweep[name] = client.poll_measurement(address, size)
                    except Exception as e:
                        self.error.emit(f"{name} @ 0x{address:X}: {e}")
                if sweep:
                    self.sample.emit(sweep)
                time.sleep(self._interval)
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
            self.finished.emit()
