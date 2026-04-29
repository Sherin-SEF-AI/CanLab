"""Compute cycle times for each CAN ID using inter-arrival statistics."""
import numpy as np
import pandas as pd


def compute_periodicity(frames_df: pd.DataFrame) -> dict:
    """Return {id -> cycle_ms} for all IDs with >= 10 frames."""
    result = {}
    if frames_df.empty or "ID" not in frames_df.columns:
        return result

    for can_id, grp in frames_df.groupby("ID"):
        ts = grp["Timestamp"].sort_values().values
        if len(ts) < 10:
            continue
        deltas = np.diff(ts) * 1000.0  # ms
        deltas = deltas[deltas > 0]
        if len(deltas) == 0:
            continue
        # Use median (robust to jitter)
        cycle_ms = float(np.median(deltas))
        result[can_id] = round(cycle_ms, 2)

    return result


def classify_period(cycle_ms: float) -> str:
    if cycle_ms <= 2:
        return "FAST (≤2ms)"
    elif cycle_ms <= 15:
        return "HIGH-FREQ (≤15ms)"
    elif cycle_ms <= 50:
        return "MID-FREQ (≤50ms)"
    elif cycle_ms <= 200:
        return "LOW-FREQ (≤200ms)"
    else:
        return "SLOW (>200ms)"
