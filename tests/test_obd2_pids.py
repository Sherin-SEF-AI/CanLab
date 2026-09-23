import pytest
"""Tests for OBD-II supported-PID mask decoding, including continuation
windows above 0x20 (#17)."""
from canlab.core.obd2_pids import supported_pids_from_mask


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


# ── the formulas, against the worked values SAE J1979 publishes ──────────────

from canlab.core.obd2_pids import (  # noqa: E402
    PID_TABLE, decode_obd_dtcs, decode_pid, response_length,
)


@pytest.mark.parametrize("pid,data,expected", [
    (0x0C, [0x1A, 0xF8], 1726.0),            # (256A + B) / 4
    (0x05, [0x7B], 83.0),                    # A - 40
    (0x06, [0x80], 0.0),                     # 100/128 A - 100
    (0x0A, [0x64], 300.0),                   # 3A
    (0x14, [0xC8, 0x80], 1.0),               # A / 200
    (0x23, [0x01, 0x00], 2560.0),            # 10 (256A + B)
    (0x24, [0x80, 0x00], 1.0),               # 2/65536 (256A + B): stoichiometric
    (0x32, [0xFF, 0xFC], -1.0),              # signed (256A + B) / 4
    (0x3C, [0x11, 0x94], 410.0),             # (256A + B) / 10 - 40
    (0x49, [0xFF], 100.0),
    (0x54, [0x80, 0x00], -32768.0),          # signed
    (0x5D, [0x69, 0x00], 0.0),               # (256A + B) / 128 - 210
    (0x61, [0x7D], 0.0),                     # A - 125
    (0xA6, [0x00, 0x01, 0xE2, 0x40], 12345.6),  # 4 bytes / 10
])
def test_published_formulas(pid, data, expected):
    assert decode_pid(pid, bytes(data)) == pytest.approx(expected, abs=1e-3)


def test_pid_0x49_is_the_accelerator_pedal_not_a_throttle():
    assert PID_TABLE[0x49]["name"] == "Accelerator Pedal Position D"


def test_a_short_answer_is_refused_not_misread():
    assert response_length(0x0C) == 2 and decode_pid(0x0C, bytes([0x1A])) is None
    assert response_length(0xA6) == 4 and decode_pid(0xA6, bytes([0, 1, 2])) is None


def test_every_pid_decodes_zeros_inside_its_range():
    for pid, entry in PID_TABLE.items():
        value = decode_pid(pid, bytes(response_length(pid) + 1))
        assert value is not None, hex(pid)
        assert entry["min"] <= value <= entry["max"], (hex(pid), value)


def test_dtc_lists_from_modes_03_07_and_0a():
    assert decode_obd_dtcs(bytes([0x43, 0x02, 0x01, 0x33, 0x03, 0x01])) == ["P0133", "P0301"]
    assert decode_obd_dtcs(bytes([0x47, 0x01, 0xC1, 0x00]), 0x07) == ["U0100"]
    assert decode_obd_dtcs(bytes([0x43, 0x00])) == []            # answered, no codes
    assert decode_obd_dtcs(bytes([0x7F, 0x03, 0x11])) is None     # a refusal is not a list
    assert decode_obd_dtcs(b"") is None
