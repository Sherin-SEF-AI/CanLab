"""
Bus load meter: estimate CAN bus utilization percentage.

Assumes 500 kbps; each standard-frame overhead ≈ 125 bits (11-bit ID, 6 EOF/IFS, etc.)
plus 8 * DLC data bits.
"""

_FRAME_OVERHEAD_BITS = 47   # standard CAN frame min (no stuffing)
_BUS_SPEED_BPS       = 500_000


class BusLoadMeter:
    def __init__(self, window_ms: int = 1000):
        self._window_ms  = window_ms
        self._frame_bits = 0
        self._last_reset = 0.0

    def add_frame(self, dlc: int, timestamp: float):
        import time
        now = time.monotonic()
        if self._last_reset == 0.0:
            self._last_reset = now

        self._frame_bits += _FRAME_OVERHEAD_BITS + dlc * 8

        elapsed = (now - self._last_reset) * 1000.0
        if elapsed >= self._window_ms:
            utilization = self._frame_bits / (_BUS_SPEED_BPS * elapsed / 1000.0)
            self._frame_bits = 0
            self._last_reset = now
            return min(1.0, utilization)
        return None   # window not complete yet

    def reset(self):
        self._frame_bits = 0
        self._last_reset = 0.0
