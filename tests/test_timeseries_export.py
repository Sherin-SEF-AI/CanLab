"""Tests for decoded time-series export (#11)."""
import os
import tempfile

import pandas as pd
import pytest

pytest.importorskip("cantools")
from core.timeseries_export import decode_timeseries, export_timeseries


def _frames_and_signals():
    # ID 018: 16-bit little-endian speed at byte 0, scale 0.1.
    rows = []
    for i in range(50):
        raw = i * 10
        rows.append({"Timestamp": i * 0.01, "ID": "018", "Bus": 0, "DLC": 8,
                     "B0": raw & 0xFF, "B1": (raw >> 8) & 0xFF,
                     **{f"B{k}": 0 for k in range(2, 8)}})
    df = pd.DataFrame(rows)
    signals = [{"message_id": "018", "message_name": "SPEED", "msg_length": 8,
                "signal_name": "VehicleSpeed", "start_bit": 0, "length": 16,
                "byte_order": "little", "value_type": "unsigned",
                "scale": 0.1, "offset": 0.0, "min_val": 0, "max_val": 6553,
                "unit": "km/h"}]
    return df, signals


def test_decode_timeseries_wide():
    df, signals = _frames_and_signals()
    out = decode_timeseries(df, signals)
    assert "VehicleSpeed" in out.columns
    assert len(out) == 50
    # raw 490 * 0.1 = 49.0 on the last row
    assert abs(out["VehicleSpeed"].iloc[-1] - 49.0) < 1e-6


def test_export_csv():
    df, signals = _frames_and_signals()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "ts.csv")
        n = export_timeseries(df, signals, path)
        assert n == 50 and os.path.exists(path)
        back = pd.read_csv(path)
        assert "VehicleSpeed" in back.columns
