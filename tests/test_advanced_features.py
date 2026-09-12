"""Annotated capture, replay overrides, undo history, FD-length packing."""
import numpy as np
import pandas as pd
import pytest

from canlab.core.annotations import AnnotationSet, candidate_to_signal, rank_candidates
from canlab.core.replay import apply_overrides


def _frame_rows(payloads, can_id):
    d = pd.DataFrame(payloads, columns=[f"B{k}" for k in range(8)])
    d["Timestamp"] = np.arange(len(d)) * 0.01
    d["ID"] = can_id
    return d


# ── annotations ──────────────────────────────────────────────────────────────

def _brake_capture(n=2000, presses=((300, 500), (900, 1100), (1500, 1700))):
    """3A0: a flag in B2 bit 4 and a pedal value in B3 follow the presses.
    3B0: pure noise. Everything else in 3A0 is noise or a counter."""
    rng = np.random.default_rng(0)
    pressed = lambda i: any(a <= i < b for a, b in presses)
    a = []
    for i in range(n):
        b2 = (1 << 4 if pressed(i) else 0) | (i & 0x0F)
        b3 = int(180 + rng.normal(0, 5)) if pressed(i) else int(20 + rng.normal(0, 5))
        a.append([rng.integers(0, 256), rng.integers(0, 256), b2, max(0, min(255, b3)), 0, 0, 0, 0])
    real = _frame_rows(a, "3A0")
    noise = real.copy()
    noise["ID"] = "3B0"
    noise["B2"] = rng.integers(0, 256, n)
    noise["B3"] = rng.integers(0, 256, n)
    ann = AnnotationSet()
    for lo, hi in presses:
        ann.add("brake", lo * 0.01, hi * 0.01)
    return pd.concat([real, noise], ignore_index=True), ann


def test_the_flag_and_the_pedal_rank_first_and_noise_does_not():
    df, ann = _brake_capture()
    cands = rank_candidates(df, ann, top=10)
    assert cands, "nothing ranked"
    top_locations = [(c.can_id, c.byte, c.bit) for c in cands[:3]]
    assert ("3A0", 2, 4) in top_locations, f"the switch is not in the top 3: {top_locations}"
    assert any(c.can_id == "3A0" and c.byte == 3 and c.bit is None for c in cands[:5]), \
        "the pedal byte is not near the top"
    assert not any(c.can_id == "3B0" for c in cands), "noise message ranked"


def test_an_inverted_flag_scores_negative():
    df, ann = _brake_capture()
    df.loc[df["ID"] == "3A0", "B2"] ^= 0x10          # invert the switch bit
    cands = rank_candidates(df, ann, top=5)
    switch = next(c for c in cands if c.byte == 2 and c.bit == 4)
    assert switch.r < -0.8


def test_open_marks_are_ignored_and_begin_end_pairs_work():
    """Live use is begin() when the pedal goes down, end() when it comes up.
    An interval still open must not count, and paired ones must."""
    df, _ = _brake_capture()
    ann = AnnotationSet()
    ann.begin("brake", 3.0)                     # left open: must not count
    assert rank_candidates(df, ann) == []
    ann.end("brake", 5.0)
    ann.begin("brake", 9.0)
    ann.end("brake", 11.0)
    ann.begin("brake", 15.0)
    ann.end("brake", 17.0)
    assert any(c.byte == 2 and c.bit == 4 for c in rank_candidates(df, ann))


def test_marking_one_press_of_three_still_surfaces_the_switch_by_byte():
    """One mark out of three gives 80% bit agreement, on the bit threshold.
    The byte-level score is looser and should still show the message."""
    df, _ = _brake_capture()
    ann = AnnotationSet()
    ann.add("brake", 3.0, 5.0)
    cands = rank_candidates(df, ann)
    assert any(c.can_id == "3A0" and c.byte == 2 for c in cands)


def test_annotations_round_trip_through_json():
    _, ann = _brake_capture()
    back = AnnotationSet.from_json(ann.to_json())
    assert [(a.label, a.start, a.end) for a in back.items] == \
        [(a.label, a.start, a.end) for a in ann.items]


def test_candidate_becomes_a_dbc_signal():
    df, ann = _brake_capture()
    c = next(x for x in rank_candidates(df, ann) if x.bit == 4)
    sig = candidate_to_signal(c)
    assert sig["start_bit"] == 2 * 8 + 4 and sig["length"] == 1
    assert sig["signal_name"] == "BRAKE"


# ── replay overrides ─────────────────────────────────────────────────────────

SIGS = [
    {"message_id": "0A6", "message_name": "W", "signal_name": "Speed", "start_bit": 7,
     "length": 16, "byte_order": "big", "value_type": "unsigned", "scale": 0.03125,
     "offset": 0.0, "min_val": 0, "max_val": 500, "unit": "km/h", "description": ""},
    {"message_id": "0A6", "message_name": "W", "signal_name": "Counter", "start_bit": 56,
     "length": 8, "byte_order": "little", "value_type": "unsigned", "scale": 1,
     "offset": 0, "min_val": 0, "max_val": 255, "unit": "", "description": ""},
]
FRAME = bytes([0x07, 0x80, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x2A])


def test_override_holds_one_signal_and_leaves_the_rest():
    from canlab.core.dbc_manager import decode_frame
    out = apply_overrides("0A6", FRAME, {"Speed": 0.0}, SIGS)
    assert decode_frame(SIGS, "0A6", out)["Speed"] == 0.0
    assert out[7] == 0x2A, "the counter signal was disturbed"
    assert out[2:7] == FRAME[2:7], "bytes the DBC does not describe were touched"
    assert len(out) == len(FRAME)


def test_override_respects_big_endian_byte_span():
    """A 16-bit big-endian field at start bit 7 spans bytes 0 and 1, not 0..2.
    Counting bits from start_bit said byte 2 too, and zeroed it."""
    out = apply_overrides("0A6", FRAME, {"Speed": 100.0}, SIGS)
    assert out[2] == 0xAA


def test_override_ignores_other_messages_and_bad_frames():
    assert apply_overrides("111", FRAME, {"Speed": 0}, SIGS) == FRAME
    assert apply_overrides("0A6", FRAME, {"NotASignal": 0}, SIGS) == FRAME


# ── undo history ─────────────────────────────────────────────────────────────

def _sig(name):
    return {"message_id": "100", "message_name": "M", "signal_name": name,
            "start_bit": 0, "length": 8, "byte_order": "little",
            "value_type": "unsigned", "scale": 1.0, "offset": 0.0,
            "min_val": 0, "max_val": 255, "unit": "", "description": ""}


@pytest.fixture
def fresh_state(qcore):
    from canlab.core.state import get_state
    st = get_state()
    st.dbc_signals.clear()
    st.__dict__.pop("_dbc_history", None)
    st.__dict__.pop("_dbc_future", None)
    yield st
    st.dbc_signals.clear()


def test_undo_and_redo_walk_the_edit_history(fresh_state):
    st = fresh_state
    names = lambda: [s["signal_name"] for s in st.dbc_signals]
    st.add_dbc_signal(_sig("A"))
    st.add_dbc_signal(_sig("B"))
    st.update_dbc_signal(1, _sig("B2"))
    st.remove_dbc_signal(0)
    assert names() == ["B2"]
    assert st.undo_dbc() and names() == ["A", "B2"]
    assert st.undo_dbc() and names() == ["A", "B"]
    assert st.redo_dbc() and names() == ["A", "B2"]
    for _ in range(5):
        st.undo_dbc()
    assert names() == [] and not st.can_undo_dbc()


def test_a_new_edit_discards_the_redo_branch(fresh_state):
    st = fresh_state
    st.add_dbc_signal(_sig("A"))
    st.undo_dbc()
    assert st.can_redo_dbc()
    st.add_dbc_signal(_sig("C"))
    assert not st.can_redo_dbc()


def test_a_bulk_import_is_one_undo_step(fresh_state):
    st = fresh_state
    st.add_dbc_signal(_sig("keep"))
    added = st.add_dbc_signals([_sig(f"S{i}") for i in range(50)])
    assert added == 50 and len(st.dbc_signals) == 51
    st.undo_dbc()
    assert [s["signal_name"] for s in st.dbc_signals] == ["keep"]


# ── CAN FD ───────────────────────────────────────────────────────────────────

def test_pack_signal_pads_to_the_fd_message_length():
    from canlab.core.injection import pack_signal
    fd = dict(_sig("Far"), message_id="1F0", start_bit=160, length=16,
              max_val=65535, msg_length=32)
    data = pack_signal(0x1234, fd)
    assert len(data) == 32
    assert data[20:22] == bytes([0x34, 0x12])
    assert len(pack_signal(5, dict(_sig("Near"), msg_length=8))) == 8


def test_replay_marks_long_frames_as_fd():
    """python-can refuses more than eight bytes on a classic message."""
    import inspect

    from canlab.core import replay
    src = inspect.getsource(replay.ReplayWorker.run)
    assert "is_fd=len(data) > 8" in src
