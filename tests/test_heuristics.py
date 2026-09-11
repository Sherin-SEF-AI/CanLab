"""The analysis heuristics, on messages whose structure is known by construction.

Each test builds a frame stream where the right answer is not in doubt, which
is what the previous versions of these rules got wrong: the checksum rule
picked the lowest-entropy byte (a checksum is high-entropy), and any message
containing a rolling counter was labelled COUNTER regardless of its payload.
"""
import numpy as np
import pandas as pd
import pytest

from canlab.core.checksums import hyundai_crc8
from canlab.core.correlation_engine import _align
from canlab.core.counter_checksum_detector import detect_counters_and_checksums
from canlab.core.signal_analyzer import analyze_id
from canlab.core.signal_classifier import classify_message_type


def sensor_message(n=300, can_id="1A0", counter=True, checksum=True):
    """B0: rolling counter, B1-B2: a moving sensor value, B3: status, B7: CRC."""
    rows = []
    for i in range(n):
        d = [0] * 8
        if counter:
            d[0] = (i & 0x0F) << 4
        value = int(600 + 300 * np.sin(i / 20))
        d[1], d[2] = (value >> 8) & 0xFF, value & 0xFF
        d[3] = 1
        if checksum:
            d[7] = hyundai_crc8(bytes(d[:7]))
        rows.append({"Timestamp": i * 0.01, "ID": can_id,
                     **{f"B{k}": d[k] for k in range(8)}})
    return pd.DataFrame(rows)


def test_a_counter_byte_does_not_make_the_whole_message_a_counter():
    stats = analyze_id(sensor_message())
    assert stats["suspected_type"] == "SENSOR"


def test_a_message_that_is_only_a_counter_still_reads_as_one():
    rows = [{"Timestamp": i * 0.01, "ID": "555",
             **{f"B{k}": ((i & 0x0F) << 4 if k == 0 else 0) for k in range(8)}}
            for i in range(200)]
    assert analyze_id(pd.DataFrame(rows))["suspected_type"] == "COUNTER"


def test_counter_and_checksum_bytes_are_located():
    found = detect_counters_and_checksums(sensor_message())
    assert "1A0" in found
    counters = {c["byte"] for c in found["1A0"]["counters"]}
    assert 0 in counters, "the rolling counter in B0 was missed"


def test_narrow_counters_are_detected():
    """A 2-bit counter advances a quarter as often; the old 0.85 bar missed it."""
    rows = [{"Timestamp": i * 0.01, "ID": "300",
             **{f"B{k}": (i % 4 if k == 0 else 7) for k in range(8)}}
            for i in range(200)]
    found = detect_counters_and_checksums(pd.DataFrame(rows))
    assert found.get("300", {}).get("counters"), "2-bit counter not detected"


def test_detection_works_on_out_of_order_frames():
    """Concatenated captures are not time-ordered; diffing them raw is wrong."""
    df = sensor_message(n=200)
    shuffled = pd.concat([df.iloc[100:], df.iloc[:100]], ignore_index=True)
    found = detect_counters_and_checksums(shuffled)
    assert 0 in {c["byte"] for c in found["1A0"]["counters"]}


def test_cyclic_message_survives_a_single_dropout():
    """One gap used to inflate the standard deviation enough to relabel a
    strictly cyclic message as event-driven."""
    ts = list(np.arange(0, 3.0, 0.01))
    ts = [t for t in ts if not (1.0 < t < 1.5)]        # a 500 ms dropout
    df = pd.DataFrame([{"Timestamp": t, "ID": "1A0",
                        **{f"B{k}": 0 for k in range(8)}} for t in ts])
    result = classify_message_type(df)
    assert result["type"] == "CYCLIC", result


def test_event_driven_message_is_not_called_cyclic():
    rng = np.random.default_rng(3)
    t = np.cumsum(rng.exponential(0.2, 200))
    df = pd.DataFrame([{"Timestamp": float(x), "ID": "1A0",
                        **{f"B{k}": 0 for k in range(8)}} for x in t])
    assert classify_message_type(df)["type"] != "CYCLIC"


def test_alignment_matches_nearest_timestamps():
    t1 = np.array([0.0, 0.1, 0.2, 0.3])
    s1 = np.array([1.0, 2.0, 3.0, 4.0])
    t2 = np.array([0.01, 0.11, 0.55])          # the third is too far away
    s2 = np.array([10.0, 20.0, 30.0])
    v1, v2 = _align(s1, t1, s2, t2, max_dt=0.05)
    assert v1.tolist() == [1.0, 2.0]
    assert v2.tolist() == [10.0, 20.0]


def test_alignment_handles_empty_input():
    empty = np.empty(0)
    v1, v2 = _align(empty, empty, np.array([1.0]), np.array([0.0]))
    assert len(v1) == 0 and len(v2) == 0


def test_correlated_signals_are_found_across_ids():
    from canlab.core.correlation_engine import correlate_id_pair
    rows = []
    for i in range(300):
        v = int(128 + 100 * np.sin(i / 15))
        rows.append({"Timestamp": i * 0.01, "ID": "100",
                     **{f"B{k}": (v if k == 1 else 0) for k in range(8)}})
        rows.append({"Timestamp": i * 0.01 + 0.001, "ID": "200",
                     **{f"B{k}": (v if k == 3 else 0) for k in range(8)}})
    hits = correlate_id_pair(pd.DataFrame(rows), "100", "200")
    assert hits, "a perfectly correlated byte pair was not found"
    best = max(hits, key=lambda h: abs(h["r"]))
    assert abs(best["r"]) > 0.95
    assert (best["byte1"], best["byte2"]) == ("B1", "B3")


def test_bus_off_counts_only_the_bus_off_error_class():
    from canlab.core.bus_health import BusHealthMeter
    meter = BusHealthMeter()
    meter.add_frame(8, 0.0, "", is_error=True, error_class=0x40)   # CAN_ERR_BUSOFF
    meter.add_frame(8, 0.1, "", is_error=True, error_class=0x04)   # a plain error
    snap = meter.snapshot()
    assert snap["error_frames"] == 2 and snap["bus_off"] == 1


def test_auto_dbc_drafts_signals_without_inventing_oem_names():
    from canlab.core.auto_dbc import build_from_analyzer
    from canlab.core.state import AppState
    st = AppState()
    st.frames_df = sensor_message(can_id="0A6")
    signals = build_from_analyzer(st)
    assert signals, "no candidate signals proposed"
    names = {s["signal_name"] for s in signals}
    assert not any("WHL_SPD" in n or "SAS" in n for n in names), names
    assert all(s["message_id"] == "0A6" for s in signals)
    # the varying payload bytes should be proposed, not just byte 0
    starts = {s["start_bit"] for s in signals}
    assert starts != {0}
    assert all(s["scale"] == 1.0 for s in signals), "scale must not be guessed"


@pytest.mark.parametrize("bits", [4, 8])
def test_counter_wrap_is_not_read_as_a_break(bits):
    mask = (1 << bits) - 1
    rows = [{"Timestamp": i * 0.01, "ID": "400",
             **{f"B{k}": ((i & mask) if k == 0 else 0) for k in range(8)}}
            for i in range(400)]
    found = detect_counters_and_checksums(pd.DataFrame(rows))
    assert found.get("400", {}).get("counters")


# ── Checksum false positives ─────────────────────────────────────────────────
# Three ways the detector used to invent checksums. On the sample capture it
# reported 44 checksum bytes across 10 messages; the truth is 8.

def _frame_rows(payloads, can_id="200"):
    return pd.DataFrame([
        {"Timestamp": i * 0.01, "ID": can_id,
         **{f"B{k}": d[k] for k in range(8)}}
        for i, d in enumerate(payloads)])


def test_padding_is_not_reported_as_a_checksum():
    """Bytes that never change match everything and mean nothing.

    XOR over an all-zero payload "predicts" every zero byte perfectly, which
    used to come back as eight checksums on a message that has none.
    """
    found = detect_counters_and_checksums(_frame_rows(
        [[i & 0xFF, 1, 0, 0, 0, 0, 0, 1] for i in range(200)]))
    cols = {c["col"] for c in found.get("200", {}).get("checksums", [])}
    assert not cols & {"B2", "B3", "B4", "B5", "B6"}, (
        f"constant padding reported as a checksum: {sorted(cols)}")


def test_a_repeated_payload_is_not_reported_as_a_checksum():
    """The real 0A6 case: a 16-bit value repeated four times.

    Every byte value then appears an even number of times, so the message XORs
    to zero and each byte equals the XOR of the other seven. That is an
    identity, not a checksum, and the message has no checksum at all.
    """
    payloads = []
    for i in range(300):
        raw = 1920 + 640 * i % 0xFFFF
        hi, lo = (raw >> 8) & 0xFF, raw & 0xFF
        payloads.append([hi, lo] * 4)
    found = detect_counters_and_checksums(_frame_rows(payloads, "0A6"))
    assert not found.get("0A6", {}).get("checksums"), (
        "a payload that repeats itself is not checksummed")


def test_a_real_sum_checksum_is_found_and_named():
    """And the detector must still find a genuine one, at the right byte."""
    payloads = []
    for i in range(300):
        d = [(i & 0x0F) << 4, i & 0xFF, (i * 7) & 0xFF, 3, 0, 0, 0, 0]
        d[7] = sum(d[:7]) & 0xFF
        payloads.append(d)
    checks = detect_counters_and_checksums(_frame_rows(payloads))["200"]["checksums"]
    assert [(c["col"], c["algorithm"]) for c in checks] == [("B7", "SUM8")]


def test_the_sample_capture_yields_one_checksum_per_protected_message():
    """End to end on the shipped sample, against what the generator wrote."""
    from pathlib import Path

    from canlab.core.log_parser import parse_log_file

    sample = (Path(__file__).resolve().parent.parent
              / "canlab" / "sample_data" / "sample_kona_drive.csv")
    df = parse_log_file(str(sample))
    found = detect_counters_and_checksums(df)
    cols = [f"B{k}" for k in range(8)]
    for can_id in df["ID"].unique():
        data = df[df["ID"] == can_id][cols].to_numpy(int)
        # The generator's rule: B7 = sum(B0..B6) & 0xFF, or no checksum.
        protected = bool(((data[:, :7].sum(1) & 0xFF) == data[:, 7]).all())
        got = [(c["col"], c["algorithm"])
               for c in found.get(can_id, {}).get("checksums", [])]
        assert got == ([("B7", "SUM8")] if protected else []), (
            f"{can_id}: expected {'B7 SUM8' if protected else 'nothing'}, got {got}")
