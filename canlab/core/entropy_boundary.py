"""
Entropy-Based Signal Boundary Detector — high-accuracy edition.

Improvements over v1:
  - Minimum 20 frames required (eliminates noise from rare messages)
  - Both per-bit entropy AND per-bit value range used (catches low-activity signals)
  - Adjacent segments with gap ≤ 3 bits are merged (prevents multi-byte splits)
  - Byte-alignment bonus: segments starting/ending on byte boundaries score higher
  - Confidence score per segment (0–1) combining entropy, range, and alignment
  - Smoothing window raised to 3 for stability
"""

import numpy as np
import pandas as pd

BYTE_COLS = [f"B{i}" for i in range(8)]
MIN_FRAMES = 20


def _bit_entropy(frames: pd.DataFrame) -> np.ndarray:
    """Per-bit Shannon entropy → 64-element array (bit 0 = LSB of B0)."""
    entropies = np.zeros(64)
    for byte_idx, col in enumerate(BYTE_COLS):
        if col not in frames.columns:
            continue
        series = frames[col].dropna().astype(int).values
        if len(series) < 4:
            continue
        for bit in range(8):
            bits = (series >> bit) & 1
            p1 = bits.mean()
            p0 = 1.0 - p1
            if p1 > 0 and p0 > 0:
                entropies[byte_idx * 8 + bit] = -(p1 * np.log2(p1) + p0 * np.log2(p0))
    return entropies


def _bit_range(frames: pd.DataFrame) -> np.ndarray:
    """Per-bit activity: fraction of frames where the bit toggles at all."""
    activity = np.zeros(64)
    for byte_idx, col in enumerate(BYTE_COLS):
        if col not in frames.columns:
            continue
        series = frames[col].dropna().astype(int).values
        if len(series) < 4:
            continue
        for bit in range(8):
            bits = (series >> bit) & 1
            activity[byte_idx * 8 + bit] = float(bits.max() - bits.min())
    return activity


def _smooth(arr: np.ndarray, window: int = 3) -> np.ndarray:
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="same")


def _merge_close_segments(segments: list, max_gap: int = 3) -> list:
    """Merge segments whose gap is ≤ max_gap bits (avoids splitting multi-byte signals)."""
    if not segments:
        return segments
    merged = [segments[0].copy()]
    for seg in segments[1:]:
        prev = merged[-1]
        gap = seg["start_bit"] - (prev["start_bit"] + prev["length"])
        if gap <= max_gap:
            new_end = seg["start_bit"] + seg["length"]
            prev["length"] = new_end - prev["start_bit"]
            prev["mean_entropy"] = round(
                (prev["mean_entropy"] + seg["mean_entropy"]) / 2, 3
            )
            prev["label"] = _bit_label(prev["start_bit"], prev["length"])
        else:
            merged.append(seg.copy())
    return merged


def _confidence(start_bit: int, length: int, mean_entropy: float,
                activity: np.ndarray) -> float:
    """
    Confidence score 0–1 combining:
      - Mean entropy (max 1.0)
      - Byte alignment bonus (+0.15 if starts on byte boundary)
      - Byte alignment bonus (+0.10 if ends on byte boundary)
      - Activity (at least one bit in range had activity=1)
    """
    score = mean_entropy  # 0–1

    if start_bit % 8 == 0:
        score = min(1.0, score + 0.15)
    end_bit = start_bit + length - 1
    if (end_bit + 1) % 8 == 0:
        score = min(1.0, score + 0.10)

    seg_activity = activity[start_bit: start_bit + length]
    if seg_activity.max() > 0:
        score = min(1.0, score + 0.05)

    return round(min(1.0, score), 3)


def detect_signal_boundaries(df: pd.DataFrame,
                              entropy_threshold: float = 0.25,
                              min_length: int = 2) -> dict:
    """
    For each CAN ID with ≥ MIN_FRAMES, find contiguous active bit runs.

    A bit is "active" if its entropy OR its range activity exceeds threshold.

    Returns:
        {
          "260": [
            {"start_bit": 8, "length": 16, "mean_entropy": 0.95,
             "confidence": 0.97, "label": "B1..B2 (16b)"},
          ],
          ...
        }
    """
    results = {}

    for can_id in df["ID"].unique():
        frames = df[df["ID"] == can_id]
        if len(frames) < MIN_FRAMES:
            continue

        ent      = _bit_entropy(frames)
        activity = _bit_range(frames)
        smoothed = _smooth(ent, window=3)

        # A bit is active if entropy or activity exceeds threshold
        above = (smoothed >= entropy_threshold) | (activity >= 1.0)

        segments = []
        i = 0
        while i < 64:
            if above[i]:
                j = i
                while j < 64 and above[j]:
                    j += 1
                length = j - i
                if length >= min_length:
                    mean_ent = float(smoothed[i:j].mean())
                    conf     = _confidence(i, length, mean_ent, activity)
                    segments.append({
                        "start_bit":    i,
                        "length":       length,
                        "mean_entropy": round(mean_ent, 3),
                        "confidence":   conf,
                        "label":        _bit_label(i, length),
                    })
                i = j
            else:
                i += 1

        segments = _merge_close_segments(segments, max_gap=3)

        if segments:
            results[can_id] = segments

    return results


def _bit_label(start_bit: int, length: int) -> str:
    start_byte = start_bit // 8
    end_bit    = start_bit + length - 1
    end_byte   = end_bit // 8
    if start_byte == end_byte:
        lo = start_bit % 8
        hi = end_bit % 8
        return f"B{start_byte}[{lo}:{hi}]"
    return f"B{start_byte}..B{end_byte} ({length}b)"


def suggest_signals(df: pd.DataFrame) -> list[dict]:
    """
    Flat list of signal suggestions across all IDs, sorted by confidence desc.
    Each entry: {"id", "start_bit", "length", "mean_entropy", "confidence", "label"}
    """
    boundary_map = detect_signal_boundaries(df)
    suggestions  = []
    for can_id, segs in boundary_map.items():
        for seg in segs:
            suggestions.append({"id": can_id, **seg})
    suggestions.sort(key=lambda x: x["confidence"], reverse=True)
    return suggestions
