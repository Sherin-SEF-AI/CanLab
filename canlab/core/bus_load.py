"""
Bus load meter: estimate CAN bus utilization percentage.

Windowing is driven by the *caller-supplied* frame timestamp (log time or
wall-clock), not an internal monotonic clock, so it stays consistent with
BusHealthMeter's per-ID gap tracking, which keys off the same timestamps.

Overhead is approximated as a fixed per-frame bit count (standard 11-bit frame,
no bit-stuffing) plus 8 bits per data byte; this under-estimates real bus load
slightly but is stable and cheap.
"""

_FRAME_OVERHEAD_BITS = 47   # standard CAN frame minimum (no stuffing)
_DEFAULT_BITRATE_BPS = 500_000


class BusLoadMeter:
    def __init__(self, bitrate: int = _DEFAULT_BITRATE_BPS, window_ms: int = 1000):
        self._bitrate    = bitrate or _DEFAULT_BITRATE_BPS
        self._window_ms  = window_ms
        self._frame_bits = 0
        self._last_reset = None   # first timestamp seen

    def add_frame(self, dlc: int, timestamp: float):
        if timestamp is None:
            import time
            timestamp = time.monotonic()
        if self._last_reset is None:
            self._last_reset = timestamp

        self._frame_bits += _FRAME_OVERHEAD_BITS + dlc * 8

        elapsed_ms = (timestamp - self._last_reset) * 1000.0
        if elapsed_ms >= self._window_ms and elapsed_ms > 0:
            utilization = self._frame_bits / (self._bitrate * elapsed_ms / 1000.0)
            self._frame_bits = 0
            self._last_reset = timestamp
            return min(1.0, utilization)
        return None   # window not complete yet

    def reset(self):
        self._frame_bits = 0
        self._last_reset = None
