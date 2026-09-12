"""Bit-level flags and inferred value tables.

Both exist because per-byte analysis cannot see the signals people want most:
a turn indicator is one bit, a gear selector is four sparse values.
"""
import numpy as np
import pandas as pd

from canlab.core.bit_flags import detect_flags, detect_flags_for_id, flag_to_signal
from canlab.core.value_tables import enum_to_signal, infer_enum_for_byte, infer_enums


def _frames(payloads, can_id="300"):
    d = pd.DataFrame(payloads, columns=[f"B{k}" for k in range(8)])
    d["Timestamp"] = np.arange(len(d)) * 0.01
    d["ID"] = can_id
    return d


def _packed_byte_capture(n=600):
    """B0: nibble counter. B1: sine measurement. B3: a switch in bit 2 and a
    two-bit selector in bits 4..5, padded apart. B2, B4..B7 constant."""
    rows = []
    for i in range(n):
        switch = 1 if (i // 120) % 2 else 0
        selector = (i // 200) % 4
        rows.append([(i & 0x0F) << 4, int(127 + 100 * np.sin(i / 30)), 0,
                     (switch << 2) | (selector << 4), 0, 0, 0, 0])
    return _frames(rows)


# ── flags ────────────────────────────────────────────────────────────────────

def test_a_switch_and_a_packed_field_are_found_and_nothing_else():
    found = detect_flags_for_id(_packed_byte_capture(), "300")
    got = {(f.byte, f.bit, f.width) for f in found}
    assert (3, 2, 1) in got, "the switch in B3 bit 2 was missed"
    assert (3, 4, 2) in got, "the two-bit selector was not grouped as one field"
    assert got == {(3, 2, 1), (3, 4, 2)}, f"extra candidates: {got}"


def test_the_top_bit_of_a_measurement_is_not_a_flag():
    """The MSB of a slow sine toggles rarely and holds long, exactly like a
    switch. The bits below it are churning, which is the tell."""
    found = detect_flags_for_id(_packed_byte_capture(), "300")
    assert not any(f.byte == 1 for f in found), \
        f"measurement bits reported as flags: {[f.label for f in found if f.byte == 1]}"


def test_counter_bits_are_not_flags():
    found = detect_flags_for_id(_packed_byte_capture(), "300")
    assert not any(f.byte == 0 for f in found)


def test_a_constant_byte_yields_nothing():
    d = _frames([[7, 0, 0, 0, 0, 0, 0, 0]] * 200)
    assert detect_flags_for_id(d, "300") == []


def test_flag_becomes_a_one_bit_dbc_signal():
    flag = detect_flags_for_id(_packed_byte_capture(), "300")
    switch = next(f for f in flag if f.width == 1)
    sig = flag_to_signal(switch, "Indicator")
    assert sig["start_bit"] == 3 * 8 + 2 and sig["length"] == 1
    assert sig["max_val"] == 1


def test_detect_flags_sorts_frames_that_arrive_out_of_order():
    d = _packed_byte_capture().sample(frac=1.0, random_state=1)
    assert "300" in detect_flags(d)


# ── enumerations ─────────────────────────────────────────────────────────────

def _enum_capture(n=600):
    rows = []
    for i in range(n):
        gear = [0, 1, 2, 4, 8][(i // 120) % 5]           # sparse, long holds
        speed = int(60 + 50 * np.sin(i / 40))             # contiguous, moving
        crawl = 55 + (i // 100)                            # 55..60, contiguous
        rows.append([gear, speed, (i & 0x0F) << 4, crawl, 0, 0, 0, 0])
    return _frames(rows, "400")


def test_sparse_long_held_values_are_an_enumeration():
    e = infer_enum_for_byte(_enum_capture(), "400", 0)
    assert e is not None
    assert [s.value for s in sorted(e.states, key=lambda s: s.value)] == [0, 1, 2, 4, 8]


def test_a_moving_measurement_is_not_an_enumeration():
    assert infer_enum_for_byte(_enum_capture(), "400", 1) is None


def test_a_counter_is_not_an_enumeration():
    assert infer_enum_for_byte(_enum_capture(), "400", 2) is None


def test_a_slow_crawl_through_contiguous_values_is_not_an_enumeration():
    """55, 56, 57, 58, 59, 60 held for a while each looks enumerated by
    holding time alone. Filling a contiguous range is what a measurement does."""
    assert infer_enum_for_byte(_enum_capture(), "400", 3) is None


def test_enum_exports_as_a_value_table_signal():
    e = infer_enum_for_byte(_enum_capture(), "400", 0)
    for s in e.states:
        if s.value == 0:
            s.name = "PARK"
    sig = enum_to_signal(e, "Gear")
    assert sig["value_table"][0] == "PARK"
    assert sig["value_table"][8].startswith("STATE_")
    assert sig["length"] == 8 and sig["start_bit"] == 0


def test_infer_enums_covers_every_message():
    d = pd.concat([_enum_capture(), _packed_byte_capture()])
    found = infer_enums(d)
    assert "400" in found
