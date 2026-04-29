"""Replay a parsed CAN log back onto a live bus."""
import time
import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal

BYTE_COLS = [f"B{i}" for i in range(8)]


class ReplayWorker(QThread):
    tick         = pyqtSignal(int, int)   # current_frame, total_frames
    loop_started = pyqtSignal(int)        # loop iteration number (1-based)
    finished     = pyqtSignal()
    error        = pyqtSignal(str)

    def __init__(self, bus, frames_df: pd.DataFrame,
                 speed: float = 1.0, loop: bool = False, parent=None):
        super().__init__(parent)
        self._bus      = bus
        self._df       = frames_df.copy()
        self._speed    = max(0.1, speed)
        self._running  = True
        self._paused   = False
        self._loop     = loop
        self._seek_idx = None   # set by seek(); None = no pending seek

    def stop(self):
        self._running = False
        self.quit()
        self.wait(2000)

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def is_paused(self) -> bool:
        return self._paused

    def set_speed(self, speed: float):
        self._speed = max(0.1, speed)

    def seek(self, idx: int):
        """Jump playback to frame index idx on the next loop iteration check."""
        self._seek_idx = max(0, int(idx))

    def run(self):
        import can
        rows = self._df.sort_values("Timestamp").reset_index(drop=True)
        n    = len(rows)
        if n == 0:
            self.finished.emit()
            return

        loop_count = 0
        start_idx  = 0

        while self._running:
            t0_real  = time.monotonic()
            t0_log   = rows.iloc[start_idx]["Timestamp"]
            _seeked  = False   # True when seek() was called mid-pass

            for idx in range(start_idx, n):
                # Seek: break out so the while loop restarts from the new index
                pending = self._seek_idx
                if pending is not None:
                    self._seek_idx = None
                    start_idx = max(0, min(int(pending), n - 1))
                    _seeked = True
                    break

                while self._paused and self._running:
                    time.sleep(0.05)
                if not self._running:
                    break

                row = rows.iloc[idx]
                target_offset = (row["Timestamp"] - t0_log) / self._speed
                elapsed = time.monotonic() - t0_real
                wait = target_offset - elapsed
                if wait > 0:
                    time.sleep(wait)

                try:
                    raw_id = row.get("ID", "0")
                    arb_id = int(str(raw_id), 16) if isinstance(raw_id, str) else int(raw_id)
                    data   = bytes(
                        int(row[f"B{i}"]) if pd.notna(row.get(f"B{i}")) else 0
                        for i in range(8)
                    )
                    extended = (
                        bool(row.get("Extended", False))
                        if "Extended" in row.index
                        else (arb_id > 0x7FF)
                    )
                    msg = can.Message(
                        arbitration_id=arb_id,
                        data=data,
                        is_extended_id=extended,
                    )
                    self._bus.send(msg)
                except Exception as e:
                    self.error.emit(str(e))

                if idx % 50 == 0:
                    self.tick.emit(idx, n)

            if _seeked:
                continue   # restart while loop with new start_idx / t0

            if not self._running:
                break

            self.tick.emit(n, n)

            if self._loop:
                loop_count += 1
                start_idx = 0
                self.loop_started.emit(loop_count)
            else:
                break

        self.finished.emit()
