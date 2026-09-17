"""Repeated blocks: consecutive IDs that share a layout, and what they propose."""
import numpy as np
import pandas as pd
import pytest

from canlab.core.block_detector import block_to_signals, detect_blocks
from canlab.core.dbc_manager import decode_frame, validate_signals
from canlab.core.log_parser import parse_log_file

SAMPLE = "canlab/sample_data/sample_kona_drive.csv"


def block_frames(first=0x500, members=16, per_id=100, rate_hz=10.0, dlc=8, seed=1,
                 gap=1, big=True, constant_member=None):
    """A block: byte 0 constant, byte 1 the member index, a 16-bit word at
    bytes 2-3 walking within 3600..3700, a small 8-bit value at byte 4, a
    counter at byte 7. ``constant_member`` freezes one member entirely."""
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(members):
        arb = first + k * gap
        word = 3650 + np.cumsum(rng.integers(-2, 3, per_id))
        word = np.clip(word, 3600, 3700)
        for i in range(per_id):
            w = int(word[i])
            if constant_member is not None and k == constant_member:
                w = 3650
            frozen = constant_member == k
            b = [0xA0, k, (w >> 8) & 0xFF if big else w & 0xFF,
                 w & 0xFF if big else (w >> 8) & 0xFF,
                 25 if frozen else 25 + (i // 20) % 3, 0, 0, 0 if frozen else i & 0xFF]
            rows.append({"Timestamp": i / rate_hz + k * 0.001, "ID": f"{arb:03X}",
                         "Bus": 0, "DLC": dlc, "Extended": False,
                         **{f"B{j}": (b[j] if j < dlc else np.nan) for j in range(8)}})
    return pd.DataFrame(rows).sort_values("Timestamp", kind="stable").reset_index(drop=True)


def test_sixteen_consecutive_ids_form_one_block_with_the_word_at_byte_2():
    blocks = detect_blocks(block_frames())
    assert len(blocks) == 1
    b = blocks[0]
    assert (b.first, b.last, len(b.members), b.dlc) == ("500", "50F", 16, 8)
    assert b.rate_hz == pytest.approx(10.0, rel=0.01) and b.gap_max == 1
    assert b.name == "BLOCK_500_50F"
    assert b.layout_agreement > 0.9            # every member: the same eight roles
    best = b.best_field
    assert (best.start_byte, best.width_bits, best.byte_order) == (2, 16, "big")
    assert best.members_active == 16 and best.consistency > 0.9
    assert 3600 <= best.band[0] and best.band[1] <= 3700
    assert "scale unknown" in best.hint
    assert b.members[0].roles == ["constant", "constant", "constant", "value", "value",
                                  "constant", "constant", "counter"]


def test_endianness_is_chosen_by_the_smoother_reading():
    little = detect_blocks(block_frames(big=False))[0].best_field
    assert (little.start_byte, little.width_bits, little.byte_order) == (2, 16, "little")


def test_a_different_dlc_or_rate_splits_the_run():
    a = block_frames(first=0x500, members=5)
    b = block_frames(first=0x505, members=5, dlc=6)
    c = block_frames(first=0x50A, members=5, rate_hz=50.0)
    blocks = detect_blocks(pd.concat([a, b, c], ignore_index=True))
    assert sorted((x.first, x.last, x.dlc) for x in blocks) == [
        ("500", "504", 8), ("505", "509", 6), ("50A", "50E", 8)]


def test_a_gap_wider_than_allowed_ends_the_block():
    spaced = block_frames(members=6, gap=3)          # 500, 503, 506 ...
    assert detect_blocks(spaced, max_gap=2) == []
    assert len(detect_blocks(spaced, max_gap=3)) == 1
    assert detect_blocks(spaced, max_gap=3)[0].gap_max == 3


def test_candidates_validate_carry_an_index_and_decode_the_raw_word():
    df = block_frames(members=4)
    b = detect_blocks(df)[0]
    sigs = block_to_signals(b, b.best_field)
    assert len(sigs) == 4 and validate_signals(sigs) == []
    assert [s["signal_name"] for s in sigs] == [
        f"BLOCK_500_503_B2_{i:02d}_CANDIDATE" for i in range(4)]
    assert all("scale unknown" in s["description"] for s in sigs)
    assert sigs[0]["start_bit"] == 23 and sigs[0]["byte_order"] == "big"   # DBC Motorola MSB
    row = df[df["ID"] == "502"].iloc[7]
    frame = bytes(int(row[f"B{i}"]) for i in range(8))
    decoded = decode_frame(sigs, "502", frame)
    assert decoded["BLOCK_500_503_B2_02_CANDIDATE"] == frame[2] * 256 + frame[3]
    custom = block_to_signals(b, b.best_field, prefix="CELL_V")
    assert custom[1]["signal_name"] == "CELL_V_B2_01_CANDIDATE"


def test_a_constant_member_is_reported_not_hidden():
    b = detect_blocks(block_frames(members=6, constant_member=2))[0]
    assert len(b.members) == 6 and b.constant_members == 1
    assert b.members[2].constant and b.best_field.members_active == 5
    assert b.as_dict()["member_roles"]["502"] == ["constant"] * 8


def test_a_heterogeneous_run_scores_low():
    rng = np.random.default_rng(7)
    rows = []
    for k in range(6):
        for i in range(100):
            rows.append({"Timestamp": i / 10.0, "ID": f"{0x600 + k:03X}", "Bus": 0, "DLC": 8,
                         **{f"B{j}": int(rng.integers(0, 256)) if (j + k) % 3 else 0
                            for j in range(8)}})
    tidy = detect_blocks(block_frames(members=6))[0]
    messy = detect_blocks(pd.DataFrame(rows))
    assert messy and messy[0].score < tidy.score
    assert messy[0].layout_agreement < tidy.layout_agreement


def test_ids_with_few_frames_are_ignored_and_do_not_break_a_run():
    df = block_frames(members=6)
    rare = pd.DataFrame([{"Timestamp": 0.5, "ID": "503", "Bus": 0, "DLC": 2,
                          "B0": 1, "B1": 2, **{f"B{j}": np.nan for j in range(2, 8)}}])
    df = pd.concat([df[df["ID"] != "503"], rare], ignore_index=True)
    blocks = detect_blocks(df, max_gap=2)
    assert len(blocks) == 1 and len(blocks[0].members) == 5 and blocks[0].gap_max == 2


def test_the_shipped_sample_forms_no_block():
    assert detect_blocks(parse_log_file(SAMPLE)) == []


def test_stop_returns_nothing():
    assert detect_blocks(block_frames(), should_stop=lambda: True) == []


def test_blocks_tab_runs_and_adds_candidates_as_one_undo_step(qcore):
    import time
    from canlab.core.state import get_state
    from canlab.tabs.auto_re_tab import AutoRETab

    state = get_state()
    state.load_frames(block_frames(members=5), "block")
    tab = AutoRETab()
    tab._run_blocks()
    deadline = time.time() + 30
    while time.time() < deadline and tab.blocks_table.rowCount() == 0:
        qcore.processEvents()
        time.sleep(0.02)
    assert tab.blocks_table.rowCount() == 1
    assert tab.blocks_table.item(0, 0).text() == "500" and tab.blocks_table.item(0, 2).text() == "5"
    assert tab.block_fields_table.rowCount() > 0
    assert tab.block_prefix.text() == "BLOCK_500_504"
    before = len(state.dbc_signals)
    tab.block_prefix.setText("PACK")
    tab._add_block_candidates()
    assert len(state.dbc_signals) == before + 5
    assert state.dbc_signals[-1]["signal_name"] == "PACK_B2_04_CANDIDATE"
    assert state.undo_dbc() and len(state.dbc_signals) == before
    tab.cleanup()
    tab.deleteLater()
    qcore.processEvents()
