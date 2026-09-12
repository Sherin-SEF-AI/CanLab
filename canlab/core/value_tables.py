"""Infer value tables for enumerated fields.

A byte that only ever takes the values {0, 1, 2, 4} is a mode selector, not a
measurement: gear position, light state, drive mode. Treating it as a number
with a scale gives a plot that jumps between plateaus and tells you nothing.
The right definition is a value table, and DBC supports those on export
already; nothing infers them.

An enumerated field has few distinct values relative to how many frames it
appears in, holds each value for a run of frames, and its values do not fill a
contiguous range the way a slowly moving measurement's would. Each distinct
value becomes a row with how often and how long it was seen, so the user can
name the ones that matter.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd

BYTE_COLS = [f"B{i}" for i in range(8)]

MIN_FRAMES = 30
MAX_STATES = 16           # more distinct values than this is a measurement
MIN_MEAN_RUN = 3          # frames per held state; a measurement rarely holds


@dataclass
class EnumState:
    value: int
    count: int
    share: float          # fraction of frames
    mean_run: float       # mean consecutive frames holding this value
    first_seen: float     # timestamp
    name: str = ""        # filled in by the user


@dataclass
class EnumField:
    can_id: str
    byte: int
    states: list[EnumState] = field(default_factory=list)
    frames: int = 0
    confidence: float = 0.0

    @property
    def value_table(self) -> dict[int, str]:
        """{value: name} for the DBC VAL_ line; unnamed states get a default."""
        return {s.value: (s.name or f"STATE_{s.value}") for s in self.states}

    def as_dict(self) -> dict:
        d = asdict(self)
        d["value_table"] = self.value_table
        return d


def _runs_by_value(values: np.ndarray) -> dict[int, list[int]]:
    """Lengths of consecutive runs, keyed by the value held."""
    out: dict[int, list[int]] = {}
    if len(values) == 0:
        return out
    start = 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[start]:
            out.setdefault(int(values[start]), []).append(i - start)
            start = i
    return out


def infer_enum_for_byte(frames: pd.DataFrame, can_id: str, byte_idx: int) -> EnumField | None:
    """An EnumField if this byte behaves like an enumeration, else None."""
    col = BYTE_COLS[byte_idx]
    if col not in frames.columns:
        return None
    series = frames[col].dropna()
    n = len(series)
    if n < MIN_FRAMES:
        return None
    values = series.astype(int).to_numpy()
    distinct = np.unique(values)
    if len(distinct) < 2 or len(distinct) > MAX_STATES:
        return None

    runs = _runs_by_value(values)
    mean_run = float(np.mean([r for rs in runs.values() for r in rs]))
    if mean_run < MIN_MEAN_RUN:
        return None                     # changes every frame or two: a value

    # A measurement crawling between two plateaus visits the values between
    # them. An enumeration jumps. Sparse coverage of the range is the tell.
    span = int(distinct.max() - distinct.min()) + 1
    coverage = len(distinct) / span
    sparse = coverage < 0.5 or len(distinct) <= 4
    if len(distinct) > 4 and coverage >= 0.8:
        return None   # 55, 56, 57, 58, 59, 60 is a slow measurement, not modes

    # Confidence: fewer states, longer holds, sparser coverage.
    conf = 0.4 * (1 - len(distinct) / MAX_STATES) \
        + 0.4 * min(1.0, mean_run / 40.0) \
        + 0.2 * (1.0 if sparse else 0.3)
    if conf < 0.35:
        return None

    ts = frames["Timestamp"].to_numpy() if "Timestamp" in frames.columns else None
    states = []
    for v in distinct:
        idx = np.flatnonzero(values == v)
        states.append(EnumState(
            value=int(v), count=int(len(idx)), share=round(len(idx) / n, 3),
            mean_run=round(float(np.mean(runs.get(int(v), [1]))), 1),
            first_seen=float(ts[idx[0]]) if ts is not None else float(idx[0]),
        ))
    states.sort(key=lambda s: -s.count)
    return EnumField(can_id=can_id, byte=byte_idx, states=states, frames=n,
                     confidence=round(conf, 3))


def infer_enums(df: pd.DataFrame, min_confidence: float = 0.35) -> dict[str, list[EnumField]]:
    """Every message: {can_id: [EnumField, ...]} for bytes that enumerate."""
    out: dict[str, list[EnumField]] = {}
    if df is None or df.empty or "ID" not in df.columns:
        return out
    for can_id, frames in df.groupby("ID", sort=False):
        if not frames["Timestamp"].is_monotonic_increasing:
            frames = frames.sort_values("Timestamp", kind="stable")
        found = []
        for byte_idx in range(8):
            enum = infer_enum_for_byte(frames, str(can_id), byte_idx)
            if enum is not None and enum.confidence >= min_confidence:
                found.append(enum)
        if found:
            out[str(can_id)] = found
    return out


def enum_to_signal(enum: EnumField, name: str | None = None) -> dict:
    """A DBC signal definition carrying the inferred value table."""
    return {
        "message_id": enum.can_id,
        "message_name": f"MSG_{enum.can_id}",
        "signal_name": name or f"ENUM_{enum.can_id}_B{enum.byte}",
        "start_bit": enum.byte * 8,
        "length": 8,
        "byte_order": "little",
        "value_type": "unsigned",
        "scale": 1.0, "offset": 0.0,
        "min_val": min(enum.value_table), "max_val": max(enum.value_table),
        "unit": "", "description": f"{len(enum.states)} states observed",
        "value_table": enum.value_table,
    }
