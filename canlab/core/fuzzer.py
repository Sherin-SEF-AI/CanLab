"""
Fuzz testing worker — injects random/boundary/mutation values to unknown IDs.

Strategies:
  random    — random byte values each iteration
  boundary  — cycles through 0x00, 0xFF, 0x55, 0xAA, then random
  mutation  — take last known frame for this ID and flip random bits
"""
import random
import time
from PyQt6.QtCore import QThread, pyqtSignal


_BOUNDARY_VALUES = [0x00, 0xFF, 0x55, 0xAA, 0x01, 0x7F, 0x80, 0xFE]


class FuzzWorker(QThread):
    hit        = pyqtSignal(str, bytes, float)   # id_hex, data, timestamp
    progress   = pyqtSignal(int)                  # iterations done
    error      = pyqtSignal(str)

    def __init__(self, bus, target_id: int, dlc: int = 8,
                 strategy: str = "boundary", rate_hz: float = 10.0,
                 max_iter: int = 1000, parent=None):
        super().__init__(parent)
        self._bus       = bus
        self._target_id = target_id
        self._dlc       = dlc
        self._strategy  = strategy
        self._rate_hz   = max(0.1, min(rate_hz, 100.0))
        # Bounded by default: 0 means unlimited, but callers should pass an
        # explicit cap. Unbounded fuzzing of a live bus is hazardous.
        self._max_iter  = max_iter
        self._abort     = False
        self._iter      = 0
        self._bv_idx    = 0
        self._last_data = bytes(dlc)

    def stop(self):
        self._abort = True
        self.quit()
        self.wait(2000)

    def run(self):
        import can
        from core.safety import require_armed, BusNotArmedError
        interval = 1.0 / self._rate_hz
        try:
            require_armed()
            while not self._abort:
                data = self._next_payload()
                msg  = can.Message(
                    arbitration_id=self._target_id,
                    data=data,
                    is_extended_id=False,
                )
                try:
                    self._bus.send(msg)
                    self.hit.emit(
                        format(self._target_id, "03X"),
                        data,
                        time.time(),
                    )
                except Exception as e:
                    self.error.emit(str(e))
                    break

                self._iter += 1
                self.progress.emit(self._iter)
                if self._max_iter and self._iter >= self._max_iter:
                    break
                # Interruptible sleep so stop() responds promptly even at low rates.
                slept = 0.0
                while slept < interval and not self._abort:
                    step = min(0.05, interval - slept)
                    time.sleep(step)
                    slept += step
        except BusNotArmedError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(str(e))

    def _next_payload(self) -> bytes:
        if self._strategy == "random":
            return bytes(random.randint(0, 255) for _ in range(self._dlc))

        if self._strategy == "boundary":
            val = _BOUNDARY_VALUES[self._bv_idx % len(_BOUNDARY_VALUES)]
            self._bv_idx += 1
            return bytes([val] * self._dlc)

        if self._strategy == "mutation":
            data = bytearray(self._last_data)
            byte_idx = random.randint(0, self._dlc - 1)
            bit_idx  = random.randint(0, 7)
            data[byte_idx] ^= (1 << bit_idx)
            self._last_data = bytes(data)
            return self._last_data

        # fallback — all zeros
        return bytes(self._dlc)
