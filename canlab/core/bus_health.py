"""
CAN Bus Health Monitor.

Composes BusLoadMeter and adds:
  - Error frame counting (python-can sets is_error_frame=True)
  - Bus-off event detection (error frame with specific DLC/data pattern)
  - Peak and rolling-average load tracking
  - Per-ID frame gap anomaly detection (ID goes silent > 3× expected period)

Emits health snapshots via AppState.bus_health_update signal.
"""

import time
import numpy as np
from collections import deque
from core.bus_load import BusLoadMeter


class BusHealthMeter:
    """
    Drop-in companion to BusLoadMeter. Call add_frame() for every received
    message (including error frames); call snapshot() periodically to get
    the current health dict and emit it.
    """

    def __init__(self, window_seconds: float = 5.0, bitrate: int = 500_000):
        self._load_meter     = BusLoadMeter(bitrate=bitrate, window_ms=int(window_seconds * 1000))
        self._window         = window_seconds
        self._error_frames   = 0
        self._bus_off_events = 0
        self._load_history: deque = deque(maxlen=120)   # last 120 samples (~2 min)
        self._last_load      = 0.0
        self._peak_load      = 0.0

        # Per-ID last-seen timestamp for gap detection
        self._last_seen: dict[str, float] = {}
        self._periods:   dict[str, float] = {}   # estimated period per ID (seconds)
        self._gap_alerts: list[str]       = []
        self._last_ts:    float           = 0.0   # most recent frame timestamp seen

    def add_frame(self, dlc: int, timestamp: float, can_id: str = "",
                  is_error: bool = False):
        """
        Call for every CAN frame (including error frames).
        Pass is_error=True for frames with python-can's is_error_frame flag set.
        """
        # Error frame detection
        if is_error:
            self._error_frames += 1
            # Bus-off: error frame with DLC == 0 and specific CAN controller pattern
            if dlc == 0:
                self._bus_off_events += 1
            return  # error frames don't count toward load

        self._last_ts = timestamp

        # Load meter
        load = self._load_meter.add_frame(dlc, timestamp)
        if load is not None:
            self._last_load = load
            self._load_history.append(load)
            if load > self._peak_load:
                self._peak_load = load

        # Per-ID gap tracking
        if can_id:
            prev = self._last_seen.get(can_id)
            if prev is not None:
                gap = timestamp - prev
                # Update rolling estimated period (exponential moving average)
                old_period = self._periods.get(can_id, gap)
                self._periods[can_id] = old_period * 0.9 + gap * 0.1
            self._last_seen[can_id] = timestamp

    def check_gaps(self, now: float = None):
        """
        Check for IDs that have gone silent (gap > 3× estimated period).
        Returns list of IDs that appear to have dropped off the bus.

        ``now`` defaults to the timestamp of the most recently ingested frame so
        that gap detection uses the same time base the frames were stamped with
        (log time or wall-clock) — never a monotonic clock, which would not be
        comparable to the stored per-ID timestamps.
        """
        if now is None:
            now = self._last_ts
        silent = []
        for can_id, last_ts in self._last_seen.items():
            period = self._periods.get(can_id, 1.0)
            if period > 0 and (now - last_ts) > period * 3:
                silent.append(can_id)
        return silent

    def snapshot(self, now: float = None) -> dict:
        """Return current health snapshot dict."""
        avg_load = float(np.mean(self._load_history)) if self._load_history else 0.0
        silent   = self.check_gaps(now)
        return {
            "error_frames":   self._error_frames,
            "bus_off":        self._bus_off_events,
            "current_load":   round(self._last_load * 100, 1),   # percent
            "peak_load":      round(self._peak_load * 100, 1),
            "avg_load":       round(avg_load * 100, 1),
            "silent_ids":     silent,
            "total_ids_seen": len(self._last_seen),
        }

    def reset(self):
        self._error_frames   = 0
        self._bus_off_events = 0
        self._peak_load      = 0.0
        self._load_history.clear()
        self._last_seen.clear()
        self._periods.clear()
