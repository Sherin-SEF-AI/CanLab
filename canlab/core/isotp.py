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
        else:
            # First Frame — only up to 4095 bytes supported here
            hi = (n >> 8) & 0x0F
            lo = n & 0xFF
            frame = bytes([0x10 | hi, lo]) + data[:6]

        try:
            msg = can.Message(
                arbitration_id=self._tx_id,
                data=frame,
                is_extended_id=False,
            )
            self._bus.send(msg)
        except Exception:
            return None

        return self._receive(n if n > 7 else None, timeout)

    def _receive(self, expected_len: Optional[int], timeout: float) -> Optional[bytes]:
        """
        Collect response frames. Handles SF, FF+CFs.
        If expected_len is None we infer from the SF length byte.
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

            raw  = bytes(resp.data)
            pci  = (raw[0] >> 4) & 0x0F

            if pci == 0x0:  # Single Frame
                length = raw[0] & 0x0F
                payload = bytearray(raw[1:1 + length])
                return bytes(payload)

            if pci == 0x1:  # First Frame
                length = ((raw[0] & 0x0F) << 8) | raw[1]
                total_len = length
                payload   = bytearray(raw[2:])  # first 6 payload bytes
                # Send Flow Control — CTS, BS=0, STmin=0
                self._send_fc()
                cf_index = 1
                continue

            if pci == 0x2:  # Consecutive Frame
                sn = raw[0] & 0x0F
                if sn != (cf_index & 0x0F):
                    return None  # sequence error
                payload   += bytearray(raw[1:])
                cf_index  += 1
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
    return session._receive(None, timeout)
