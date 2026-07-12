"""
ISO-TP (ISO 15765-2) reassembly — single-channel, blocking receive.

Implements:
  Single Frame  (PCI N_PCI = 0x0x): payload = data[1:1+length]
  First Frame   (PCI N_PCI = 0x1x): start multi-frame; send Flow Control
  Consecutive   (PCI N_PCI = 0x2x): collect in order until length reached
  Flow Control  (PCI N_PCI = 0x3x): sent by us after FF received

Usage:
    session = ISOTPSession(bus, tx_id=0x7E0, rx_id=0x7E8)
    payload = session.send(uds_request_bytes, timeout=1.0)
    if payload:
        ...
"""
import time
from typing import Optional

# Flow Control constants
FC_CTS   = 0x30   # Continue To Send
FC_WAIT  = 0x31
FC_OVFLW = 0x32

BLOCK_SIZE = 0        # 0 = no block limit
ST_MIN     = 0        # 0 ms separation time (fastest)


class ISOTPSession:
    """
    Blocking ISO-TP send/receive for a single request-response pair.
    Mimics the python-can recv() API (returns None on timeout).
    """

    def __init__(self, bus, tx_id: int, rx_id: int):
        self._bus   = bus
        self._tx_id = tx_id
        self._rx_id = rx_id

    def send(self, data: bytes, timeout: float = 1.0) -> Optional[bytes]:
        """
        Send `data` (UDS request bytes) and return the fully assembled response,
        or None on timeout / error.
        """
        import can
        n = len(data)

        if n <= 7:
            # Single Frame
            frame = bytes([n & 0x0F]) + data + bytes(7 - n)
            try:
                self._bus.send(can.Message(arbitration_id=self._tx_id,
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
                self._bus.send(can.Message(arbitration_id=self._tx_id,
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
            try:
                self._bus.send(can.Message(arbitration_id=self._tx_id,
                                           data=cf, is_extended_id=False))
            except Exception:
                return False
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
            elif st > 0:
                time.sleep(st)
        return True

    def _wait_for_fc(self, deadline: float):
        """Block until a Flow Control frame arrives; return (fs, bs, stmin)."""
        while time.monotonic() < deadline:
            resp = self._bus.recv(timeout=0.05)
            if resp is None or resp.arbitration_id != self._rx_id:
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
        return 0.0

    def _receive(self, expected_len: Optional[int], timeout: float,
                 passive: bool = False) -> Optional[bytes]:
        """
        Collect response frames. Handles SF, FF+CFs.
        If expected_len is None we infer from the SF/FF length byte.
        When passive=True (sniffing), no Flow Control is transmitted.
        """
        import can
        deadline  = time.monotonic() + timeout
        payload   = bytearray()
        total_len = expected_len  # None until we parse SF/FF
        cf_index  = 1             # expected consecutive frame SN

        while time.monotonic() < deadline:
            resp = self._bus.recv(timeout=0.05)
            if resp is None or resp.arbitration_id != self._rx_id:
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
                payload = bytearray(raw[1:1 + length])
                return bytes(payload)

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
                arbitration_id=self._tx_id,
                data=fc,
                is_extended_id=False,
            )
            self._bus.send(msg)
        except Exception:
            pass


def recv_isotp(bus, rx_id: int, timeout: float = 1.0) -> Optional[bytes]:
    """
    Passive receive only (no request sent). Useful for sniffing ISO-TP responses.
    Returns assembled payload or None on timeout.
    """
    dummy_tx = rx_id - 8  # typical response offset reversed
    session  = ISOTPSession(bus, tx_id=dummy_tx, rx_id=rx_id)
    # passive=True: do not inject Flow Control while merely sniffing, which would
    # otherwise put frames on the bus and could corrupt another tester's transfer.
    return session._receive(None, timeout, passive=True)
