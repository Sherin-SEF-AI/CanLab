"""
Signal Value Reverse Lookup.

Given a target physical value V and tolerance, scan all (ID, byte_range)
candidates in the captured frames and score how well each decodes to V.

Algorithm:
  For each (ID, start_bit, length, byte_order) candidate:
    1. Extract raw integer from each frame
    2. Try scale candidates: 1, 0.1, 0.01, 0.5, 0.25, 1/256 with offset 0
    3. Score = fraction of frames where decoded_value is within tolerance of V
    4. Stability = 1 - (std / range) of decoded values (low spread = good signal)
    5. Final score = 0.6 * match_fraction + 0.4 * stability

Returns top candidates sorted by score descending.
"""

import numpy as np
import pandas as pd
from typing import Optional

BYTE_COLS    = [f"B{i}" for i in range(8)]
SCALE_HINTS  = [1.0, 0.5, 0.25, 0.1, 0.01, 1/256, 1/16, 10.0, 100.0]
MIN_FRAMES   = 10
MAX_RESULTS  = 20


def _extract_raw(frames: pd.DataFrame, byte_idx: int, length_bytes: int,
                 big_endian: bool) -> Optional[np.ndarray]:
    """Extract `length_bytes`-wide integer starting at `byte_idx`."""
    cols = [f"B{byte_idx + i}" for i in range(length_bytes)]
    if any(c not in frames.columns for c in cols):
        return None
    sub = frames[cols].dropna()
    if len(sub) < MIN_FRAMES:
        return None
    sub = sub.astype(int)
    if big_endian:
        raw = sum(sub[cols[i]].values * (256 ** (length_bytes - 1 - i))
                  for i in range(length_bytes))
    else:
        raw = sum(sub[cols[i]].values * (256 ** i)
                  for i in range(length_bytes))
    return raw.astype(float)


def _score_candidate(raw_vals: np.ndarray, target: float,
                     scale: float, offset: float,
                     tolerance: float) -> float:
    """Return combined score [0,1] for a (scale, offset) pair."""
    decoded = raw_vals * scale + offset
    match_frac = float((np.abs(decoded - target) <= tolerance).mean())
    if match_frac == 0:
        return 0.0
    val_range = float(decoded.max() - decoded.min())
    val_std   = float(decoded.std())
    stability = 1.0 - (val_std / val_range) if val_range > 1e-6 else 1.0
    return round(0.6 * match_frac + 0.4 * stability, 4)


def find_signal_for_value(df: pd.DataFrame,
                          target: float,
                          tolerance: float = 1.0,
                          max_length_bytes: int = 2) -> list[dict]:
    """
    Scan all IDs and byte positions for signals that decode near `target`.

    Args:
        df:               frames DataFrame (standard B0..B7 columns)
        target:           physical value to search for (e.g. 90.0 for 90°)
        tolerance:        acceptable deviation from target (default ±1.0)
        max_length_bytes: max signal width to try (1 or 2 bytes)

    Returns list of candidates (sorted by score desc):
        [{"id", "byte_idx", "length_bytes", "byte_order", "scale",
          "offset", "score", "sample_median", "sample_n"}, ...]
    """
    results = []

    for can_id in df["ID"].unique():
        frames = df[df["ID"] == can_id]
        if len(frames) < MIN_FRAMES:
            continue

        for byte_idx in range(8):
            for length_bytes in range(1, max_length_bytes + 1):
                if byte_idx + length_bytes > 8:
                    break
                for big_endian in (False, True):
                    raw = _extract_raw(frames, byte_idx, length_bytes, big_endian)
                    if raw is None:
                        continue
                    max_raw = 256 ** length_bytes - 1

                    for scale in SCALE_HINTS:
                        for offset in (0.0, -scale * max_raw / 2):
                            score = _score_candidate(raw, target, scale, offset, tolerance)
                            if score < 0.30:
                                continue
                            decoded_med = float(np.median(raw * scale + offset))
                            results.append({
                                "id":           can_id,
                                "byte_idx":     byte_idx,
                                "length_bytes": length_bytes,
                                "byte_order":   "big" if big_endian else "little",
                                "scale":        round(scale, 6),
                                "offset":       round(offset, 4),
                                "score":        score,
                                "sample_median": round(decoded_med, 3),
                                "sample_n":     len(raw),
                            })

    # Deduplicate: keep best score per (id, byte_idx, length_bytes)
    seen: dict = {}
    for r in results:
        key = (r["id"], r["byte_idx"], r["length_bytes"], r["byte_order"])
        if key not in seen or r["score"] > seen[key]["score"]:
            seen[key] = r

    final = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
    return final[:MAX_RESULTS]
