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
