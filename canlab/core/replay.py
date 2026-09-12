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
                 speed: float = 1.0, loop: bool = False, parent=None,
                 overrides: dict | None = None, dbc_signals: list | None = None):
        super().__init__(parent)
        self._bus      = bus
        self._df       = frames_df.copy()
        # {signal_name: physical value}. Frames whose message carries one of
        # these signals are re-encoded with that value held, everything else
        # in the frame left as recorded. See apply_overrides.
        self._overrides   = dict(overrides or {})
        self._dbc_signals = list(dbc_signals or [])
        self._speed    = min(max(0.1, speed), 100.0)   # clamp: no unbounded flood
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
        self._speed = min(max(0.1, speed), 100.0)

    def seek(self, idx: int):
        """Jump playback to frame index idx on the next loop iteration check."""
        self._seek_idx = max(0, int(idx))

    def run(self):
        import can
        from canlab.core import safety
        from canlab.core.safety import BusNotArmedError, BlockedIdError
        rows = self._df.sort_values("Timestamp").reset_index(drop=True)
        n    = len(rows)
        if n == 0:
            self.finished.emit()
            return
        safety.register_tx_worker(self)

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
                    # Interruptible: a long inter-frame gap must not block stop().
                    end = time.monotonic() + wait
                    while self._running:
                        remaining = end - time.monotonic()
                        if remaining <= 0:
                            break
                        time.sleep(min(0.05, remaining))
                if not self._running:
                    break

                try:
                    raw_id = row.get("ID", "0")
                    arb_id = int(str(raw_id), 16) if isinstance(raw_id, str) else int(raw_id)
                    # Honour the recorded DLC. Always sending 8 bytes with 0
                    # for the absent ones puts a different frame on the bus
                    # than was captured: a 3-byte message came back as 8 with
                    # five spurious zeros.
                    dlc_val = row.get("DLC")
                    count = int(dlc_val) if pd.notna(dlc_val) else 8
                    count = max(0, min(count, 64))
                    data = bytes(
                        int(row[f"B{i}"]) & 0xFF if pd.notna(row.get(f"B{i}")) else 0
                        for i in range(count)
                    )
                    if self._overrides:
                        data = apply_overrides(f"{arb_id:03X}", data,
                                               self._overrides, self._dbc_signals)
                    extended = (
                        bool(row.get("Extended", False))
                        if "Extended" in row.index
                        else (arb_id > 0x7FF)
                    )
                    # A recorded FD frame replays as one; python-can refuses
                    # more than eight bytes on a classic message.
                    msg = can.Message(
                        arbitration_id=arb_id,
                        data=data,
                        is_extended_id=extended,
                        is_fd=len(data) > 8,
                        bitrate_switch=len(data) > 8,
                    )
                    safety.gated_send(self._bus, msg)
                except BusNotArmedError as e:
                    self.error.emit(str(e))
                    self._running = False
                    break
                except BlockedIdError as e:
                    self.error.emit(str(e))
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

        safety.unregister_tx_worker(self)
        self.finished.emit()


def apply_overrides(can_id: str, data: bytes, overrides: dict,
                    dbc_signals: list) -> bytes:
    """Re-encode one frame with some of its signals held at chosen values.

    Replay a real drive onto a bench ECU exactly as recorded, but with vehicle
    speed held at zero: that is how you find out what a module does when one
    input disagrees with everything else it sees. The frame is decoded through
    the DBC, the overridden signals replaced, and the whole thing encoded
    again, so counters and checksums that are themselves signals in the DBC are
    preserved and anything not in the DBC is left byte for byte.

    Frames whose message carries none of the overridden signals are returned
    untouched, as are frames the DBC cannot decode.
    """
    from canlab.core.canid import normalize_id
    from canlab.core.dbc_manager import decode_frame, encode_frame

    cid = normalize_id(can_id)
    mine = [s for s in dbc_signals
            if normalize_id(s.get("message_id", "")) == cid
            and s.get("signal_name") in overrides]
    if not mine:
        return data
    try:
        values = decode_frame(dbc_signals, cid, data)
    except Exception:
        return data
    if not values:
        return data
    for s in mine:
        values[s["signal_name"]] = overrides[s["signal_name"]]
    try:
        encoded = encode_frame(dbc_signals, cid, values)
    except Exception:
        return data
    # The DBC decides the encoded length; the frame keeps the recorded one.
    encoded = bytes(encoded).ljust(len(data), b"\x00")[:len(data)]
    # Bytes the DBC does not describe keep their recorded value. Which bytes
    # a signal touches depends on byte order: a big-endian field's bits are
    # not a contiguous range of DBC bit numbers, so ask bit_coords rather
    # than count from start_bit.
    from canlab.core.bit_coords import dbc_to_grid
    out = bytearray(data)
    described = set()
    msg_sigs = [s for s in dbc_signals if normalize_id(s.get("message_id", "")) == cid]
    for s in msg_sigs:
        little = str(s.get("byte_order", "little")).lower().startswith("l")
        for cell in dbc_to_grid(int(s["start_bit"]), int(s["length"]), little):
            described.add(cell // 8)
    for b in described:
        if b < len(out) and b < len(encoded):
            out[b] = encoded[b]
    return bytes(out)
