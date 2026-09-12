"""FrameStore: canonical materialisation, cap/drop, per-ID lookups, thread safety."""
import threading
import time

import numpy as np
import pytest

from canlab.core.frame_store import FrameStore
from canlab.core.log_parser import make_row


def _fill(store, n=100, ids=(0x1A0, 0x2B0), dlc=8):
    for i in range(n):
        store.append(i * 0.01, ids[i % len(ids)], False, 0, dlc,
                     bytes([(i + k) & 0xFF for k in range(dlc)]))
    return store


def test_materialise_matches_a_naively_built_frame():
    store = _fill(FrameStore(), 50)
    df = store.materialize()
    assert list(df.columns) == (["Timestamp", "ID", "Bus", "DLC", "Extended"]
                                + [f"B{i}" for i in range(8)] + ["Delta"])
    assert len(df) == 50
    assert df["ID"].iloc[0] == "1A0" and df["ID"].iloc[1] == "2B0"
    assert df["B0"].iloc[3] == 3 and df["B7"].iloc[3] == 10
    assert df["DLC"].dtype.kind == "i" and df["Bus"].dtype.kind == "i"
    assert df["Timestamp"].iloc[-1] == pytest.approx(0.49)


def test_short_frames_pad_with_nan_and_fd_widens():
    store = FrameStore()
    store.append(0.0, 0x100, False, 0, 3, b"\x01\x02\x03")
    df = store.materialize()
    assert df["B2"].iloc[0] == 3 and np.isnan(df["B3"].iloc[0])
    import re
    byte_cols = [c for c in df.columns if re.fullmatch(r"B\d+", c)]
    assert byte_cols == [f"B{i}" for i in range(8)]
    store.append(0.1, 0x101, False, 0, 12, bytes(range(12)))
    df = store.materialize()
    assert df["B11"].iloc[1] == 11 and np.isnan(df["B11"].iloc[0])
    assert np.isnan(df["B8"].iloc[0])           # the classic frame stays 8 bytes


def test_delta_is_per_id_and_continues_across_batches():
    store = FrameStore()
    for ts in (0.0, 0.10, 0.25):
        store.append(ts, 0x1A0, False, 0, 1, b"\x00")
    store.append(0.30, 0x2B0, False, 0, 1, b"\x00")
    df = store.materialize()
    assert df[df["ID"] == "1A0"]["Delta"].tolist() == pytest.approx([0.0, 0.10, 0.15])
    assert df[df["ID"] == "2B0"]["Delta"].tolist() == [0.0]


def test_cap_drops_oldest_and_keeps_newest():
    store = FrameStore(cap=1000)
    _fill(store, 1600, ids=(0x1A0,))
    assert len(store) <= 1000
    assert store.dropped > 0
    df = store.materialize()
    assert df["Timestamp"].iloc[-1] == pytest.approx(15.99)   # newest kept
    assert len(df) == len(store)
    assert store.id_stats()["1A0"].count == len(df)           # stats re-derived


def test_snapshot_is_independent_of_later_appends():
    store = _fill(FrameStore(), 20)
    snap = store.snapshot().copy()
    _fill(store, 20)
    assert len(snap) == 20 and len(store.materialize()) == 40
    assert snap["Timestamp"].iloc[-1] == pytest.approx(0.19)


def test_frames_for_id_matches_a_boolean_mask():
    store = _fill(FrameStore(), 60)
    df = store.materialize()
    expected = df[df["ID"] == "1A0"]
    got = store.frames_for_id("1A0")
    assert len(got) == len(expected)
    assert got["B0"].tolist() == expected["B0"].tolist()
    assert got["Timestamp"].tolist() == expected["Timestamp"].tolist()
    # index labels line up with the full frame, so .loc round-trips (plot tab)
    s = got["B0"].dropna()
    assert got.loc[s.index, "Timestamp"].tolist() == expected["Timestamp"].tolist()
    assert len(store.frames_for_id("1A0", tail=5)) == 5
    assert store.frames_for_id("7FF").empty
    assert store.frames_for_id("0x1a0").equals(got)          # id form is normalised


def test_id_stats_and_last_frame_are_incremental():
    store = _fill(FrameStore(), 100)
    st = store.id_stats()["1A0"]
    assert st.count == 50 and st.first_ts == 0.0
    assert st.mean_period == pytest.approx(0.02)
    assert st.frequency == pytest.approx(50.0)
    last = store.last_frame("1A0")
    assert last["ID"] == "1A0" and last["B0"] == 98
    assert store.last_frame("7FF") is None
    assert store.unique_ids() == ["1A0", "2B0"]


def test_dataframe_round_trip_through_load():
    store = _fill(FrameStore(), 30)
    df = store.materialize()
    other = FrameStore()
    other.load_dataframe(df)
    back = other.materialize()
    for col in ("Timestamp", "ID", "DLC", "B0", "B7", "Delta"):
        assert back[col].tolist() == pytest.approx(df[col].tolist()) if col != "ID" \
            else back[col].tolist() == df[col].tolist()


def test_append_batch_from_hub_rows():
    rows = [make_row(i * 0.01, 0x1A0, False, 0, bytes([i, 2, 3])) for i in range(5)]
    store = FrameStore()
    assert store.append_batch(rows) == 5
    df = store.materialize()
    assert df["DLC"].tolist() == [3] * 5 and df["B0"].tolist() == [0, 1, 2, 3, 4]
    assert np.isnan(df["B3"]).all()


def test_string_operations_consumers_rely_on():
    store = _fill(FrameStore(), 20)
    df = store.materialize()
    assert df["ID"].str.contains("1A", case=False).any()
    assert set(df["ID"].unique().tolist()) == {"1A0", "2B0"}
    assert df.groupby("ID")["Timestamp"].count().to_dict() == {"1A0": 10, "2B0": 10}
    assert (df["ID"] == "1A0").sum() == 10


def test_reads_are_safe_while_another_thread_appends():
    store = FrameStore()
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            store.append(i * 0.001, 0x1A0, False, 0, 8, bytes(8))
            i += 1

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            df = store.materialize()
            assert list(df.columns)[0] == "Timestamp"
            assert len(df) == len(df["ID"])        # never a half-built frame
            store.frames_for_id("1A0", tail=50)
    finally:
        stop.set()
        t.join(2)
    assert len(store) > 0


def test_append_stays_flat_as_the_store_grows():
    """The regression this replaced: pd.concat made every batch cost O(n)."""
    store = FrameStore(cap=400_000)
    _fill(store, 20_000, ids=(0x1A0,))
    t0 = time.monotonic()
    _fill(store, 20_000, ids=(0x1A0,))
    early = time.monotonic() - t0
    _fill(store, 160_000, ids=(0x1A0,))
    t0 = time.monotonic()
    _fill(store, 20_000, ids=(0x1A0,))
    late = time.monotonic() - t0
    assert late < early * 4, f"append cost grew with size: {early:.3f}s -> {late:.3f}s"


def test_state_frames_df_property_round_trip():
    from canlab.core.state import AppState
    st = AppState()
    df = _fill(FrameStore(), 10).materialize()
    st.frames_df = df
    assert len(st.frames_df) == 10
    assert st.get_unique_ids() == ["1A0", "2B0"]
    assert len(st.get_frames_for_id("1A0")) == 5
    st.append_frames(df)
    assert len(st.frames_df) == 20
