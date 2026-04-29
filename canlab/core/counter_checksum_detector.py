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
    if inc_rate > 0.85:
        results.append({"type": "byte_counter", "wrap": 256, "confidence": round(inc_rate, 3)})

    # Lower nibble counter 0-15
    lo = vals & 0x0F
    diffs_lo = np.diff(lo)
    wrap_lo  = (lo[1:] == 0) & (lo[:-1] == 15)
    inc_lo   = ((diffs_lo == 1) | wrap_lo).mean()
    if inc_lo > 0.85:
        results.append({"type": "nibble_lo_counter", "wrap": 16, "confidence": round(inc_lo, 3)})

    # Upper nibble counter 0-15
    hi = (vals >> 4) & 0x0F
    diffs_hi = np.diff(hi)
    wrap_hi  = (hi[1:] == 0) & (hi[:-1] == 15)
    inc_hi   = ((diffs_hi == 1) | wrap_hi).mean()
    if inc_hi > 0.85:
        results.append({"type": "nibble_hi_counter", "wrap": 16, "confidence": round(inc_hi, 3)})

    if not results:
        return None
    return max(results, key=lambda x: x["confidence"])


# ── Checksum detection ────────────────────────────────────────────────────────

def _xor8(data: list[int], exclude_idx: int) -> int:
    result = 0
    for i, v in enumerate(data):
        if i != exclude_idx:
            result ^= v
    return result & 0xFF


def _sum8(data: list[int], exclude_idx: int) -> int:
    return sum(v for i, v in enumerate(data) if i != exclude_idx) & 0xFF


def _hyundai_xor(data: list[int], exclude_idx: int, msg_id: int) -> int:
    """Hyundai/Kia: XOR of bytes[0:7] XOR (msg_id >> 8) XOR nibble magic."""
    chk = 0
    for i, v in enumerate(data):
        if i != exclude_idx:
            chk ^= v
    chk ^= (msg_id >> 4) & 0xFF
    return chk & 0xFF


def _nibble_sum(data: list[int], exclude_idx: int) -> int:
    """Sum of all nibbles mod 16, placed in lower nibble."""
    total = 0
    for i, v in enumerate(data):
        if i != exclude_idx:
            total += (v & 0x0F) + ((v >> 4) & 0x0F)
    return total & 0xFF


_ALGORITHMS = {
    "XOR8":        _xor8,
    "SUM8":        _sum8,
    "NIBBLE_SUM":  _nibble_sum,
}


def _detect_checksum_byte(frames: pd.DataFrame, byte_idx: int,
                           msg_id_int: int = 0) -> Optional[dict]:
    """
    For a given byte index, test if it matches any checksum algorithm
    computed over the other bytes.
    """
    if frames.empty or len(frames) < 5:
        return None

    candidates = []

    for alg_name, alg_fn in _ALGORITHMS.items():
        match_count = 0
        total       = 0
        for _, row in frames.iterrows():
            data = []
            valid = True
            for i in range(8):
                v = row.get(f"B{i}")
                if pd.isna(v):
                    valid = False
                    break
                data.append(int(v))
            if not valid:
                continue
            expected = alg_fn(data, byte_idx)
            actual   = data[byte_idx]
            if expected == actual:
                match_count += 1
            total += 1

        if total == 0:
            continue
        conf = match_count / total
        if conf > 0.90:
            candidates.append({"algorithm": alg_name, "confidence": round(conf, 3)})

    # Also try Hyundai-specific with msg_id
    if msg_id_int > 0:
        match_count = 0
        total       = 0
        for _, row in frames.iterrows():
            data = [int(row.get(f"B{i}", 0) or 0) for i in range(8)]
            expected = _hyundai_xor(data, byte_idx, msg_id_int)
            if expected == data[byte_idx]:
                match_count += 1
            total += 1
        if total and match_count / total > 0.90:
            candidates.append({
                "algorithm": "HYUNDAI_XOR",
                "confidence": round(match_count / total, 3),
            })

    if not candidates:
        return None
    return max(candidates, key=lambda x: x["confidence"])


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
        frames = df[df["ID"] == can_id].copy()
        if len(frames) < 5:
            continue

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

            chk = _detect_checksum_byte(frames, i, mid_int)
            if chk:
                checksums.append({"byte": i, "col": col, **chk})

        if counters or checksums:
            results[can_id] = {"counters": counters, "checksums": checksums}

    return results
