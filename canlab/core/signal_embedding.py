"""
Feature-vector embedding + cosine-similarity search for CAN IDs.

Each CAN ID is encoded as a 26-dim float vector:
  [0:8]   Shannon entropy per byte (normalised to 0-1)
  [8:16]  Delta-std per byte (normalised)
  [16:24] Unique-value fraction per byte (n_unique / 256)
  [24]    Message frequency (normalised, max 100 Hz)
  [25]    DLC / 8

build_index() → call once per log load.
find_similar() → query any time, O(n_ids) cosine scans.
"""

import numpy as np
import pandas as pd

BYTE_COLS = [f"B{i}" for i in range(8)]
FEATURE_DIM = 26


def _entropy(vals: np.ndarray) -> float:
    counts = np.bincount(vals, minlength=256).astype(float)
    probs  = counts[counts > 0] / counts.sum()
    return float(-np.sum(probs * np.log2(probs + 1e-12)))


def extract_features(frames_df: pd.DataFrame) -> np.ndarray:
    """Build a FEATURE_DIM-dim float vector for one CAN ID's frames."""
    feats: list[float] = []

    # [0:8] Entropy per byte
    for col in BYTE_COLS:
        if col not in frames_df.columns or frames_df[col].dropna().empty:
            feats.append(0.0)
        else:
            vals = frames_df[col].dropna().astype(int).values
            feats.append(min(_entropy(vals) / 8.0, 1.0))

    # [8:16] Delta std per byte
    for col in BYTE_COLS:
        if col not in frames_df.columns or frames_df[col].dropna().empty:
            feats.append(0.0)
        else:
            vals = frames_df[col].dropna().astype(float).values
            std  = float(np.std(np.diff(vals))) if len(vals) > 1 else 0.0
            feats.append(min(std / 128.0, 1.0))

    # [16:24] Unique value fraction per byte
    for col in BYTE_COLS:
        if col not in frames_df.columns or frames_df[col].dropna().empty:
            feats.append(0.0)
        else:
            feats.append(frames_df[col].dropna().nunique() / 256.0)

    # [24] Frequency
    total_t = (frames_df["Timestamp"].iloc[-1]
               - frames_df["Timestamp"].iloc[0])
    freq = len(frames_df) / max(float(total_t), 1e-3)
    feats.append(min(freq / 100.0, 1.0))

    # [25] DLC
    dlc = int(frames_df["DLC"].iloc[0]) if "DLC" in frames_df.columns else 8
    feats.append(dlc / 8.0)

    return np.array(feats, dtype=float)


def build_index(frames_df: pd.DataFrame,
                min_frames: int = 5) -> dict[str, np.ndarray]:
    """Build {id → feature_vector} for all IDs with ≥ min_frames frames."""
    index: dict[str, np.ndarray] = {}
    for can_id, grp in frames_df.groupby("ID"):
        if len(grp) < min_frames:
            continue
        index[can_id] = extract_features(grp)
    return index


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def find_similar(query_id: str,
                 index: dict[str, np.ndarray],
                 top_k: int = 5) -> list[dict]:
    """
    Return the top_k most similar IDs to query_id by cosine similarity.

    [{"id": "2B0", "similarity": 0.94}, ...]
    """
    if query_id not in index:
        return []
    q    = index[query_id]
    sims = [
        {"id": cid, "similarity": round(_cosine(q, vec), 3)}
        for cid, vec in index.items()
        if cid != query_id
    ]
    sims.sort(key=lambda x: x["similarity"], reverse=True)
    return sims[:top_k]
