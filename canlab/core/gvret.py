"""GVRET: the serial protocol SavvyCAN's own hardware speaks.

GVRET firmware runs on the boards people actually buy for this work: the
Macchina M2 and A0, the EVTV CANDue, and the ESP32RET builds that expose the
same protocol over WiFi on TCP port 23. python-can has no backend for it, so
those adapters could not be used here at all.

The wire format, from SavvyCAN's connections/gvretserial.cpp:

    host to device
        E7 E7                            leave text mode, talk binary
        F1 05 <can0 4 LE> <can1 4 LE>    set the buses up
        F1 00 <id 4 LE> <bus> <len> <data...> 00      send a frame
    device to host
        F1 00 <ts 4 LE us> <id 4 LE> <len|bus<<4> <data...>    a frame arrived
        F1 <other> <fixed length reply>  answers to the handshake

The baud word carries flags above the speed: bit 31 configured, bit 30
enabled, bit 29 listen only. Bit 31 of an arbitration ID means extended, in
both directions.

``GvretCodec`` is the whole protocol and touches no I/O, so it is tested
against byte strings rather than hardware. ``GvretBus`` wraps it in a
python-can Bus and is registered under the interface name "gvret".
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

SOF = 0xF1
BINARY_MODE = b"\xE7\xE7"

CMD_FRAME = 0x00
CMD_TIME_SYNC = 0x01
CMD_DIG_INPUTS = 0x02
CMD_ANALOG_INPUTS = 0x03
CMD_SET_DIG_OUTPUTS = 0x04
CMD_SETUP_CANBUS = 0x05
CMD_GET_CANBUS_PARAMS = 0x06
CMD_GET_DEVICE_INFO = 0x07
CMD_VALIDATE = 0x09
CMD_GET_NUM_BUSES = 0x0C
CMD_GET_EXT_BUSES = 0x0D
CMD_FD_FRAME = 0x14
CMD_GET_FD_SETTINGS = 0x16

# Bytes that follow the command byte for every fixed-length device reply.
# Getting one of these wrong desynchronises the stream, so they are taken
# from the reference implementation's state machine rather than guessed.
REPLY_LENGTHS = {
    CMD_TIME_SYNC: 4,
    CMD_DIG_INPUTS: 2,
    CMD_ANALOG_INPUTS: 9,
    CMD_SET_DIG_OUTPUTS: 0,
    CMD_SETUP_CANBUS: 0,
    CMD_GET_CANBUS_PARAMS: 10,
    CMD_GET_DEVICE_INFO: 6,
    CMD_VALIDATE: 0,
    CMD_GET_NUM_BUSES: 1,
    CMD_GET_EXT_BUSES: 15,
    CMD_GET_FD_SETTINGS: 15,
}

BAUD_CONFIGURED = 0x80000000
BAUD_ENABLED = 0x40000000
BAUD_LISTEN_ONLY = 0x20000000
EXTENDED_FLAG = 0x80000000


def baud_word(bitrate: int, *, enabled: bool = True, listen_only: bool = False) -> int:
    word = int(bitrate) & 0x0FFFFFFF
    word |= BAUD_CONFIGURED
    if enabled:
        word |= BAUD_ENABLED
    if listen_only:
        word |= BAUD_LISTEN_ONLY
    return word


def encode_setup(bitrate0: int, bitrate1: int | None = None, *,
                 listen_only: bool = False, enable1: bool = False) -> bytes:
    """F1 05: bring the buses up. The second bus stays configured but off
    unless asked for, so a two-channel board does not start echoing traffic
    from a channel nobody selected."""
    w0 = baud_word(bitrate0, enabled=True, listen_only=listen_only)
    w1 = baud_word(bitrate1 or bitrate0, enabled=enable1, listen_only=listen_only)
    return bytes([SOF, CMD_SETUP_CANBUS]) + w0.to_bytes(4, "little") + w1.to_bytes(4, "little")


def encode_handshake() -> bytes:
    """What SavvyCAN sends on connect: binary mode, then ask the board what it
    is. The replies are all fixed length, so the parser stays in step."""
    return (BINARY_MODE
            + bytes([SOF, CMD_GET_NUM_BUSES])
            + bytes([SOF, CMD_GET_CANBUS_PARAMS])
            + bytes([SOF, CMD_GET_DEVICE_INFO])
            + bytes([SOF, CMD_VALIDATE]))


def encode_frame(arb_id: int, data: bytes, *, extended: bool = False,
                 bus: int = 0) -> bytes:
    """F1 00: transmit. Note the shape differs from the receive direction,
    which packs length and bus into one byte and has no trailing zero."""
    ident = int(arb_id) & 0x1FFFFFFF
    if extended:
        ident |= EXTENDED_FLAG
    payload = bytes(data)[:8]
    return (bytes([SOF, CMD_FRAME]) + ident.to_bytes(4, "little")
            + bytes([int(bus) & 3, len(payload)]) + payload + b"\x00")


class GvretFrame:
    __slots__ = ("timestamp", "arbitration_id", "is_extended_id", "data", "channel", "dlc")

    def __init__(self, timestamp, arbitration_id, is_extended_id, data, channel, dlc):
        self.timestamp = timestamp
        self.arbitration_id = arbitration_id
        self.is_extended_id = is_extended_id
        self.data = data
        self.channel = channel
        self.dlc = dlc

    def __repr__(self):
        return (f"GvretFrame(id=0x{self.arbitration_id:X}, ext={self.is_extended_id}, "
                f"bus={self.channel}, data={self.data.hex(' ').upper()})")


class GvretCodec:
    """Byte stream in, frames out. Survives partial reads and noise.

    The device timestamp is a microsecond counter on the board, not wall time,
    so ``feed`` returns it as ``device_us`` and the caller decides. GvretBus
    anchors the first frame to the host clock and advances from there, which
    keeps inter-frame gaps at the board's precision without the capture
    claiming to have happened in 1970.
    """

    def __init__(self):
        self.buf = bytearray()
        self.desyncs = 0
        self.device_info: dict = {}

    def feed(self, chunk: bytes) -> list[GvretFrame]:
        self.buf.extend(chunk)
        out: list[GvretFrame] = []
        while True:
            frame, consumed, wait = self._parse_one()
            if wait:
                break
            del self.buf[:consumed]
            if frame is not None:
                out.append(frame)
        return out

    def _parse_one(self):
        """(frame or None, bytes consumed, need more data)."""
        buf = self.buf
        if not buf:
            return None, 0, True
        if buf[0] != SOF:
            # Not at a command boundary. Skip to the next plausible start
            # rather than throwing the whole buffer away.
            nxt = buf.find(SOF, 1)
            self.desyncs += 1
            if nxt < 0:
                return None, len(buf), False
            return None, nxt, False
        if len(buf) < 2:
            return None, 0, True
        cmd = buf[1]
        if cmd == CMD_FRAME:
            return self._parse_frame(buf, 9, fd=False)
        if cmd == CMD_FD_FRAME:
            return self._parse_frame(buf, 9, fd=True)
        length = REPLY_LENGTHS.get(cmd)
        if length is None:
            # An unknown command byte means we are not really at a boundary.
            self.desyncs += 1
            return None, 1, False
        if len(buf) < 2 + length:
            return None, 0, True
        if cmd == CMD_GET_DEVICE_INFO:
            self.device_info.update(build=int.from_bytes(buf[2:4], "little"),
                                    single_wire=buf[7])
        elif cmd == CMD_GET_NUM_BUSES:
            self.device_info["buses"] = buf[2]
        return None, 2 + length, False

    def _parse_frame(self, buf, header: int, *, fd: bool):
        if len(buf) < 2 + header:
            return None, 0, True
        body = buf[2:]
        device_us = int.from_bytes(body[0:4], "little")
        ident = int.from_bytes(body[4:8], "little")
        extended = bool(ident & EXTENDED_FLAG)
        ident &= 0x1FFFFFFF
        lenbus = body[8]
        # Classic frames pack length and bus into one byte; FD carries a full
        # length because 64 does not fit in a nibble.
        if fd:
            length, bus = lenbus, 0
        else:
            length, bus = lenbus & 0x0F, (lenbus & 0xF0) >> 4
        if len(body) < 9 + length:
            return None, 0, True
        data = bytes(body[9:9 + length])
        frame = GvretFrame(device_us / 1e6, ident, extended, data, bus, length)
        return frame, 2 + 9 + length, False


def _open_stream(channel: str, *, tty_baudrate: int = 1_000_000, timeout: float = 0.05):
    """A serial port, or a TCP socket when the channel looks like host:port.

    ESP32RET boards serve the same protocol over WiFi on port 23, which is how
    most people use an A0.
    """
    text = str(channel).strip()
    if text.startswith("tcp://"):
        text = text[len("tcp://"):]
    looks_tcp = ("." in text.split(":")[0] or text.lower().startswith("localhost")) \
        and ":" in text
    if looks_tcp:
        import socket
        host, _, port = text.rpartition(":")
        sock = socket.create_connection((host, int(port or 23)), timeout=5.0)
        sock.settimeout(timeout)
        return _SocketStream(sock)
    try:
        import serial
    except ImportError as e:
        raise ImportError("the GVRET backend needs pyserial: "
                          "pip install pyserial") from e
    return serial.Serial(text, baudrate=tty_baudrate, timeout=timeout)


class _SocketStream:
    """The few serial methods the bus uses, over a socket."""

    def __init__(self, sock):
        self._sock = sock

    def read(self, n: int = 1) -> bytes:
        import socket
        try:
            return self._sock.recv(max(1, n))
        except (socket.timeout, TimeoutError):
            return b""
        except OSError:
            return b""

    def write(self, data: bytes) -> int:
        return self._sock.sendall(data) or len(data)

    @property
    def in_waiting(self) -> int:
        return 0

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def _bus_base():
    from can import BusABC
    return BusABC


def _make_bus_class():
    from can import BusABC, Message

    class GvretBus(BusABC):
        """A GVRET board as a python-can bus."""

        def __init__(self, channel, bitrate: int = 500_000, *, bus_index: int = 0,
                     tty_baudrate: int = 1_000_000, listen_only: bool = False,
                     second_bitrate: int | None = None, enable_second: bool = False,
                     can_filters=None, **kwargs):
            self.channel_info = f"GVRET {channel} @ {bitrate} bit/s (bus {bus_index})"
            self._codec = GvretCodec()
            self._bus_index = int(bus_index)
            self._host_epoch = None
            self._device_epoch = None
            self._stream = _open_stream(channel, tty_baudrate=tty_baudrate)
            self._stream.write(encode_handshake())
            self._stream.write(encode_setup(bitrate, second_bitrate,
                                            listen_only=listen_only,
                                            enable1=enable_second))
            self._listen_only = listen_only
            self._pending: list = []
            super().__init__(channel=channel, can_filters=can_filters, **kwargs)

        # -- receive ------------------------------------------------------------
        def _recv_internal(self, timeout):
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                if self._pending:
                    return self._pending.pop(0), False
                chunk = self._stream.read(4096) or b""
                if chunk:
                    for f in self._codec.feed(chunk):
                        if f.channel != self._bus_index and not self._single_bus():
                            continue
                        self._pending.append(self._to_message(f))
                    if self._pending:
                        continue
                if deadline is not None and time.monotonic() >= deadline:
                    return None, False
                if not chunk:
                    time.sleep(0.001)

        def _single_bus(self) -> bool:
            return self._codec.device_info.get("buses", 2) <= 1

        def _to_message(self, f: GvretFrame):
            # The board counts microseconds since it booted. Anchor the first
            # frame to now and keep the board's spacing after that.
            if self._device_epoch is None:
                self._device_epoch = f.timestamp
                self._host_epoch = time.time()
            ts = self._host_epoch + (f.timestamp - self._device_epoch)
            return Message(timestamp=ts, arbitration_id=f.arbitration_id,
                           is_extended_id=f.is_extended_id, data=f.data,
                           dlc=f.dlc, channel=f.channel, is_rx=True)

        # -- transmit -----------------------------------------------------------
        def send(self, msg, timeout=None) -> None:
            if self._listen_only:
                raise RuntimeError("this GVRET bus was opened listen-only")
            self._stream.write(encode_frame(
                msg.arbitration_id, bytes(msg.data or b""),
                extended=bool(msg.is_extended_id),
                bus=self._bus_index))

        def shutdown(self) -> None:
            try:
                super().shutdown()
            except Exception:
                log.debug("BusABC shutdown complained", exc_info=True)
            try:
                self._stream.close()
            except Exception:
                log.debug("closing the GVRET stream failed", exc_info=True)

    return GvretBus


_BUS_CLASS = None


def GvretBus(*args, **kwargs):           # noqa: N802 - it is a class in all but name
    """Build the bus class on first use, so importing this module never
    requires python-can to be present."""
    global _BUS_CLASS
    if _BUS_CLASS is None:
        _BUS_CLASS = _make_bus_class()
    return _BUS_CLASS(*args, **kwargs)


def register() -> bool:
    """Teach python-can the "gvret" interface name.

    An entry point in pyproject.toml does the same for anything else in the
    environment; this makes it work in a checkout that has not been
    reinstalled.
    """
    try:
        import can.interfaces as interfaces
        import can.util as util
    except ImportError:
        return False
    interfaces.BACKENDS.setdefault("gvret", (__name__, "_BusEntry"))
    # VALID_INTERFACES is a frozenset built at import, and can.util holds its
    # own reference, so both have to be replaced or Bus() rejects the name.
    valid = frozenset(interfaces.BACKENDS)
    interfaces.VALID_INTERFACES = valid
    util.VALID_INTERFACES = valid
    try:
        import can
        can.VALID_INTERFACES = valid
    except ImportError:                              # pragma: no cover
        pass
    return True


class _BusEntry:
    """python-can imports the module and calls the named attribute; this
    forwards to the lazily built class."""

    def __new__(cls, *args, **kwargs):
        return GvretBus(*args, **kwargs)
