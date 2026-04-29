"""
Actuator Safety Boundary Scanner.

Sweeps a signal value from min → max in configurable steps while monitoring
a watchdog ID. If the watchdog ID disappears (frequency drops to zero) or an
anomaly frame arrives, the scan aborts and emits safety_cutout.

Reuses InjectionWorker internals (pack_signal, hyundai_checksum).
"""
import time
from PyQt6.QtCore import QThread, pyqtSignal


class SafetyScanWorker(QThread):
    step_done     = pyqtSignal(float, bytes)   # value, raw_bytes_sent
    cutout        = pyqtSignal(float, str)     # value_at_cutout, reason
    scan_finished = pyqtSignal()
    error         = pyqtSignal(str)

    def __init__(self, bus, sig: dict,
                 min_val: float, max_val: float, steps: int = 50,
                 step_delay_ms: int = 200,
                 watchdog_id: int = 0,          # 0 = disabled
                 watchdog_timeout_ms: int = 500,
                 apply_checksum: bool = True,
                 apply_counter: bool  = True,
                 parent=None):
        super().__init__(parent)
        self._bus             = bus
        self._sig             = sig
        self._min             = float(min_val)
        self._max             = float(max_val)
        self._steps           = max(2, int(steps))
        self._step_delay      = step_delay_ms / 1000.0
        self._watchdog_id     = watchdog_id
        self._watchdog_timeout = watchdog_timeout_ms / 1000.0
        self._apply_checksum  = apply_checksum
        self._apply_counter   = apply_counter
        self._abort           = False
        self._last_watchdog_ts = time.monotonic()
        self._counter         = 0

    def stop(self):
        self._abort = True
        self.quit()
        self.wait(2000)

    # Called from mainwindow when a live frame with watchdog_id arrives
    def notify_watchdog(self):
        self._last_watchdog_ts = time.monotonic()

    def run(self):
        from core.injection import pack_signal, hyundai_checksum
        import can

        step_size = (self._max - self._min) / (self._steps - 1)
        mid_str   = self._sig.get("message_id", "0")
        try:
            mid = int(mid_str, 16)
        except (ValueError, TypeError):
            mid = 0

        for i in range(self._steps):
            if self._abort:
                break

            value = self._min + i * step_size
            data  = pack_signal(value, self._sig)

            if self._apply_counter:
                # counter in upper nibble of byte 0
                data[0] = (data[0] & 0x0F) | ((self._counter & 0x0F) << 4)
                self._counter = (self._counter + 1) & 0x0F

            if self._apply_checksum:
                data[7] = hyundai_checksum(bytes(data), mid)

            try:
                msg = can.Message(
                    arbitration_id=mid,
                    data=bytes(data),
                    is_extended_id=False,
                )
                self._bus.send(msg)
            except Exception as e:
                self.error.emit(str(e))
                return

            self.step_done.emit(value, bytes(data))

            # Check watchdog
            if self._watchdog_id:
                elapsed = time.monotonic() - self._last_watchdog_ts
                if elapsed > self._watchdog_timeout:
                    self.cutout.emit(value, f"Watchdog 0x{self._watchdog_id:03X} silent for {elapsed*1000:.0f}ms")
                    return

            time.sleep(self._step_delay)

        if not self._abort:
            self.scan_finished.emit()
