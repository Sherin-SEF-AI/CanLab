"""Tests for reference-driven calibration (#1)."""
import numpy as np
import pandas as pd
import pytest

from canlab.core.reference_calibrate import calibrate_against_reference, best_signal


def _synth(scale=0.1, big_endian=False, start_byte=2, n=300):
    """A 16-bit field at start_byte encodes speed = raw*scale; build frames+ref."""
    ts = np.arange(n) * 0.01
    speed = np.linspace(0, 60, n)          # km/h reference
    raw = np.round(speed / scale).astype(int)   # what the ECU would transmit
    rows = []
    for i in range(n):
        b = [0] * 8
        b[0] = i & 0xFF                    # unrelated rolling counter
        if big_endian:
            b[start_byte]   = (raw[i] >> 8) & 0xFF
            b[start_byte+1] = raw[i] & 0xFF
        else:
            b[start_byte]   = raw[i] & 0xFF
            b[start_byte+1] = (raw[i] >> 8) & 0xFF
        rows.append({"Timestamp": ts[i], "ID": "0A6", "Bus": 0, "DLC": 8,
                     **{f"B{k}": b[k] for k in range(8)}})
    df = pd.DataFrame(rows)
    return df, ts, speed


def test_recovers_signal_with_high_r2_and_pass():
    # A field that linearly encodes the reference must be found and verified.
    # (Exact start_bit/endianness is intentionally not asserted: a linear signal
    #  read at a shifted offset is still linear, so several fields fit equally —
    #  the meaningful guarantee is a PASS with near-perfect R² on the right ID.)
    df, ref_ts, ref_val = _synth(scale=0.1, big_endian=False, start_byte=2)
    best = best_signal(df, ref_ts, ref_val, min_r2=0.95)
    assert best is not None
    assert best["verdict"] == "PASS"
    assert best["id"] == "0A6"
    assert best["r2"] > 0.99
    assert best["scale"] > 0


def test_big_endian_multibyte_signal_passes():
    df, ref_ts, ref_val = _synth(scale=0.05, big_endian=True, start_byte=3)
    best = best_signal(df, ref_ts, ref_val, min_r2=0.95)
    assert best is not None and best["verdict"] == "PASS"
    assert best["r2"] > 0.99


def test_unconfirmed_when_no_signal_matches():
    # Random reference unrelated to any byte -> no PASS.
    df, ref_ts, _ = _synth()
    rng = np.random.default_rng(0)
    noise = rng.normal(size=len(ref_ts))
    res = calibrate_against_reference(df, ref_ts, noise, min_r2=0.9)
    assert all(c["verdict"] == "UNCONFIRMED" for c in res)


# ── clock offset, de-dup, progress ───────────────────────────────────────────

import os  # noqa: E402

from canlab.core.dbc_manager import decode_frame, validate_signals  # noqa: E402
from canlab.core.log_parser import parse_log_file  # noqa: E402
from canlab.core.reference_calibrate import (  # noqa: E402
    calibrate_many, calibrate_with_lag_search, candidate_to_signal_def, find_time_offset,
)
from canlab.core.reference_series import ReferenceSeries  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "canlab", "sample_data", "sample_kona_drive.csv")


def _walk(n=3000, seed=3, can_id="1B0"):
    """A 16-bit little-endian field at bytes 2-3 following a random walk, so
    the reference is not periodic and a lag has one answer."""
    rng = np.random.default_rng(seed)
    ts = np.arange(n) * 0.01
    walk = np.cumsum(rng.normal(0, 0.3, n))
    walk = walk - walk.min() + 5
    raw = np.round(walk / 0.1).astype(int)
    rows = [{"Timestamp": ts[i], "ID": can_id, "Bus": 0, "DLC": 8,
             "B0": i & 0xFF, "B1": 0, "B2": raw[i] & 0xFF, "B3": (raw[i] >> 8) & 0xFF,
             "B4": 0, "B5": 0, "B6": 0, "B7": 0} for i in range(n)]
    return pd.DataFrame(rows), ts, walk


def test_a_reference_running_3_7_seconds_ahead_is_lined_up():
    df, ts, walk = _walk()
    off = find_time_offset(df, ts[::10] + 3.7, walk[::10], window_s=30.0)
    assert off["lag_s"] == pytest.approx(3.7, abs=0.2)
    assert off["id"] == "1B0" and off["r"] > 0.95
    fitted = calibrate_against_reference(df, ts[::10] + 3.7, walk[::10],
                                         lag_s=off["lag_s"], top_k=1)[0]
    assert fitted["verdict"] == "PASS" and fitted["r2"] > 0.99
    assert (fitted["start_bit"], fitted["length"], fitted["byte_order"]) == (16, 16, "little")


def test_without_the_lag_the_same_reference_does_not_fit():
    df, ts, walk = _walk()
    raw_fit = calibrate_against_reference(df, ts[::10] + 3.7, walk[::10], top_k=1)
    assert not raw_fit or raw_fit[0]["r2"] < 0.9


def test_an_epoch_reference_is_aligned_to_a_capture_that_starts_at_zero():
    df, ts, walk = _walk()
    off = find_time_offset(df, ts[::10] + 1.6e9 + 2.0, walk[::10], window_s=10.0)
    assert off["base_shift_s"] == pytest.approx(1.6e9 + 2.0, abs=0.1)
    assert off["lag_s"] == pytest.approx(1.6e9 + 2.0, abs=0.2)
    best = calibrate_against_reference(df, ts[::10] + 1.6e9 + 2.0, walk[::10],
                                       lag_s=off["lag_s"], top_k=1)[0]
    assert best["r2"] > 0.99


def test_overlapping_wins_collapse_to_one_row_per_field():
    df, ts, walk = _walk()
    loose = calibrate_against_reference(df, ts[::10], walk[::10], dedup=False, top_k=50)
    tight = calibrate_against_reference(df, ts[::10], walk[::10], top_k=50)
    # the same word read from bit 9, 10, ... fits just as well; only one survives
    assert sum(1 for c in loose if c["r2"] > 0.99) > 3
    assert sum(1 for c in tight if c["r2"] > 0.99) == 1
    assert tight[0]["start_bit"] == 16 and tight[0]["length"] == 16


def test_progress_is_reported_per_id_and_stop_is_honoured():
    df, ts, walk = _walk()
    other = df.copy()
    other["ID"] = "2C0"
    both = pd.concat([df, other], ignore_index=True)
    seen = []
    calibrate_against_reference(both, ts[::10], walk[::10],
                                progress_cb=lambda d, t: seen.append((d, t)))
    assert seen[0] == (0, 2) and seen[-1] == (2, 2)
    halted = calibrate_against_reference(both, ts[::10], walk[::10], should_stop=lambda: True)
    assert halted == []


def test_two_references_are_calibrated_independently():
    df, ts, walk = _walk()
    speed = ReferenceSeries("speed", ts[::10] + 1.5, walk[::10], unit="km/h")
    noise = ReferenceSeries("noise", ts[::10], np.random.default_rng(1).normal(size=300))
    ticks = []
    out = calibrate_many(df, [speed, noise], window_s=5.0, top_k=3,
                         progress_cb=lambda d, t: ticks.append((d, t)))
    by_series = {}
    for cand in out:
        by_series.setdefault(cand["series"], []).append(cand)
    assert by_series["speed"][0]["verdict"] == "PASS"
    assert by_series["speed"][0]["lag_s"] == pytest.approx(1.5, abs=0.2)
    assert by_series["speed"][0]["unit"] == "km/h"
    assert all(c["verdict"] == "UNCONFIRMED" for c in by_series.get("noise", []))
    assert ticks[-1] == (200, 200)


def test_a_big_endian_candidate_becomes_a_signal_cantools_decodes():
    df, ref_ts, ref_val = _synth(scale=0.05, big_endian=True, start_byte=3)
    cand = best_signal(df, ref_ts, ref_val, min_r2=0.95)
    assert cand["byte_order"] == "big"
    sig = candidate_to_signal_def(cand, "speed", "km/h")
    assert validate_signals([sig]) == []
    assert sig["start_bit"] == 31                       # MSB of byte 3, DBC Motorola
    row = df.iloc[100]
    frame = bytes(int(row[f"B{i}"]) for i in range(8))
    raw = frame[3] * 256 + frame[4]
    decoded = decode_frame([sig], "0A6", frame)
    assert decoded["speed"] == pytest.approx(raw * cand["scale"] + cand["offset"], rel=1e-6)
    assert sig["unit"] == "km/h" and "PASS" in sig["description"]


def test_the_shipped_sample_gives_wheel_speed_at_one_thirty_second():
    """0x0A6 carries int(speed * 32) big-endian in all four words, with speed
    60 + 20 sin(2 pi t / 5). The reference clock is put one second ahead and
    the window kept under half the period so the lag is unambiguous."""
    df = parse_log_file(SAMPLE)
    t = np.arange(0, 10, 0.1)
    speed = 60 + 20 * np.sin(2 * np.pi * t / 5)
    series = ReferenceSeries("speed", t + 1.0, speed, unit="km/h")
    cands = calibrate_with_lag_search(df, series, window_s=2.0, top_k=8)
    wins = [c for c in cands if c["verdict"] == "PASS"]
    assert len(wins) == 4
    assert {(c["start_bit"], c["length"], c["byte_order"]) for c in wins} == {
        (0, 16, "big"), (16, 16, "big"), (32, 16, "big"), (48, 16, "big")}
    for c in wins:
        assert c["id"] == "0A6" and c["scale"] == pytest.approx(1 / 32)
        assert c["r2"] > 0.99 and c["lag_s"] == pytest.approx(1.0, abs=0.2)


def test_a_small_scale_is_not_rounded_to_zero():
    """Scales were rounded to six decimal places, so a field in 1e-7 degrees,
    a common GPS encoding, came back with scale 0.0."""
    n = 400
    ts = np.arange(n) * 0.05
    lat = 42.66 + np.cumsum(np.random.default_rng(4).normal(0, 2e-5, n))
    raw = np.round(lat / 1e-7).astype(np.int64)
    rows = [{"Timestamp": ts[i], "ID": "3F0", "Bus": 0, "DLC": 8,
             **{f"B{k}": int((int(raw[i]) >> (8 * k)) & 0xFF) if k < 4 else 0 for k in range(8)}}
            for i in range(n)]
    (best,) = calibrate_against_reference(pd.DataFrame(rows), ts, lat, widths=(16,), top_k=1)
    assert (best["start_bit"], best["length"], best["byte_order"]) == (0, 16, "little")
    assert best["scale"] == pytest.approx(1e-7, rel=1e-6)       # was 0.0
    assert best["r2"] > 0.999


def test_a_native_unit_is_written_into_the_dbc_signal():
    cand = {"id": "0AA", "start_bit": 0, "length": 16, "byte_order": "big",
            "scale": 0.002777778, "offset": -18.66, "r2": 0.9985, "n": 2479,
            "verdict": "PASS", "unit": "m/s", "series": "speed",
            "native_unit": "km/h", "native_scale": 0.01, "native_offset": -67.18}
    sig = candidate_to_signal_def(cand, "WHEEL_SPEED_FR")
    assert (sig["scale"], sig["offset"], sig["unit"]) == (0.01, -67.18, "km/h")
    assert "fitted against a reference in m/s" in sig["description"]
