"""Tests for the global bus-transmit ARM gate (C8)."""
import pytest

from core import safety


def setup_function(_):
    safety.set_armed(False)


def teardown_function(_):
    safety.set_armed(False)


def test_disarmed_by_default():
    assert safety.is_armed() is False
    with pytest.raises(safety.BusNotArmedError):
        safety.require_armed()


def test_arming_allows_transmit():
    safety.set_armed(True)
    assert safety.is_armed() is True
    safety.require_armed()   # must not raise


def test_disarm_re_blocks():
    safety.set_armed(True)
    safety.set_armed(False)
    with pytest.raises(safety.BusNotArmedError):
        safety.require_armed()


# ── The actuator sweep is a transmit path too ────────────────────────────────

def _sweep_signal():
    return {"message_id": "200", "message_name": "T", "signal_name": "V",
            "start_bit": 0, "length": 8, "byte_order": "little",
            "value_type": "unsigned", "scale": 1.0, "offset": 0.0,
            "min_val": 0, "max_val": 255, "unit": "", "description": ""}


class _RecordingBus:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


def test_safety_scan_sends_nothing_while_disarmed():
    """SafetyScanWorker was the one transmit path with no ARM TX check.

    It sweeps an actuator between its limits, which is the most physically
    consequential thing the tool emits, and it put frames on the bus while the
    toolbar said DISARMED.
    """
    import pytest
    pytest.importorskip("PyQt6")
    from core.safety import set_armed
    from core.safety_scanner import SafetyScanWorker

    set_armed(False)
    bus = _RecordingBus()
    worker = SafetyScanWorker(bus=bus, sig=_sweep_signal(), min_val=0,
                              max_val=255, steps=5, step_delay_ms=0,
                              watchdog_id=0, watchdog_timeout_ms=500)
    try:
        worker.run()
        assert bus.sent == [], "actuator sweep transmitted while disarmed"
    finally:
        set_armed(False)


def test_safety_scan_stops_when_disarmed_midway():
    """Disarming has to stop a sweep in flight, not just prevent the next one."""
    import pytest
    pytest.importorskip("PyQt6")
    from core.safety import set_armed
    from core.safety_scanner import SafetyScanWorker

    set_armed(True)
    sent = []

    class Bus:
        def send(self, msg):
            sent.append(msg)
            set_armed(False)          # disarm after the first frame

    worker = SafetyScanWorker(bus=Bus(), sig=_sweep_signal(), min_val=0,
                              max_val=255, steps=20, step_delay_ms=0,
                              watchdog_id=0, watchdog_timeout_ms=500)
    try:
        worker.run()
        assert len(sent) == 1, f"sweep continued after disarm: {len(sent)} frames"
    finally:
        set_armed(False)


def test_armed_sweep_reaches_the_bus():
    """And the gate must not break the feature when it is armed."""
    import pytest
    pytest.importorskip("PyQt6")
    from core.safety import set_armed
    from core.safety_scanner import SafetyScanWorker

    set_armed(True)
    bus = _RecordingBus()
    worker = SafetyScanWorker(bus=bus, sig=_sweep_signal(), min_val=0,
                              max_val=255, steps=5, step_delay_ms=0,
                              watchdog_id=0, watchdog_timeout_ms=500)
    try:
        worker.run()
        assert len(bus.sent) == 5
    finally:
        set_armed(False)
