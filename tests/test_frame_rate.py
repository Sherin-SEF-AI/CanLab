"""What the frame rate says, and what it does when frames are lost.

Asked after a user compared CanLab against SavvyCAN on the same adapter and
saw a much lower number. Two things were wrong on this side, and neither is
the receive path: the hub sustains several hundred thousand frames a second
against a bus that answers instantly.

The rate was the count since the last timer tick, shown as if a second had
passed, and the timer runs on the GUI thread. And a full hand-off buffer threw
the oldest frames away without counting them, so a struggling interface looked
like a quiet bus.
"""
import time
from collections import deque

import pytest

pytest.importorskip("can")

import can

from canlab.core.bus_hub import BusHub


class Firehose:
    """A bus that hands over a frame every time it is asked."""

    def __init__(self, count: int):
        self.count, self.sent = count, 0

    def recv(self, timeout=None):
        if self.sent >= self.count:
            time.sleep(0.005)
            return None
        self.sent += 1
        return can.Message(timestamp=time.time(),
                           arbitration_id=0x100 + (self.sent % 32),
                           data=bytes(8), is_extended_id=False)

    def shutdown(self):
        pass


def drain_all(hub, expected, timeout=20.0):
    got, deadline = [], time.monotonic() + timeout
    while len(got) < expected and time.monotonic() < deadline:
        time.sleep(0.01)
        got.extend(hub.drain())
    return got


def test_the_receive_path_is_not_the_bottleneck():
    """If the display says a few hundred frames a second, that is the bus or
    the adapter's driver, not this code."""
    total = 20_000
    hub = BusHub(Firehose(total), name="bench", bus_index=0, bitrate=500_000)
    hub.start()
    started = time.perf_counter()
    got = drain_all(hub, total)
    elapsed = time.perf_counter() - started
    hub.shutdown()
    assert len(got) == total
    assert total / elapsed > 20_000, f"only {total / elapsed:,.0f} frames/s"


def test_frames_thrown_away_are_counted():
    """A bounded deque drops the oldest silently. Silence here reads as a quiet
    bus, which is the opposite of what is happening."""
    hub = BusHub(Firehose(5_000), name="x", bus_index=0, bitrate=500_000)
    hub._pending = deque(maxlen=100)          # a GUI that never comes back
    hub.start()
    time.sleep(0.5)
    kept = hub.drain()
    hub.shutdown()
    assert len(kept) <= 100
    assert hub.dropped > 0
    assert hub.dropped + len(kept) >= hub.rx_count - 5


def test_nothing_is_dropped_when_the_gui_keeps_up():
    hub = BusHub(Firehose(2_000), name="x", bus_index=0, bitrate=500_000)
    hub.start()
    drain_all(hub, 2_000)
    hub.shutdown()
    assert hub.dropped == 0


# ── the rate the window shows ────────────────────────────────────────────────

def test_the_rate_is_divided_by_the_time_that_actually_passed():
    """The timer lives on the GUI thread, so a slow redraw delays it. Treating
    a late tick as one second reported 1,400 frames as 1,400 a second when
    1.4 seconds had passed, a 40% overstatement that moves with the interface
    rather than with the bus."""
    from canlab.mainwindow import frames_per_second

    assert frames_per_second(1_400, 1.4) == pytest.approx(1_000)
    assert frames_per_second(1_400, 0.7) == pytest.approx(2_000)
    assert frames_per_second(0, 1.0) == 0


def test_the_rate_survives_a_tick_with_no_time_between():
    from canlab.mainwindow import frames_per_second

    assert frames_per_second(10, 0.0) > 0        # no division by zero
