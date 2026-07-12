"""OBD-II Mode 01 live poller using ISO-TP physical addressing."""
import time
from PyQt6.QtCore import QThread, pyqtSignal

from core.obd2_pids import PID_TABLE, decode_pid, supported_pids_from_mask

# Physical ECU address: tx=0x7E0 → rx=0x7E8 (primary ECU)
_TX_ID = 0x7E0
_RX_ID = 0x7E8


class OBD2Poller(QThread):
    pid_value   = pyqtSignal(int, float, str)  # pid, value, unit
    error       = pyqtSignal(str)
    pids_discovered = pyqtSignal(list)          # list[int] of supported PIDs

    def __init__(self, bus, pids: list[int], interval_ms: int = 200,
                 discover_only: bool = False, parent=None):
        super().__init__(parent)
        self._bus          = bus
        self._pids         = list(pids)
        self._interval     = max(50, interval_ms) / 1000.0  # seconds
        self._running      = True
        self._discover     = discover_only

    def stop(self):
        self._running = False
        self.quit()
        self.wait(2000)

    def run(self):
        from core.isotp import ISOTPSession
        session = ISOTPSession(self._bus, tx_id=_TX_ID, rx_id=_RX_ID)

        if self._discover:
            self._do_discover(session)
            return

        while self._running:
            for pid in self._pids:
                if not self._running:
                    break
                try:
                    payload = session.send(bytes([0x02, 0x01, pid]), timeout=0.5)
                    if payload and len(payload) >= 3 and payload[1] == 0x41 and payload[2] == pid:
                        data = payload[3:]
                        value = decode_pid(pid, data)
                        if value is not None:
                            unit = PID_TABLE.get(pid, {}).get("unit", "")
                            self.pid_value.emit(pid, value, unit)
                except Exception as e:
                    self.error.emit(f"PID 0x{pid:02X}: {e}")
            time.sleep(self._interval)

    def _do_discover(self, session):
        """Query the 'supported PIDs' masks (0x00, 0x20, 0x40, …) and emit them.

        Each window's bit 32 (PID base+0x20) indicates the next window exists, so
        walk them until it clears — this discovers PIDs above 0x20, not just 1–32.
        """
        all_pids = []
        try:
            for base in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0):
                if not self._running:
                    break
                payload = session.send(bytes([0x02, 0x01, base]), timeout=1.0)
                if not (payload and len(payload) >= 6
                        and payload[1] == 0x41 and payload[2] == base):
                    break
                mask_data = payload[3:7]
                window_pids = supported_pids_from_mask(mask_data, base=base)
                all_pids.extend(window_pids)
                # The "next window" PID (base + 0x20) being present means continue.
                if (base + 0x20) not in window_pids:
                    break
            known = [p for p in all_pids if p in PID_TABLE]
            self.pids_discovered.emit(known)
        except Exception as e:
            self.error.emit(f"Discover failed: {e}")
            self.pids_discovered.emit([p for p in all_pids if p in PID_TABLE])
