"""Signal injection: pack a value into a CAN frame and send it."""
from PyQt6.QtCore import QThread, pyqtSignal


def pack_signal(value: float, sig: dict) -> bytearray:
    """Encode a physical value into a payload honouring the signal's byte order,
    sign and width (via cantools); other bits are zero."""
    from canlab.core.dbc_manager import encode_frame
    payload = encode_frame([sig], sig.get("message_id", "0"),
                           {sig.get("signal_name", "SIG"): value})
    data = bytearray(payload)
    if len(data) < 8:
        data.extend(bytes(8 - len(data)))
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
                 apply_counter: bool = False, parent=None):
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
        self.wait(2000)

    def run(self):
        import time
        import can
        from canlab.core import safety
        from canlab.core.safety import BusNotArmedError, BlockedIdError
        safety.register_tx_worker(self)
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
                safety.gated_send(self._bus, msg)
                self.tick.emit(
                    self._sig.get("signal_name", "?"), self._value
                )
            except (BusNotArmedError, BlockedIdError) as e:
                self.error.emit(str(e))
                self._running = False
                break
            except Exception as e:
                self.error.emit(str(e))
            time.sleep(self._period_ms / 1000.0)
        safety.unregister_tx_worker(self)

    def set_value(self, v: float):
        self._value = v
