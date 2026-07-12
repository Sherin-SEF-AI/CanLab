"""
Signal analyzer — high-accuracy edition.

compute_correlation_matrix improvements:
  - Spearman rank correlation (catches monotonic nonlinear relationships)
  - Mutual information via sklearn (catches any statistical dependency including bitfields)
  - Combined dependency score = max(|pearson|, |spearman|, MI_normalized)
  - Returns combined score matrix, not just Pearson

_classify improvements:
  - Checksum detection: low entropy relative to other bytes
  - Status flag vs packed bitfield distinction
  - PERIODIC classification based on inter-frame timing variance
"""

import numpy as np
import pandas as pd
from scipy.stats import entropy as scipy_entropy, spearmanr

try:
    from sklearn.metrics import mutual_info_score as _mi_score
    _SKLEARN = True
except ImportError:
    _SKLEARN = False


BYTE_COLS = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]


def compute_byte_entropy(series: pd.Series) -> float:
    series = series.dropna().astype(int)
    if len(series) < 2:
        return 0.0
    counts = np.bincount(series, minlength=256)
    probs  = counts / counts.sum()
    probs  = probs[probs > 0]
    return float(-np.sum(probs * np.log2(probs)))


def analyze_id(frames: pd.DataFrame) -> dict:
    if frames.empty:
        return {}

    frames = frames.sort_values("Timestamp")
    total_time = frames["Timestamp"].iloc[-1] - frames["Timestamp"].iloc[0]
    freq = len(frames) / total_time if total_time > 0 else 0.0

    # Inter-frame timing variance (low = periodic, high = event-driven)
    if len(frames) > 2:
        iats = np.diff(frames["Timestamp"].values)
        iat_cv = float(np.std(iats) / np.mean(iats)) if np.mean(iats) > 0 else 0.0
    else:
        iat_cv = 0.0

    stats = {"freq": freq, "iat_cv": iat_cv, "frame_count": len(frames), "bytes": {}}

    byte_entropies = []
    for col in BYTE_COLS:
        if col not in frames.columns:
            continue
        series = frames[col].dropna()
        if series.empty:
            continue
        ent = compute_byte_entropy(series)
        byte_entropies.append(ent)
        s = series.astype(float)
        stats["bytes"][col] = {
            "min":         float(s.min()),
            "max":         float(s.max()),
            "mean":        float(s.mean()),
            "range":       float(s.max() - s.min()),
            "entropy":     ent,
            "change_rate": float((s.diff() != 0).sum() / max(len(s) - 1, 1)),
            "unique":      int(s.nunique()),
        }

    stats["mean_entropy"]  = float(np.mean(byte_entropies)) if byte_entropies else 0.0
    stats["suspected_type"] = _classify(stats, frames)
    return stats


def _classify(stats: dict, frames: pd.DataFrame) -> str:
    freq   = stats.get("freq", 0)
    iat_cv = stats.get("iat_cv", 1.0)

    if freq < 1.0:
        return "DIAGNOSTIC"

    byte_stats = stats.get("bytes", {})
    entropies  = [b["entropy"] for b in byte_stats.values()]
    if not entropies:
        return "UNKNOWN"

    mean_ent = np.mean(entropies)

    # Counter: one byte has near-linear increment pattern
    for col, bstats in byte_stats.items():
        series = frames[col].dropna().astype(float)
        if len(series) < 3:
            continue
        diffs = series.diff().dropna()
        if (diffs == 1).mean() > 0.70:
            return "COUNTER"
        lo = series.astype(int) & 0x0F
        if (lo.diff().dropna() == 1).mean() > 0.70:
            return "COUNTER"

    # Checksum: one byte has low entropy while others are high
    if len(entropies) >= 2:
        for col, bstats in byte_stats.items():
            others = [e for c, e in zip(byte_stats.keys(), entropies) if c != col]
            if bstats["entropy"] < 1.5 and np.mean(others) > 3.0:
                return "CHECKSUM"

    # Sensor: at least one byte has high entropy + large range
    for col, bstats in byte_stats.items():
        if bstats["entropy"] > 3.0 and bstats["range"] > 50:
            return "SENSOR"

    # Status flag: many bytes with range ≤ 1
    flag_count = sum(1 for b in byte_stats.values() if b["range"] <= 1 and b["max"] <= 1)
    if flag_count >= 4:
        return "STATUS_FLAG"

    # Packed bitfield: moderate entropy, many unique values per byte
    packed = sum(1 for b in byte_stats.values() if 1 < b["entropy"] < 4 and b["unique"] > 4)
    if packed >= 3:
        return "PACKED_BITFIELD"

    # Periodic vs event-driven
    if iat_cv < 0.15:
        return "PERIODIC"

    return "UNKNOWN"


def analyze_all(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    records = []
    for can_id in df["ID"].unique():
        id_frames = df[df["ID"] == can_id]
        stats = analyze_id(id_frames)
        if not stats:
            continue

        row = {
            "ID":            can_id,
            "Bus":           id_frames["Bus"].iloc[0] if "Bus" in id_frames.columns else "0",
            "Freq_Hz":       round(stats["freq"], 2),
            "Frames":        stats["frame_count"],
            "Entropy":       round(stats["mean_entropy"], 2),
            "SuspectedType": stats["suspected_type"],
        }
        for col in BYTE_COLS:
            if col in stats["bytes"]:
                b = stats["bytes"][col]
                row[f"{col}_range"] = f"{int(b['min'])}-{int(b['max'])}"
            else:
                row[f"{col}_range"] = "-"
        records.append(row)

    return pd.DataFrame(records)


def compute_timing_dependency_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    ID×ID *message-timing* dependency matrix.

    NOTE: this correlates each ID's frame-*arrival* pattern (a histogram of when
    its frames occur, binned across the capture window) — NOT the byte/signal
    *values*. It surfaces IDs that tend to be transmitted together in time. For
    byte-level value correlation, use core.correlation_engine instead.

    Each cell = max(|Pearson|, |Spearman|, MI_normalized) of the two IDs' timing
    histograms.
    """
    if df.empty:
        return pd.DataFrame()

    ids        = df["ID"].unique()
    time_index = np.linspace(df["Timestamp"].min(), df["Timestamp"].max(), 1000)
    series_map = {}

    for can_id in ids:
        id_frames = df[df["ID"] == can_id].sort_values("Timestamp")
        if len(id_frames) < 2:
            continue
        change_ts = id_frames["Timestamp"].values[1:]
        counts = np.zeros(len(time_index))
        for t in change_ts:
            idx = np.searchsorted(time_index, t)
            if idx < len(counts):
                counts[idx] += 1
        series_map[can_id] = counts

    if not series_map:
        return pd.DataFrame()

    id_list = list(series_map.keys())
    n       = len(id_list)
    matrix  = np.zeros((n, n))

    for i in range(n):
        matrix[i, i] = 1.0
        a = series_map[id_list[i]]
        for j in range(i + 1, n):
            b = series_map[id_list[j]]
            score = _dependency_score(a, b)
            matrix[i, j] = score
            matrix[j, i] = score

    return pd.DataFrame(matrix, index=id_list, columns=id_list)


# Backwards-compatible alias. The old name implied signal-value correlation,
# which this never computed; kept so existing callers keep working.
compute_correlation_matrix = compute_timing_dependency_matrix


def _dependency_score(a: np.ndarray, b: np.ndarray) -> float:
    """Max of |Pearson|, |Spearman|, MI_normalized."""
    scores = []

    # Pearson
    if a.std() > 0 and b.std() > 0:
        pearson = float(np.corrcoef(a, b)[0, 1])
        scores.append(abs(pearson) if not np.isnan(pearson) else 0.0)

    # Spearman (rank-based, catches monotonic nonlinear)
    try:
        spear, _ = spearmanr(a, b)
        scores.append(abs(float(spear)) if not np.isnan(spear) else 0.0)
    except Exception:
        pass

    # Mutual information (catches any statistical dependency)
    if _SKLEARN:
        try:
            # Discretize to 16 bins for MI
            a_d = np.digitize(a, np.linspace(a.min(), a.max() + 1e-9, 17)) - 1
            b_d = np.digitize(b, np.linspace(b.min(), b.max() + 1e-9, 17)) - 1
            mi  = _mi_score(a_d, b_d)
            # Normalize MI by max possible (entropy of uniform 16-bin)
            mi_norm = min(1.0, mi / np.log2(16))
            scores.append(mi_norm)
        except Exception:
            pass

    return round(max(scores) if scores else 0.0, 3)
