"""Tests for OBD-II supported-PID mask decoding, including continuation
windows above 0x20 (#17)."""
from core.obd2_pids import supported_pids_from_mask


def test_base_window_pids_1_to_32():
    # Bit for PID 1 is the MSB of byte 0.
    mask = bytes([0x80, 0x00, 0x00, 0x00])
    assert supported_pids_from_mask(mask) == [1]


def test_next_window_flag_bit():
    # Bit 32 (LSB) set -> PID 0x20 supported (the "next window" marker).
    mask = bytes([0x00, 0x00, 0x00, 0x01])
    assert supported_pids_from_mask(mask) == [0x20]


def test_continuation_window_offsets_by_base():
    mask = bytes([0x80, 0x00, 0x00, 0x00])
    assert supported_pids_from_mask(mask, base=0x20) == [0x21]
    assert supported_pids_from_mask(mask, base=0x40) == [0x41]


def test_short_mask_returns_empty():
    assert supported_pids_from_mask(b"\x00\x00") == []
