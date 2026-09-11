"""
Counter / Checksum Auto-Detector.

For each message ID in a DataFrame:
  - Counter bytes: value increments by 1 each frame, wraps at a power-of-2 boundary
    (0x0F nibble counter, 0xFF byte counter, or upper/lower nibble)
  - Checksum bytes: value can be reproduced from the other bytes using a known algorithm
    (XOR8, SUM8 mod 256, XOR of nibbles, Hyundai-style XOR)

Returns a dict: {can_id: {"counters": [...], "checksums": [...]}}
Each entry is a dict with byte index, confidence, and detected algorithm/wrap.
"""

import numpy as np

# A counter is allowed to miss some increments (dropped frames, resets); 2-bit
# counters only advance a quarter of the time, so the old 0.85 bar excluded
# every counter narrower than a nibble.
COUNTER_MATCH = 0.70
import pandas as pd
from typing import Optional

BYTE_COLS = [f"B{i}" for i in range(8)]


# ── Counter detection ─────────────────────────────────────────────────────────

def _detect_counter_byte(series: pd.Series) -> Optional[dict]:
    """
    Return counter info if this byte series looks like a rolling counter, else None.
    Checks full-byte counter (0-255) and nibble counters (0-15 in upper/lower nibble).
    """
    vals = series.dropna().astype(int).values
    if len(vals) < 8:
        return None

    results = []

    # Full byte counter 0-255
    diffs = np.diff(vals)
    wrap_mask = (vals[1:] == 0) & (vals[:-1] > 200)
    inc_mask  = (diffs == 1) | wrap_mask
    inc_rate  = inc_mask.mean()
    if inc_rate > COUNTER_MATCH:
        results.append({"type": "byte_counter", "wrap": 256, "confidence": round(inc_rate, 3)})

    # Lower nibble counter 0-15
    lo = vals & 0x0F
    diffs_lo = np.diff(lo)
    wrap_lo  = (lo[1:] == 0) & (lo[:-1] == 15)
    inc_lo   = ((diffs_lo == 1) | wrap_lo).mean()
    if inc_lo > COUNTER_MATCH:
        results.append({"type": "nibble_lo_counter", "wrap": 16, "confidence": round(inc_lo, 3)})

    # Upper nibble counter 0-15
    hi = (vals >> 4) & 0x0F
    diffs_hi = np.diff(hi)
    wrap_hi  = (hi[1:] == 0) & (hi[:-1] == 15)
    inc_hi   = ((diffs_hi == 1) | wrap_hi).mean()
    if inc_hi > COUNTER_MATCH:
        results.append({"type": "nibble_hi_counter", "wrap": 16, "confidence": round(inc_hi, 3)})

    if not results:
        return None
    return max(results, key=lambda x: x["confidence"])


# ── Checksum detection ────────────────────────────────────────────────────────

def _detect_checksums_vectorized(frames: pd.DataFrame, msg_id_int: int = 0,
                                 min_conf: float = 0.90) -> list[dict]:
    """Vectorized replacement for per-byte _detect_checksum_byte over one ID.

    Builds the (N,8) byte matrix once and computes every algorithm for every
    byte with numpy, instead of iterrows × algorithms × bytes. XOR is its own
    inverse and SUM/NIBBLE_SUM are cumulative, so "checksum over the other 7
    bytes" is (total ⊕/− this byte) — no Python per-row loop needed.
    """
    cols = [c for c in BYTE_COLS if c in frames.columns]
    if len(cols) < 2:
        return []
    mat = frames[BYTE_COLS].to_numpy(dtype=np.float64)   # NaN for missing bytes
    valid = ~np.isnan(mat).any(axis=1)
    mat = mat[valid].astype(np.int64)
    n = len(mat)
    if n < 5:
        return []

    total_xor = np.zeros(n, dtype=np.int64)
    for k in range(8):
        total_xor ^= mat[:, k]
    total_sum = mat.sum(axis=1)
    nib = (mat & 0x0F) + ((mat >> 4) & 0x0F)
    total_nib = nib.sum(axis=1)
    hy_const = (msg_id_int >> 4) & 0xFF

    # Per byte, per algorithm: how often the relation holds. `spread` counts
    # every byte the relation holds for, including ones rejected as candidates
    # below, because that is what says whether the relation localises anything.
    scores: dict[str, dict[int, float]] = {}
    spread: dict[str, int] = {}
    for k in range(8):
        col = mat[:, k]
        # A checksum has to be worth more than a guess. Score each candidate
        # against the best constant predictor -- the byte's most common value.
        # A constant byte has a baseline of 1.0 and can never be beaten, which
        # is the right answer: padding is not a checksum.
        baseline = float(np.bincount(col, minlength=256).max()) / n
        floor = max(min_conf, baseline)
        algos = {
            "XOR8":       (total_xor ^ col),
            "SUM8":       ((total_sum - col) & 0xFF),
            "NIBBLE_SUM": ((total_nib - nib[:, k]) & 0xFF),
        }
        if msg_id_int > 0:
            algos["HYUNDAI_XOR"] = ((total_xor ^ col) ^ hy_const)
        for name, expected in algos.items():
            conf = float(np.mean(expected == col))
            if conf > min_conf:
                spread[name] = spread.get(name, 0) + 1
            if conf > floor:
                scores.setdefault(name, {})[k] = conf

    # Some relations are message-wide identities that say nothing about which
    # byte is the checksum. "Is byte k the XOR of the other seven?" is the same
    # question as "does the whole message XOR to zero?" -- the answer does not
    # depend on k, so a real XOR checksum and a payload that simply repeats
    # each value an even number of times both make all eight bytes match. When
    # a relation holds across most of the payload it has no localising power,
    # so drop it rather than report eight checksums or guess at one.
    localising = {name: hits for name, hits in scores.items()
                  if spread.get(name, 0) <= len(cols) // 2}

    # What is left may still name the same byte twice (a trailing SUM8 also
    # makes XOR8 hold on two bytes). Prefer the most specific relation: the one
    # that matched the fewest bytes, then the most confident. Where a relation
    # did match more than one byte, take the last -- a trailing checksum is the
    # near-universal convention.
    best_per_byte: dict[int, tuple] = {}
    for name, hits in localising.items():
        k = max(hits)
        rank = (spread[name], -hits[k])
        if k not in best_per_byte or rank < best_per_byte[k][0]:
            best_per_byte[k] = (rank, {"byte": k, "col": f"B{k}",
                                       "algorithm": name,
                                       "confidence": round(hits[k], 3)})
    return [v[1] for _, v in sorted(best_per_byte.items())]


# ── Main API ──────────────────────────────────────────────────────────────────

def detect_counters_and_checksums(df: pd.DataFrame) -> dict:
    """
    Analyse every message ID in df.

    Returns:
        {
          "0A6": {
            "counters":  [{"byte": 0, "type": "nibble_hi_counter", "wrap": 16, "confidence": 0.99}],
            "checksums": [{"byte": 7, "algorithm": "XOR8", "confidence": 0.97}],
          },
          ...
        }
    """
    results = {}
    for can_id in df["ID"].unique():
        frames = df[df["ID"] == can_id]
        if len(frames) < 5:
            continue
        # Increment detection compares consecutive rows, so the frames have to
        # be in time order — concatenated captures are not.
        if not frames["Timestamp"].is_monotonic_increasing:
            frames = frames.sort_values("Timestamp", kind="stable")
        frames = frames.copy()

        counters  = []
        checksums = []

        try:
            mid_int = int(can_id, 16)
        except (ValueError, TypeError):
            mid_int = 0

        for i, col in enumerate(BYTE_COLS):
            if col not in frames.columns:
                continue
            series = frames[col].dropna()
            if series.empty:
                continue
            ctr = _detect_counter_byte(series)
            if ctr:
                counters.append({"byte": i, "col": col, **ctr})

        # Checksums for all 8 bytes in one vectorized pass (was iterrows per byte).
        checksums = _detect_checksums_vectorized(frames, mid_int)

        if counters or checksums:
            results[can_id] = {"counters": counters, "checksums": checksums}

    return results
