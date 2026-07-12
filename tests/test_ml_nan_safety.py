"""Short-DLC (NaN byte) inputs must not crash the ML detectors (#10)."""
import pytest

pytest.importorskip("pandas")
pytest.importorskip("numpy")
import numpy as np
import pandas as pd


def _short_dlc_frames():
    # Message with a rolling counter in B0 and a genuinely missing B7 (NaN).
    rows = []
    for i in range(60):
        rows.append({
            "Timestamp": i * 0.01,
            "ID": "0A6",
            "DLC": 7,
            "B0": i & 0x0F, "B1": 0, "B2": 0, "B3": 0,
            "B4": 0, "B5": 0, "B6": 0, "B7": np.nan,
        })
    return pd.DataFrame(rows)


def test_counter_checksum_detector_handles_nan():
    from core.counter_checksum_detector import detect_counters_and_checksums
    result = detect_counters_and_checksums(_short_dlc_frames())   # must not raise
    assert "0A6" in result


def test_correlation_engine_handles_short_dlc():
    from core.correlation_engine import correlate_id_pair
    df = _short_dlc_frames()
    df2 = df.copy()
    df2["ID"] = "260"
    both = pd.concat([df, df2], ignore_index=True)
    # Must not raise IndexError on the NaN B7 column.
    correlate_id_pair(both, "0A6", "260")


def test_isolation_forest_scoring_handles_nan():
    pytest.importorskip("sklearn")
    from core.anomaly_detector import IsolationForestBaseline
    det = IsolationForestBaseline()
    det.fit(_short_dlc_frames())
    if det.is_fitted:
        row = {"B0": 1, "B1": 0, "B2": 0, "B3": 0, "B4": 0, "B5": 0, "B6": 0, "B7": np.nan}
        det.score("0A6", row)   # must not raise "Input contains NaN"
