"""Tests for J1939 DM1 active-DTC decoding (backlog)."""
from core.j1939 import decode_dm1, decode_pgn


def _dm1_with_dtc(spn, fmi, oc, cm=0, lamp=0x40):
    b2 = spn & 0xFF
    b3 = (spn >> 8) & 0xFF
    b4 = (((spn >> 16) & 0x07) << 5) | (fmi & 0x1F)
    b5 = ((cm & 1) << 7) | (oc & 0x7F)
    return bytes([lamp, 0x00, b2, b3, b4, b5])


def test_decode_single_dtc():
    data = _dm1_with_dtc(spn=1234, fmi=5, oc=3)
    res = decode_dm1(data)
    assert res["lamps"]["malfunction"] == 1
    assert len(res["dtcs"]) == 1
    d = res["dtcs"][0]
    assert d["spn"] == 1234
    assert d["fmi"] == 5
    assert d["oc"] == 3
    assert "Current below normal" in d["fmi_name"]


def test_high_spn_bits():
    # SPN needing the top 3 bits (>65535).
    data = _dm1_with_dtc(spn=520192, fmi=3, oc=1)
    d = decode_dm1(data)["dtcs"][0]
    assert d["spn"] == 520192


def test_no_dtc_slots_are_skipped():
    data = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00])  # all-zero DTC slot
    assert decode_dm1(data)["dtcs"] == []


def test_decode_pgn_routes_dm1():
    data = _dm1_with_dtc(spn=100, fmi=1, oc=2)
    res = decode_pgn(0xFECA, data)
    assert "dtcs" in res and res["dtcs"][0]["spn"] == 100
