"""Regression tests for core.canid.normalize_id (the .lstrip('0x') bug, C1)."""
import pytest

from core.canid import normalize_id


@pytest.mark.parametrize("value,expected", [
    ("0A6", "0A6"),      # the classic lstrip("0x") victim -> used to become "A6"
    ("0xA6", "0A6"),
    ("0X0A6", "0A6"),
    ("  a6 ", "0A6"),
    ("A6", "0A6"),
    ("244", "244"),
    ("7E0", "7E0"),
    ("18FEF100", "18FEF100"),   # 29-bit extended id keeps its width
    (256, "100"),
    (0, "000"),
])
def test_normalize_id(value, expected):
    assert normalize_id(value) == expected


def test_low_ids_are_not_corrupted():
    # Every ID below 0x100 must survive normalization (the whole point of C1).
    for i in range(0x100):
        s = format(i, "03X")
        assert normalize_id(s) == s
        assert normalize_id(int(s, 16)) == s


def test_empty_and_garbage_are_stable():
    assert normalize_id("") == ""
    assert normalize_id("zz") == "ZZ"
