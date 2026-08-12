"""Signal injection: pack a value into a CAN frame and send it."""
import struct
from PyQt6.QtCore import QThread, pyqtSignal


def _msg_length(sig: dict) -> int:
    """Payload length to build: honour msg_length, else fit the signal (min 8)."""
    declared = sig.get("msg_length")
    if declared:
        try:
            return max(1, min(int(declared), 64))
        except (ValueError, TypeError):
            pass
    try:
        need = (int(sig.get("start_bit", 0)) + int(sig.get("length", 8)) + 7) // 8
    except (ValueError, TypeError):
        need = 8
    return max(8, need)


def _pack_signal_cantools(value: float, sig: dict) -> bytearray | None:
    """Encode one signal with cantools so packing matches decoding exactly.

    Handles big-endian (Motorola) bit numbering and signed values that the
    hand-rolled little-endian packer below gets wrong. Returns None if cantools
    can't encode (caller falls back to the manual packer).
    """
    try:
        import cantools
        from core.dbc_manager import signal_dict_to_cantools
        length = _msg_length(sig)
        ct_sig = signal_dict_to_cantools(sig)
        msg = cantools.database.Message(
            frame_id=1, name="INJ", length=length, signals=[ct_sig],
        )
        data = msg.encode({ct_sig.name: float(value)},
                          scaling=True, padding=True, strict=False)
        return bytearray(data)
    except Exception:
        return None


def pack_signal(value: float, sig: dict) -> bytearray:
    """
    Bit-pack a physical value into a CAN payload according to the signal def.

    Uses cantools for correct little/big-endian and signed packing (identical to
    the decode path); falls back to a little-endian bit writer only if cantools
    can't encode. The previous version always packed little-endian and unsigned,
    so any big-endian or signed signal was injected with a different value than
    the DBC would decode.
    """
    packed = _pack_signal_cantools(value, sig)
    if packed is not None:
        return packed

    scale  = float(sig.get("scale",  1.0))
    offset = float(sig.get("offset", 0.0))
    raw    = int(round((value - offset) / scale))

    start_bit = int(sig.get("start_bit", 0))
    length    = int(sig.get("length",    8))
    n         = _msg_length(sig)

    data = bytearray(n)
    raw_masked = raw & ((1 << length) - 1)
    for bit in range(length):
        byte_pos = (start_bit + bit) // 8
        bit_pos  = (start_bit + bit) % 8
        if byte_pos < n and raw_masked & (1 << bit):
            data[byte_pos] |= (1 << bit_pos)
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
                 period_ms: int = 10, apply_checksum: bool = False,
                 apply_counter: bool = False, extended: bool | None = None,
                 parent=None):
        super().__init__(parent)
        self._bus            = bus
        self._sig            = sig
        self._value          = value
        self._period_ms      = period_ms
        self._apply_checksum = apply_checksum
        self._apply_counter  = apply_counter
        self._extended       = extended
        self._counter        = 0
        self._running        = True

    def stop(self):
        self._running = False

    def run(self):
        import time
        import can
        from core.safety import require_armed, BusNotArmedError
        from core.canid import normalize_id
        try:
            mid = int(normalize_id(self._sig.get("message_id", "0")), 16)
        except (ValueError, TypeError):
            mid = 0
        extended = (self._extended if self._extended is not None
                    else bool(self._sig.get("extended")) or mid > 0x7FF)

        while self._running:
            try:
                require_armed()
                data = pack_signal(self._value, self._sig)
                if self._apply_counter and len(data) > 0:
                    self._counter = (self._counter + 1) & 0x0F
                    data[0] = (data[0] & 0x0F) | (self._counter << 4)
                if self._apply_checksum and len(data) > 0:
                    data[-1] = hyundai_checksum(bytes(data), mid)
                msg = can.Message(
                    arbitration_id=mid,
                    data=bytes(data),
                    is_extended_id=extended,
                )
                self._bus.send(msg)
                self.tick.emit(
                    self._sig.get("signal_name", "?"), self._value
                )
            except BusNotArmedError as e:
                self.error.emit(str(e))
                self._running = False
                break
            except Exception as e:
                self.error.emit(str(e))
            time.sleep(self._period_ms / 1000.0)

    def set_value(self, v: float):
        self._value = v
