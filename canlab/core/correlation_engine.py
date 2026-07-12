"""
Cross-ID byte-level Pearson correlation engine.

For every pair of CAN IDs, aligns their timestamps via nearest-neighbour
matching and computes Pearson r for each byte-to-byte combination.
Results are filtered by a configurable |r| threshold and optionally a
lag sweep (±50 ms) to catch feed-forward / delayed relationships.
"""

import numpy as np
import pandas as pd
from scipy.stats import pearsonr

BYTE_COLS = [f"B{i}" for i in range(8)]
LAG_OFFSETS_MS = [-50, -25, -12, 0, 12, 25, 50]


def _align(s1: np.ndarray, t1: np.ndarray,
           s2: np.ndarray, t2: np.ndarray,
           max_dt: float = 0.1) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-neighbour align s2 onto t1 timestamps."""
    v1, v2 = [], []
    j = 0
    for i in range(len(t1)):
        ts = t1[i]
        while j < len(t2) - 1 and abs(t2[j + 1] - ts) < abs(t2[j] - ts):
            j += 1
        if abs(t2[j] - ts) <= max_dt:
            v1.append(s1[i])
            v2.append(s2[j])
    return np.array(v1, dtype=float), np.array(v2, dtype=float)


def _best_r_with_lag(s1: np.ndarray, t1: np.ndarray,
                     s2: np.ndarray, t2: np.ndarray,
                     base_r: float) -> tuple[float, int]:
    """
    Try each lag offset and return (best_r, best_lag_ms).
    Falls back to (base_r, 0) if no improvement is found.
    """
    best_r   = abs(base_r)
    best_lag = 0
    for lag_ms in LAG_OFFSETS_MS:
        if lag_ms == 0:
            continue
        t2_shifted = t2 + lag_ms / 1000.0
        v1, v2 = _align(s1, t1, s2, t2_shifted)
        if len(v1) < 15:
            continue
        if v1.std() < 0.01 or v2.std() < 0.01:
            continue
        try:
            rl, pl = pearsonr(v1, v2)
            if pl < 0.05 and abs(rl) > best_r:
                best_r   = abs(rl)
                best_lag = lag_ms
        except Exception:
            continue
    return best_r * (1 if base_r >= 0 else -1), best_lag


def correlate_id_pair(frames_df: pd.DataFrame,
                      id1: str, id2: str,
                      min_r: float = 0.75,
                      find_lag: bool = True) -> list[dict]:
    """
    Compute pairwise byte correlations between two CAN IDs.

    Returns list of:
      {"id1": ..., "byte1": ..., "id2": ..., "byte2": ...,
       "r": float, "lag_ms": int, "n": int}
    """
    df1 = frames_df[frames_df["ID"] == id1].sort_values("Timestamp")
    df2 = frames_df[frames_df["ID"] == id2].sort_values("Timestamp")
    if df1.empty or df2.empty:
        return []

    results = []

    for col1 in BYTE_COLS:
        if col1 not in df1.columns:
            continue
        # Drop NaN on (Timestamp, byte) TOGETHER so the value array stays
        # index-aligned with its timestamp array. Dropping only the value series
        # (as before) desynced values from timestamps and overran s1[i] on any
        # short-DLC frame → IndexError / mismatched Pearson r.
        sub1 = df1[["Timestamp", col1]].dropna()
        s1 = sub1[col1].astype(float).values
        t1 = sub1["Timestamp"].values
        if len(s1) < 20 or s1.std() < 0.1:
            continue

        for col2 in BYTE_COLS:
            if col2 not in df2.columns:
                continue
            sub2 = df2[["Timestamp", col2]].dropna()
            s2 = sub2[col2].astype(float).values
            t2 = sub2["Timestamp"].values
            if len(s2) < 20 or s2.std() < 0.1:
                continue

            v1, v2 = _align(s1, t1, s2, t2)
            if len(v1) < 15:
                continue
            if v1.std() < 0.01 or v2.std() < 0.01:
                continue

            try:
                r, p = pearsonr(v1, v2)
            except Exception:
                continue

            if p >= 0.05 or abs(r) < min_r:
                continue

            lag_ms = 0
            if find_lag:
                _, lag_ms = _best_r_with_lag(s1, t1, s2, t2, r)

            results.append({
                "id1":    id1,
                "byte1":  col1,
                "id2":    id2,
                "byte2":  col2,
                "r":      round(r, 3),
                "lag_ms": lag_ms,
                "n":      len(v1),
            })

    return results


def run_correlation_sweep(frames_df: pd.DataFrame,
                          min_r: float = 0.75,
                          max_id_pairs: int = 500,
                          find_lag: bool = True,
                          progress_cb=None) -> list[dict]:
    """
    Sweep all unique CAN ID pairs (up to max_id_pairs).

    progress_cb(done: int, total: int) is called after each pair if provided.
    Returns results sorted by |r| descending.
    """
    ids   = sorted(frames_df["ID"].unique().tolist())
    total = min(len(ids) * (len(ids) - 1) // 2, max_id_pairs)
    checked = 0
    results = []

    for i, id1 in enumerate(ids):
        for id2 in ids[i + 1:]:
            if checked >= max_id_pairs:
                break
            results.extend(
                correlate_id_pair(frames_df, id1, id2, min_r, find_lag)
            )
            checked += 1
            if progress_cb:
                progress_cb(checked, total)

    return sorted(results, key=lambda r: abs(r["r"]), reverse=True)
