"""Signal injection: pack a value into a CAN frame and send it."""
import struct
from PyQt6.QtCore import QThread, pyqtSignal


def pack_signal(value: float, sig: dict) -> bytearray:
    """
    Bit-pack a physical value into an 8-byte CAN payload according to signal def.
    Applies Hyundai rolling-counter in upper nibble of byte 0 if requested.
    """
    scale  = float(sig.get("scale",  1.0))
    offset = float(sig.get("offset", 0.0))
    raw    = int((value - offset) / scale)

    start_bit = int(sig.get("start_bit", 0))
    length    = int(sig.get("length",    8))
    byte_idx  = start_bit // 8
    bit_off   = start_bit % 8

    data = bytearray(8)
    # Write raw value into the correct byte(s) — simple little-endian
    raw_masked = raw & ((1 << length) - 1)
    for bit in range(length):
        byte_pos = (start_bit + bit) // 8
        bit_pos  = (start_bit + bit) % 8
        if byte_pos < 8:
            if raw_masked & (1 << bit):
                data[byte_pos] |= (1 << bit_pos)
            else:
                data[byte_pos] &= ~(1 << bit_pos)
    return data


def hyundai_checksum(data: bytes, msg_id: int) -> int:
    checksum = sum(data[:7])
    checksum += (msg_id >> 8) & 0xFF
    checksum += msg_id & 0xFF
    return (~checksum) & 0xFF


class InjectionWorker(QThread):
    """Periodically send a single signal value onto the bus."""
    error    = pyqtSignal(str)
    tick     = pyqtSignal(str, float)   # sig_name, value

    def __init__(self, bus, sig: dict, value: float,
                 period_ms: int = 10, apply_checksum: bool = True,
                 apply_counter: bool = True, parent=None):
        super().__init__(parent)
        self._bus            = bus
        self._sig            = sig
        self._value          = value
        self._period_ms      = period_ms
        self._apply_checksum = apply_checksum
        self._apply_counter  = apply_counter
        self._counter        = 0
        self._running        = True

    def stop(self):
        self._running = False

    def run(self):
        import time
        import can
        try:
            mid_str = self._sig.get("message_id", "0")
            mid = int(mid_str, 16) if mid_str else 0
        except (ValueError, TypeError):
            mid = 0

        while self._running:
            try:
                data = pack_signal(self._value, self._sig)
                if self._apply_counter:
                    self._counter = (self._counter + 1) & 0x0F
                    data[0] = (data[0] & 0x0F) | (self._counter << 4)
                if self._apply_checksum:
                    data[7] = hyundai_checksum(bytes(data), mid)
                msg = can.Message(
                    arbitration_id=mid,
                    data=bytes(data),
                    is_extended_id=False,
                )
                self._bus.send(msg)
                self.tick.emit(
                    self._sig.get("signal_name", "?"), self._value
                )
            except Exception as e:
                self.error.emit(str(e))
            import time as _t
            _t.sleep(self._period_ms / 1000.0)

    def set_value(self, v: float):
        self._value = v
