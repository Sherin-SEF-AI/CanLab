"""
ISO-TP (ISO 15765-2) reassembly — single-channel, blocking receive.

Implements:
  Single Frame  (PCI N_PCI = 0x0x): payload = data[1:1+length]
  First Frame   (PCI N_PCI = 0x1x): start multi-frame; send Flow Control
  Consecutive   (PCI N_PCI = 0x2x): collect in order until length reached
  Flow Control  (PCI N_PCI = 0x3x): sent by us after FF received

The payload contract is *unframed*: callers pass the service bytes only
(``[0x22, 0xF1, 0x90]``), never the PCI/length byte, and get the response
service bytes back the same way. Getting this wrong is silent — the frame still
goes out, just malformed — so the tests pin the exact bytes on the wire.

Usage:
    session = ISOTPSession(bus, tx_id=0x7E0, rx_id=0x7E8)
    payload = session.request(bytes([0x22, 0xF1, 0x90]))    # handles NRC 0x78
    if payload:
        ...
"""
import time
from typing import Optional
import logging

from canlab.core.safety import gated_send

log = logging.getLogger(__name__)

# Flow Control constants
FC_CTS   = 0x30   # Continue To Send
FC_WAIT  = 0x31
FC_OVFLW = 0x32

BLOCK_SIZE = 0        # 0 = no block limit
ST_MIN     = 0        # 0 ms separation time (fastest)

NRC_RESPONSE_PENDING = 0x78   # requestCorrectlyReceivedResponsePending
P2_STAR_DEFAULT      = 5.0    # seconds to wait after a 0x78, per ISO 14229
MAX_PENDING          = 20     # give up rather than wait forever

# CAN FD payload lengths a frame can actually be padded to.
_FD_LENGTHS = (8, 12, 16, 20, 24, 32, 48, 64)


def is_response_pending(payload) -> bool:
    """True for a UDS ``7F <service> 78`` response-pending reply."""
    return (payload is not None and len(payload) >= 3
            and payload[0] == 0x7F and payload[2] == NRC_RESPONSE_PENDING)


def _pad(frame: bytes, tx_dl: int = 8) -> bytes:
    """Pad a frame up to the next legal CAN(-FD) length."""
    if len(frame) <= 8:
        return frame + bytes(8 - len(frame))
    for n in _FD_LENGTHS:
        if len(frame) <= n <= tx_dl:
            return frame + bytes(n - len(frame))
    return frame[:tx_dl]


class ISOTPSession:
    """
    Blocking ISO-TP send/receive for a single request-response pair.
    Mimics the python-can recv() API (returns None on timeout).
    """

    def __init__(self, bus, tx_id: int, rx_id, tx_dl: int = 8):
        self._bus   = bus
        self._tx_id = tx_id
        # rx_id may be one id or several (functional addressing answers from any
        # of 0x7E8-0x7EF).
        self._rx_ids = ({int(rx_id)} if isinstance(rx_id, int)
                        else {int(r) for r in rx_id})
        self._rx_id = next(iter(self._rx_ids)) if len(self._rx_ids) == 1 else None
        self._tx_dl = int(tx_dl)
        self.last_rx_id = None

    def _rx_match(self, arb_id: int) -> bool:
        if arb_id in self._rx_ids:
            self.last_rx_id = arb_id
            return True
        return False

    def _fc_tx_id(self) -> int:
        """Flow control goes back to the ECU that started the transfer."""
        if self._rx_id is not None or self.last_rx_id is None:
            return self._tx_id
        return self.last_rx_id - 0x08

    def send(self, data: bytes, timeout: float = 1.0) -> Optional[bytes]:
        """
        Send `data` (UDS request bytes) and return the fully assembled response,
        or None on timeout / error.
        """
        import can
        n = len(data)

        if n <= 7:
            # Single Frame
            frame = _pad(bytes([n & 0x0F]) + data, self._tx_dl)
            try:
                gated_send(self._bus, can.Message(arbitration_id=self._tx_id,
                                                  data=frame, is_extended_id=False))
            except Exception:
                return None
        elif self._tx_dl > 8 and n <= self._tx_dl - 2:
            # CAN FD escape single frame: PCI 0x00 then a full length byte.
            frame = _pad(bytes([0x00, n]) + data, self._tx_dl)
            try:
                gated_send(self._bus, can.Message(arbitration_id=self._tx_id,
                                                  data=frame, is_extended_id=False))
            except Exception:
                return None
        else:
            # First Frame + Flow-Control handshake + Consecutive Frames.
            # (Previously only the FF was sent, silently truncating every
            #  request longer than 7 bytes.)
            hi = (n >> 8) & 0x0F
            lo = n & 0xFF
            ff = bytes([0x10 | hi, lo]) + data[:6]
            try:
                gated_send(self._bus, can.Message(arbitration_id=self._tx_id,
                                                  data=ff, is_extended_id=False))
            except Exception:
                return None
            if not self._send_consecutive_frames(data, timeout):
                return None

        # Response length is inferred from the ECU's own SF/FF, never from the
        # request length.
        return self._receive(None, timeout)

    def _send_consecutive_frames(self, data: bytes, timeout: float) -> bool:
        """Transmit CFs for a multi-frame request, honouring the ECU's FC."""
        import can
        deadline = time.monotonic() + timeout
        fc = self._wait_for_fc(deadline)
        if fc is None:
            return False
        flow_status, block_size, st_min = fc

        idx = 6            # next unsent data offset (6 went in the FF)
        sn  = 1            # consecutive-frame sequence number
        sent_in_block = 0
        st = self._stmin_seconds(st_min)
        # STmin is the gap between two consecutive frames, and a flow control
        # arriving between them does not reset it. Sleeping only inside a
        # block sent the first frame after each flow control 0.1 ms after the
        # previous one, which can-isotp's timestamps showed and an ECU with a
        # slow receive buffer would drop.
        last_cf = None

        while idx < len(data):
            if flow_status == 0x2:      # OVFLW — abort
                return False
            if flow_status == 0x1:      # WAIT — re-wait for a fresh FC
                fc = self._wait_for_fc(deadline)
                if fc is None:
                    return False
                flow_status, block_size, st_min = fc
                st = self._stmin_seconds(st_min)
                sent_in_block = 0
                continue

            chunk = data[idx:idx + 7]
            cf = bytes([0x20 | (sn & 0x0F)]) + chunk + bytes(7 - len(chunk))
            if last_cf is not None and st > 0:
                wait = st - (time.monotonic() - last_cf)
                if wait > 0:
                    time.sleep(wait)
            try:
                gated_send(self._bus, can.Message(arbitration_id=self._tx_id,
                                                  data=cf, is_extended_id=False))
            except Exception:
                return False
            last_cf = time.monotonic()
            idx += 7
            sn = (sn + 1) & 0x0F
            sent_in_block += 1

            if idx >= len(data):
                break
            if block_size and sent_in_block >= block_size:
                fc = self._wait_for_fc(deadline)
                if fc is None:
                    return False
                flow_status, block_size, st_min = fc
                st = self._stmin_seconds(st_min)
                sent_in_block = 0
        return True

    def request(self, data: bytes, timeout: float = 1.0,
                p2_star: float = P2_STAR_DEFAULT):
        """Send a UDS request and return the final response payload.

        A ``7F xx 78`` (response pending) reply is not an answer — the ECU is
        asking for more time — so keep waiting for the real one instead of
        reporting the NRC to the caller.
        """
        resp = self.send(data, timeout=timeout)
        pending = 0
        while is_response_pending(resp) and pending < MAX_PENDING:
            pending += 1
            resp = self.receive(p2_star)
        return resp

    def receive(self, timeout: float = 1.0):
        """Wait for one assembled response without sending anything."""
        return self._receive(None, timeout)

    def _wait_for_fc(self, deadline: float):
        """Block until a Flow Control frame arrives; return (fs, bs, stmin)."""
        while time.monotonic() < deadline:
            resp = self._bus.recv(timeout=0.05)
            if resp is None or not self._rx_match(resp.arbitration_id):
                continue
            raw = bytes(resp.data)
            if raw and (raw[0] >> 4) & 0x0F == 0x3:
                fs     = raw[0] & 0x0F
                bs     = raw[1] if len(raw) > 1 else 0
                st_min = raw[2] if len(raw) > 2 else 0
                return fs, bs, st_min
        return None

    @staticmethod
    def _stmin_seconds(st_min: int) -> float:
        """Decode an ISO-TP STmin byte to seconds."""
        if st_min <= 0x7F:
            return st_min / 1000.0            # 0-127 ms
        if 0xF1 <= st_min <= 0xF9:
            return (st_min - 0xF0) / 10000.0  # 100-900 microseconds
        return 0.127                          # reserved: treat as the 127 ms max

    def _receive(self, expected_len: Optional[int], timeout: float,
                 passive: bool = False) -> Optional[bytes]:
        """
        Collect response frames. Handles SF, FF+CFs.
        If expected_len is None we infer from the SF/FF length byte.
        When passive=True (sniffing), no Flow Control is transmitted.
        """
        deadline  = time.monotonic() + timeout
        payload   = bytearray()
        total_len = expected_len  # None until we parse SF/FF
        cf_index  = 1             # expected consecutive frame SN

        while time.monotonic() < deadline:
            resp = self._bus.recv(timeout=0.05)
            if resp is None or not self._rx_match(resp.arbitration_id):
                continue

            raw = bytes(resp.data)
            if not raw:
                continue
            # A valid frame arrived — extend the deadline so a legitimately slow
            # multi-frame transfer doesn't time out mid-stream.
            deadline = time.monotonic() + timeout
            pci  = (raw[0] >> 4) & 0x0F

            if pci == 0x0:  # Single Frame
                length = raw[0] & 0x0F
                if length == 0 and len(raw) > 1:
                    # CAN FD escape single frame: the real length follows.
                    return bytes(raw[2:2 + raw[1]])
                return bytes(raw[1:1 + length])

            if pci == 0x1:  # First Frame
                length = ((raw[0] & 0x0F) << 8) | raw[1]
                total_len = length
                payload   = bytearray(raw[2:])  # first 6 payload bytes
                if not passive:
                    # Send Flow Control — CTS, BS=0, STmin=0
                    self._send_fc()
                cf_index = 1
                continue

            if pci == 0x2:  # Consecutive Frame
                sn = raw[0] & 0x0F
                if sn != (cf_index & 0x0F):
                    return None  # sequence error
                payload   += bytearray(raw[1:])
                cf_index  = (cf_index + 1) & 0x0F
                if total_len is not None and len(payload) >= total_len:
                    return bytes(payload[:total_len])
                continue

        return None  # timeout

    def _send_fc(self):
        """Send a Flow Control CTS frame."""
        import can
        fc = bytes([FC_CTS, BLOCK_SIZE, ST_MIN, 0, 0, 0, 0, 0])
        try:
            msg = can.Message(
                arbitration_id=self._fc_tx_id(),
                data=fc,
                is_extended_id=False,
            )
            gated_send(self._bus, msg)
        except Exception:
            log.warning("suppressed exception", exc_info=True)
