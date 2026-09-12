"""Guess which algorithm produces a frame's checksum byte.

The algorithms themselves live in :mod:`canlab.core.checksums` (verified
against commaai/opendbc); this module only scores them against real frames,
with a chronological train/validate split so a coincidence on a short capture
does not read as a match.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from canlab.core.checksums import ALGORITHMS

MIN_FRAMES = 50
TRAIN_RATIO = 0.70
MATCH_THRESHOLD = 0.90     # a real checksum matches essentially every frame

BYTE_COLS = [f"B{i}" for i in range(8)]


def _extract_rows(frames: pd.DataFrame) -> np.ndarray:
    """Frames as an (n, 8) uint8 matrix, skipping rows with missing bytes."""
    cols = [c for c in BYTE_COLS if c in frames.columns]
    if len(cols) < 2 or frames.empty:
        return np.empty((0, 0), dtype=np.uint8)
    raw = frames[cols].to_numpy(dtype=float)
    keep = ~np.isnan(raw).any(axis=1)
    return raw[keep].astype(np.uint8)


# ── Main API ──────────────────────────────────────────────────────────────────

def guess_checksum(frames: pd.DataFrame, byte_idx: int,
                   msg_id_hex: str = "000") -> list[dict]:
    """Score every algorithm against ``byte_idx`` of these frames.

    Returns matches sorted by confidence::

        [{"algorithm": "crc8_hyundai", "name": ..., "confidence": 0.99,
          "train_acc": 1.0, "val_acc": 0.99, "sample_size": 200}, ...]
    """
    try:
        msg_id_int = int(msg_id_hex, 16)
    except (ValueError, TypeError):
        msg_id_int = 0

    rows = _extract_rows(frames)
    n = len(rows)
    if n < MIN_FRAMES or byte_idx >= rows.shape[1]:
        return []

    # A byte that never changes cannot be distinguished from padding: a
    # constant zero trivially "matches" XOR over an all-zero payload.
    if len(np.unique(rows[:, byte_idx])) < 2:
        return []

    # Chronological split: a checksum holds over time, a coincidence rarely does.
    split = int(n * TRAIN_RATIO)
    train, validate = rows[:split], rows[split:]

    def accuracy(block) -> float:
        if len(block) == 0:
            return 0.0
        hits = 0
        for data in block:
            payload = bytes(data.tolist())
            expected = payload[byte_idx]
            if algo.width == 4:
                expected &= 0x0F
            if algo.compute(payload, msg_id_int, byte_idx) == expected:
                hits += 1
        return hits / len(block)

    results = []
    for algo in ALGORITHMS.values():
        if algo.fixed_index is not None:
            fixed = algo.fixed_index % rows.shape[1]
            if byte_idx != fixed:
                continue
        train_acc = accuracy(train)
        if train_acc < MATCH_THRESHOLD:
            continue
        val_acc = accuracy(validate) if len(validate) else train_acc
        if val_acc < MATCH_THRESHOLD:
            continue
        confidence = round((train_acc * val_acc) ** 0.5, 3)
        results.append({
            "algorithm":   algo.id,
            "name":        algo.name,
            "confidence":  confidence,
            "train_acc":   round(train_acc, 3),
            "val_acc":     round(val_acc, 3),
            "sample_size": n,
        })

    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results


def guess_all_bytes(frames: pd.DataFrame, msg_id_hex: str = "000") -> dict:
    """
    Run guess_checksum for every byte index (0-7).
    Returns {byte_idx: [matches]} for bytes that have any match.
    """
    results = {}
    for i in range(8):
        matches = guess_checksum(frames, i, msg_id_hex)
        if matches:
            results[i] = matches
    return results
