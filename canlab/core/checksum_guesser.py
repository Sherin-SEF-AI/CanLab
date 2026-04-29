"""
Checksum Algorithm Guesser — high-accuracy edition.

Improvements over v1:
  - Minimum 50 frames required before reporting any match
  - Cross-validation: train on 70%, validate on 30% (prevents false positives on small samples)
  - Sample-size penalty: confidence scaled by sqrt(min(n,200)/200) for n < 200
  - Reports sample_size in output so the caller can judge reliability
  - Threshold lowered to 0.75 for detection but final score is cross-val accuracy

Supported algorithms:
  XOR8, SUM8, SUM8_INV, XOR_NIBBLES, NIBBLE_SUM,
  CRC8_SAE (poly 0x1D), CRC8_AUTOSAR (poly 0x2F),
  HYUNDAI_XOR, HYUNDAI_FULL
"""

from typing import Optional
import numpy as np
import pandas as pd

MIN_FRAMES = 50
TRAIN_RATIO = 0.70


# ── CRC tables ────────────────────────────────────────────────────────────────

def _make_crc8_table(poly: int, init: int = 0xFF) -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ poly) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
        table.append(crc)
    return table


_CRC8_SAE_TABLE     = _make_crc8_table(0x1D, 0xFF)
_CRC8_AUTOSAR_TABLE = _make_crc8_table(0x2F, 0xFF)


def _crc8(data: list[int], table: list[int], init: int = 0xFF,
          xor_out: int = 0xFF) -> int:
    crc = init
    for b in data:
        crc = table[(crc ^ b) & 0xFF]
    return crc ^ xor_out


# ── Algorithm implementations ─────────────────────────────────────────────────

def _compute(alg: str, data: list[int], excl: int, msg_id: int = 0) -> int:
    d = [v for i, v in enumerate(data) if i != excl]

    if alg == "XOR8":
        r = 0
        for v in d:
            r ^= v
        return r & 0xFF

    if alg == "SUM8":
        return sum(d) & 0xFF

    if alg == "SUM8_INV":
        return (~sum(d)) & 0xFF

    if alg == "XOR_NIBBLES":
        r = 0
        for v in d:
            r ^= (v & 0x0F) ^ ((v >> 4) & 0x0F)
        return r & 0xFF

    if alg == "NIBBLE_SUM":
        s = 0
        for v in d:
            s += (v & 0x0F) + ((v >> 4) & 0x0F)
        return s & 0xFF

    if alg == "CRC8_SAE":
        return _crc8(d, _CRC8_SAE_TABLE, init=0xFF, xor_out=0xFF)

    if alg == "CRC8_AUTOSAR":
        return _crc8(d, _CRC8_AUTOSAR_TABLE, init=0xFF, xor_out=0xFF)

    if alg == "HYUNDAI_XOR":
        r = 0
        for v in d:
            r ^= v
        r ^= (msg_id >> 4) & 0xFF
        return r & 0xFF

    if alg == "HYUNDAI_FULL":
        r = 0
        for i, v in enumerate(data):
            if i == excl:
                continue
            if i == 0:
                v = v & 0x0F
            r ^= v
        r ^= (msg_id >> 4) & 0xFF
        return r & 0xFF

    return 0


ALL_ALGORITHMS = [
    "XOR8", "SUM8", "SUM8_INV", "XOR_NIBBLES", "NIBBLE_SUM",
    "CRC8_SAE", "CRC8_AUTOSAR", "HYUNDAI_XOR", "HYUNDAI_FULL",
]


# ── Row extraction helper ─────────────────────────────────────────────────────

def _extract_rows(frames: pd.DataFrame) -> list[list[int]]:
    rows = []
    for _, row in frames.iterrows():
        data  = []
        valid = True
        for i in range(8):
            v = row.get(f"B{i}")
            if pd.isna(v):
                valid = False
                break
            data.append(int(v))
        if valid:
            rows.append(data)
    return rows


# ── Main API ──────────────────────────────────────────────────────────────────

def guess_checksum(frames: pd.DataFrame, byte_idx: int,
                   msg_id_hex: str = "000") -> list[dict]:
    """
    Try all checksum algorithms for `byte_idx` in `frames`.

    Requires ≥ MIN_FRAMES rows. Uses 70/30 train/validate split to eliminate
    false positives. Confidence is further scaled by sample size.

    Returns list of matches sorted by confidence (descending):
        [{"algorithm": "XOR8", "confidence": 0.99,
          "train_acc": 1.0, "val_acc": 0.99,
          "sample_size": 200}, ...]
    """
    try:
        msg_id_int = int(msg_id_hex, 16)
    except (ValueError, TypeError):
        msg_id_int = 0

    all_rows = _extract_rows(frames)
    n = len(all_rows)
    if n < MIN_FRAMES:
        return []

    # Deterministic 70/30 split
    split = int(n * TRAIN_RATIO)
    train_rows = all_rows[:split]
    val_rows   = all_rows[split:]

    results = []
    for alg in ALL_ALGORITHMS:
        # Training accuracy
        train_match = sum(
            1 for data in train_rows
            if _compute(alg, data, byte_idx, msg_id_int) == data[byte_idx]
        )
        train_acc = train_match / len(train_rows) if train_rows else 0.0

        if train_acc < 0.75:
            continue

        # Validation accuracy (cross-validate)
        if val_rows:
            val_match = sum(
                1 for data in val_rows
                if _compute(alg, data, byte_idx, msg_id_int) == data[byte_idx]
            )
            val_acc = val_match / len(val_rows)
        else:
            val_acc = train_acc

        # Only pass if validation also high
        if val_acc < 0.75:
            continue

        # Final confidence = geometric mean of train+val, scaled by sample size
        raw_conf  = (train_acc * val_acc) ** 0.5
        size_scale = min(1.0, (n / 200) ** 0.5)
        confidence = round(raw_conf * size_scale, 3)

        results.append({
            "algorithm":   alg,
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
