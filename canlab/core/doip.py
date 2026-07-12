"""
DoIP (Diagnostics over IP, ISO 13400-2) client — standard library only.

Wraps UDS payloads for Ethernet-based vehicles so CanLab's diagnostic tooling
can reach an ECU over TCP instead of a CAN bus.  Uses ``socket`` exclusively:
TCP for the diagnostic session, UDP broadcast for vehicle discovery.

Wire format (every message)::

    +---------+-----------------+--------------+----------------+---------+
    | version | inverse version | payload type | payload length | payload |
    | 1 byte  | 1 byte          | 2 bytes BE   | 4 bytes BE     | N bytes |
    +---------+-----------------+--------------+----------------+---------+

Typical use::

    from core.doip import DoIPClient, discover

    for entity in discover(timeout=2.0):
        print(entity["ip"], entity["vin"], hex(entity["logical_address"]))

    client = DoIPClient("192.168.1.10", target_address=0x1234)
    client.connect()                       # TCP + routing activation
    resp = client.request(bytes([0x22, 0xF1, 0x90]))   # UDS ReadDataByID
    client.close()

The encode_/decode_ helpers are pure functions and can be unit-tested without
any network access.
"""
from __future__ import annotations

import socket
import struct
from typing import NamedTuple, Optional

# --------------------------------------------------------------------------- #
# Protocol constants
# --------------------------------------------------------------------------- #
PROTOCOL_VERSION = 0x02          # ISO 13400-2:2012
INVERSE_VERSION = 0xFF ^ PROTOCOL_VERSION  # 0xFD
HEADER_SIZE = 8
DEFAULT_PORT = 13400
DEFAULT_SOURCE_ADDRESS = 0x0E00  # common tester range (0x0E00-0x0EFF)

# Payload types (ISO 13400-2)
PT_VEHICLE_ID_REQUEST = 0x0001
PT_VEHICLE_ID_REQUEST_EID = 0x0002
PT_VEHICLE_ID_REQUEST_VIN = 0x0003
PT_VEHICLE_ANNOUNCEMENT = 0x0004   # also Vehicle Identification Response
PT_ROUTING_ACTIVATION_REQUEST = 0x0005
PT_ROUTING_ACTIVATION_RESPONSE = 0x0006
PT_ALIVE_CHECK_REQUEST = 0x0007
PT_ALIVE_CHECK_RESPONSE = 0x0008
PT_DIAGNOSTIC_MESSAGE = 0x8001
PT_DIAGNOSTIC_MESSAGE_ACK = 0x8002    # positive ACK
PT_DIAGNOSTIC_MESSAGE_NACK = 0x8003   # negative ACK

# Routing activation response codes (payload type 0x0006)
ROUTING_ACTIVATION_SUCCESS = 0x10
ROUTING_ACTIVATION_CODES = {
    0x00: "Routing activation denied — unknown source address",
    0x01: "Routing activation denied — all concurrent sockets registered/active",
    0x02: "Routing activation denied — source address different from activated",
    0x03: "Routing activation denied — source address already active on another socket",
    0x04: "Routing activation denied — missing authentication",
    0x05: "Routing activation denied — rejected confirmation",
    0x06: "Routing activation denied — unsupported routing activation type",
    0x10: "Routing successfully activated",
    0x11: "Routing will be activated — confirmation required",
}

# Diagnostic message positive ACK codes (payload type 0x8002)
DIAG_ACK_POSITIVE = 0x00

# Diagnostic message negative ACK codes (payload type 0x8003)
DIAG_NACK_CODES = {
    0x00: "Invalid source address",
    0x01: "Unknown target address",
    0x02: "Diagnostic message too large",
    0x03: "Out of memory",
    0x04: "Target unreachable",
    0x05: "Unknown network",
    0x06: "Transport protocol error",
}


class DoIPError(Exception):
    """Raised on any DoIP protocol failure, with a decoded human message."""


# --------------------------------------------------------------------------- #
# Pure encode/decode helpers
# --------------------------------------------------------------------------- #
class DoIPHeader(NamedTuple):
    protocol_version: int
    payload_type: int
    payload_length: int


def encode_header(payload_type: int, payload: bytes = b"") -> bytes:
    """Build a full DoIP message: 8-byte header followed by ``payload``."""
    return struct.pack(
        ">BBHI",
        PROTOCOL_VERSION,
        INVERSE_VERSION,
        payload_type & 0xFFFF,
        len(payload),
    ) + payload


def decode_header(data: bytes) -> DoIPHeader:
    """
    Parse the 8-byte DoIP header from ``data`` (payload may or may not follow).

    Returns (protocol_version, payload_type, payload_length).  Raises DoIPError
    if the buffer is too short or the version / inverse-version pair is invalid.
    """
    if len(data) < HEADER_SIZE:
        raise DoIPError(f"DoIP header too short: {len(data)} < {HEADER_SIZE} bytes")
    version, inverse, payload_type, payload_length = struct.unpack(
        ">BBHI", data[:HEADER_SIZE]
    )
    if inverse != (0xFF ^ version):
        raise DoIPError(
            f"DoIP header version mismatch: version=0x{version:02X} "
            f"inverse=0x{inverse:02X}"
        )
    return DoIPHeader(version, payload_type, payload_length)


def encode_vehicle_id_request() -> bytes:
    """Vehicle Identification Request (0x0001) — empty payload."""
    return encode_header(PT_VEHICLE_ID_REQUEST, b"")


def decode_vehicle_id_response(payload: bytes) -> dict:
    """
    Decode a Vehicle Announcement / Identification Response (0x0004) payload.

    Layout: VIN(17) + logical address(2) + EID(6) + GID(6) +
    further-action(1) [+ optional VIN/GID sync status(1)].
    """
    if len(payload) < 32:
        raise DoIPError(
            f"Vehicle announcement payload too short: {len(payload)} < 32 bytes"
        )
    vin_raw = payload[0:17]
    logical_address = struct.unpack(">H", payload[17:19])[0]
    eid = payload[19:25]
    gid = payload[25:31]
    further_action = payload[31]
    # VIN is ASCII; strip trailing padding (0x00 or 0xFF used when unavailable).
    vin = vin_raw.rstrip(b"\x00\xff").decode("ascii", errors="replace")
    return {
        "vin": vin,
        "logical_address": logical_address,
        "eid": eid,
        "gid": gid,
        "further_action_required": further_action,
    }


def encode_routing_activation_request(
    source_address: int,
    activation_type: int = 0x00,
    oem_specific: Optional[bytes] = None,
) -> bytes:
    """
    Routing Activation Request (0x0005).

    Payload: source address(2) + activation type(1) + reserved ISO(4, zero)
    [+ optional OEM-specific(4)].
    """
    payload = struct.pack(">H", source_address & 0xFFFF)
    payload += bytes([activation_type & 0xFF])
    payload += b"\x00\x00\x00\x00"          # reserved by ISO
    if oem_specific is not None:
        if len(oem_specific) != 4:
            raise DoIPError("oem_specific must be exactly 4 bytes")
        payload += oem_specific
    return encode_header(PT_ROUTING_ACTIVATION_REQUEST, payload)


class RoutingActivationResponse(NamedTuple):
    tester_address: int
    entity_address: int
    response_code: int


def decode_routing_activation_response(payload: bytes) -> RoutingActivationResponse:
    """
    Decode a Routing Activation Response (0x0006) payload.

    Layout: tester logical address(2) + entity logical address(2) +
    response code(1) + reserved(4) [+ optional OEM(4)].
    """
    if len(payload) < 9:
        raise DoIPError(
            f"Routing activation response too short: {len(payload)} < 9 bytes"
        )
    tester_address, entity_address = struct.unpack(">HH", payload[0:4])
    response_code = payload[4]
    return RoutingActivationResponse(tester_address, entity_address, response_code)


def encode_diagnostic_message(
    source_address: int, target_address: int, uds_payload: bytes
) -> bytes:
    """
    Diagnostic Message (0x8001) wrapping a UDS payload.

    Payload: source address(2) + target address(2) + UDS data(N).
    """
    payload = struct.pack(">HH", source_address & 0xFFFF, target_address & 0xFFFF)
    payload += bytes(uds_payload)
    return encode_header(PT_DIAGNOSTIC_MESSAGE, payload)


class DiagnosticMessage(NamedTuple):
    source_address: int
    target_address: int
    uds_payload: bytes


def decode_diagnostic_message(payload: bytes) -> DiagnosticMessage:
    """
    Decode a Diagnostic Message (0x8001) payload into
    (source_address, target_address, uds_payload).
    """
    if len(payload) < 4:
        raise DoIPError(
            f"Diagnostic message payload too short: {len(payload)} < 4 bytes"
        )
    source_address, target_address = struct.unpack(">HH", payload[0:4])
    return DiagnosticMessage(source_address, target_address, bytes(payload[4:]))


class DiagnosticAck(NamedTuple):
    source_address: int
    target_address: int
    code: int


def decode_diagnostic_ack(payload: bytes) -> DiagnosticAck:
    """
    Decode a Diagnostic Message ACK (0x8002) or NACK (0x8003) payload.

    Layout: source address(2) + target address(2) + ACK/NACK code(1)
    [+ optional echoed previous message].
    """
    if len(payload) < 5:
        raise DoIPError(
            f"Diagnostic ACK payload too short: {len(payload)} < 5 bytes"
        )
    source_address, target_address = struct.unpack(">HH", payload[0:4])
    code = payload[4]
    return DiagnosticAck(source_address, target_address, code)


# --------------------------------------------------------------------------- #
# Socket helpers
# --------------------------------------------------------------------------- #
def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes from a TCP socket or raise DoIPError."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout as exc:
            raise DoIPError("Timed out waiting for DoIP data") from exc
        if not chunk:
            raise DoIPError("DoIP connection closed by peer")
        buf += chunk
    return bytes(buf)


def _recv_message(sock: socket.socket) -> tuple[int, bytes]:
    """Read one complete DoIP message from a TCP socket -> (payload_type, payload)."""
    header = _recv_exactly(sock, HEADER_SIZE)
    parsed = decode_header(header)
    payload = _recv_exactly(sock, parsed.payload_length) if parsed.payload_length else b""
    return parsed.payload_type, payload


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class DoIPClient:
    """
    Blocking DoIP client for a single ECU over TCP.

    Parameters
    ----------
    host : str
        IP address / hostname of the DoIP entity (gateway).
    target_address : int
        Logical address of the target ECU (16-bit).
    source_address : int
        Our tester logical address (default 0x0E00).
    port : int
        TCP/UDP port (default 13400).
    timeout : float
        Socket timeout in seconds applied to all operations.
    """

    def __init__(
        self,
        host: str,
        target_address: int,
        source_address: int = DEFAULT_SOURCE_ADDRESS,
        port: int = DEFAULT_PORT,
        timeout: float = 2.0,
    ):
        self.host = host
        self.target_address = target_address
        self.source_address = source_address
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._activated = False

    # -- lifecycle ------------------------------------------------------- #
    def connect(self, activation_type: int = 0x00) -> None:
        """Open the TCP connection and perform routing activation."""
        if self._sock is not None:
            raise DoIPError("DoIPClient is already connected")
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise DoIPError(f"Failed to connect to {self.host}:{self.port}: {exc}") from exc
        sock.settimeout(self.timeout)
        self._sock = sock
        try:
            self._routing_activation(activation_type)
        except Exception:
            self.close()
            raise

    def _routing_activation(self, activation_type: int) -> None:
        assert self._sock is not None
        self._sock.sendall(
            encode_routing_activation_request(self.source_address, activation_type)
        )
        payload_type, payload = self._recv_message()
        if payload_type != PT_ROUTING_ACTIVATION_RESPONSE:
            raise DoIPError(
                f"Expected routing activation response (0x0006), "
                f"got 0x{payload_type:04X}"
            )
        resp = decode_routing_activation_response(payload)
        if resp.response_code != ROUTING_ACTIVATION_SUCCESS:
            msg = ROUTING_ACTIVATION_CODES.get(
                resp.response_code, "Unknown routing activation code"
            )
            raise DoIPError(
                f"Routing activation failed (0x{resp.response_code:02X}): {msg}"
            )
        self._activated = True

    # -- diagnostics ----------------------------------------------------- #
    def request(self, uds_bytes: bytes) -> bytes:
        """
        Send a UDS payload as a Diagnostic Message and return the UDS response.

        Waits for the positive Diagnostic Message ACK (0x8002) then the actual
        UDS response Diagnostic Message (0x8001).  Raises DoIPError on a NACK or
        a protocol/timeout error.
        """
        if self._sock is None or not self._activated:
            raise DoIPError("DoIPClient is not connected/activated; call connect() first")

        self._sock.sendall(
            encode_diagnostic_message(
                self.source_address, self.target_address, uds_bytes
            )
        )

        # 1) Acknowledgement (positive 0x8002 or negative 0x8003).
        payload_type, payload = self._recv_message()
        if payload_type == PT_DIAGNOSTIC_MESSAGE_NACK:
            ack = decode_diagnostic_ack(payload)
            msg = DIAG_NACK_CODES.get(ack.code, "Unknown diagnostic NACK code")
            raise DoIPError(f"Diagnostic message rejected (0x{ack.code:02X}): {msg}")
        if payload_type != PT_DIAGNOSTIC_MESSAGE_ACK:
            raise DoIPError(
                f"Expected diagnostic ACK (0x8002), got 0x{payload_type:04X}"
            )
        ack = decode_diagnostic_ack(payload)
        if ack.code != DIAG_ACK_POSITIVE:
            raise DoIPError(f"Diagnostic message not acknowledged (0x{ack.code:02X})")

        # 2) The UDS response, itself a Diagnostic Message (0x8001).
        payload_type, payload = self._recv_message()
        if payload_type != PT_DIAGNOSTIC_MESSAGE:
            raise DoIPError(
                f"Expected diagnostic message response (0x8001), "
                f"got 0x{payload_type:04X}"
            )
        return decode_diagnostic_message(payload).uds_payload

    def close(self) -> None:
        """Close the TCP connection (idempotent)."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._activated = False

    def __enter__(self) -> "DoIPClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- internal -------------------------------------------------------- #
    def _recv_message(self) -> tuple[int, bytes]:
        assert self._sock is not None
        return _recv_message(self._sock)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def discover(timeout: float = 2.0, port: int = DEFAULT_PORT,
             broadcast: str = "255.255.255.255") -> list[dict]:
    """
    UDP-broadcast a Vehicle Identification Request and collect announcements.

    Returns a list of dicts::

        [{"ip": "192.168.1.10", "vin": "W...", "logical_address": 0x1234,
          "eid": b"...", "gid": b"..."}]

    Blocks for up to ``timeout`` seconds gathering responses.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except OSError:
        pass
    sock.settimeout(timeout)

    results: list[dict] = []
    seen: set[tuple[str, int]] = set()
    try:
        sock.sendto(encode_vehicle_id_request(), (broadcast, port))
        import time
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                break
            except OSError:
                break
            try:
                header = decode_header(data)
            except DoIPError:
                continue
            if header.payload_type != PT_VEHICLE_ANNOUNCEMENT:
                continue
            payload = data[HEADER_SIZE:HEADER_SIZE + header.payload_length]
            try:
                info = decode_vehicle_id_response(payload)
            except DoIPError:
                continue
            key = (addr[0], info["logical_address"])
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "ip": addr[0],
                "vin": info["vin"],
                "logical_address": info["logical_address"],
                "eid": info["eid"],
                "gid": info["gid"],
            })
    finally:
        sock.close()
    return results
