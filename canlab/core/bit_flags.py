"""Find single-bit and small-field flags inside bytes.

Role classification works per byte, and a byte that packs a turn-indicator
bit, a door bit and four bits of padding reads as "STATUS" and stops there.
The signals people most want (indicators, doors, cruise engaged, pedal
switches, gear) are exactly those bits.

A flag has a signature a counter or a measurement does not: it changes rarely
relative to the frame rate, and it holds each state for a run of frames. A bit
of a counter toggles constantly; a bit of a measurement toggles at a rate
depending on its position with no long steady runs. So per bit, count the
transitions and measure how long states are held, and report the bits that
look like switches.

Adjacent flag bits that always change together are reported once as a small
field (a two-bit gear selector, say) rather than as two flags.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

BYTE_COLS = [f"B{i}" for i in range(8)]

MIN_FRAMES = 30
# A bit that never changes is padding; one that changes more often than this
# fraction of frames is a counter or noise, not a switch.
MAX_TOGGLE_RATE = 0.10
# Each state has to be held at least this many frames on average to count as
# something a person or a module set, rather than a bit of a moving value.
MIN_MEAN_RUN = 4


@dataclass
class BitFlag:
    can_id: str
    byte: int
    bit: int                 # LSB = 0, within the byte
    width: int               # 1 for a flag, >1 for a small packed field
    toggles: int             # state changes seen
    frames: int              # frames examined
    mean_run: float          # mean frames per held state
    duty: float              # fraction of frames with the bit set (width 1)
    states: list[int]        # distinct values seen (for fields)
    confidence: float
    label: str

    def as_dict(self) -> dict:
        return asdict(self)


def _bit_series(values: np.ndarray, bit: int) -> np.ndarray:
    return (values >> bit) & 1


def _runs(series: np.ndarray) -> np.ndarray:
    """Lengths of the constant runs in a 0/1 (or small-int) series."""
    if len(series) == 0:
        return np.array([], dtype=int)
    change = np.flatnonzero(np.diff(series) != 0)
    edges = np.concatenate(([0], change + 1, [len(series)]))
    return np.diff(edges)


def _score(toggles: int, n: int, mean_run: float) -> float:
    """Higher for rare, well-held changes; zero for constant or noisy bits."""
    if toggles == 0:
        return 0.0
    rate = toggles / n
    if rate > MAX_TOGGLE_RATE or mean_run < MIN_MEAN_RUN:
        return 0.0
    # Reward long holds and few toggles, cap at 1.
    hold = min(1.0, mean_run / 50.0)
    rarity = 1.0 - rate / MAX_TOGGLE_RATE
    return round(0.5 * hold + 0.5 * rarity, 3)


def detect_flags_for_id(frames: pd.DataFrame, can_id: str) -> list[BitFlag]:
    """Flags and small packed fields in one message's frames, best first."""
    if len(frames) < MIN_FRAMES:
        return []
    found: list[BitFlag] = []
    for byte_idx, col in enumerate(BYTE_COLS):
        if col not in frames.columns:
            continue
        values = frames[col].dropna().astype(int).to_numpy()
        n = len(values)
        if n < MIN_FRAMES:
            continue

        # Per-bit signatures first.
        per_bit = []
        for bit in range(8):
            s = _bit_series(values, bit)
            runs = _runs(s)
            toggles = int(len(runs) - 1)
            mean_run = float(runs.mean()) if len(runs) else float(n)
            per_bit.append((bit, s, toggles, mean_run))

        # A bit above a busy bit in the same byte is a carry of a binary
        # value, not a switch: the top bit of a slowly moving measurement
        # toggles rarely and holds long, exactly like a flag, but the bits
        # below it are churning. A constant bit ends the value, since fields
        # are padded apart with zeros; anything above it is judged afresh.
        in_value = False
        excluded = set()
        for bit, s, toggles, mean_run in per_bit:
            if toggles == 0:
                in_value = False
                continue
            if in_value:
                excluded.add(bit)
            elif toggles / n > MAX_TOGGLE_RATE:
                in_value = True

        # Adjacent flag-like bits form one field when the upper bit only ever
        # changes at a moment the lower one does: that is what binary
        # counting looks like. Two independent flags fail that test.
        used = set()
        for bit, s, toggles, mean_run in per_bit:
            if bit in used or bit in excluded or _score(toggles, n, mean_run) == 0.0:
                continue
            group = [bit]
            lower = set(np.flatnonzero(np.diff(s) != 0).tolist())
            for nxt, s2, t2, r2 in per_bit[bit + 1:]:
                if nxt in used or nxt in excluded or _score(t2, n, r2) == 0.0:
                    break
                upper = set(np.flatnonzero(np.diff(s2) != 0).tolist())
                if upper and len(upper & lower) / len(upper) >= 0.8:
                    group.append(nxt)
                    lower = upper
                else:
                    break
            used.update(group)

            width = len(group)
            lo = group[0]
            mask = (1 << width) - 1
            field = (values >> lo) & mask
            runs = _runs(field)
            toggles = int(len(runs) - 1)
            mean_run = float(runs.mean()) if len(runs) else float(n)
            conf = _score(toggles, n, mean_run)
            if conf == 0.0:
                continue
            states = sorted(int(v) for v in np.unique(field))
            duty = float(field.astype(bool).mean()) if width == 1 else float("nan")
            kind = "flag" if width == 1 else f"{width}-bit field"
            label = (f"{col} bit {lo}" if width == 1
                     else f"{col} bits {lo}..{lo + width - 1}")
            found.append(BitFlag(
                can_id=can_id, byte=byte_idx, bit=lo, width=width,
                toggles=toggles, frames=n, mean_run=round(mean_run, 1),
                duty=round(duty, 3) if width == 1 else duty,
                states=states, confidence=conf,
                label=f"{label} ({kind}, {toggles} changes, {len(states)} states)",
            ))
    found.sort(key=lambda f: (-f.confidence, f.byte, f.bit))
    return found


def detect_flags(df: pd.DataFrame, min_confidence: float = 0.3) -> dict[str, list[BitFlag]]:
    """Every message: {can_id: [BitFlag, ...]} for messages that have any."""
    out: dict[str, list[BitFlag]] = {}
    if df is None or df.empty or "ID" not in df.columns:
        return out
    for can_id, frames in df.groupby("ID", sort=False):
        if not frames["Timestamp"].is_monotonic_increasing:
            frames = frames.sort_values("Timestamp", kind="stable")
        flags = [f for f in detect_flags_for_id(frames, str(can_id))
                 if f.confidence >= min_confidence]
        if flags:
            out[str(can_id)] = flags
    return out


def flag_to_signal(flag: BitFlag, name: str | None = None) -> dict:
    """A DBC signal definition for a detected flag or field."""
    return {
        "message_id": flag.can_id,
        "message_name": f"MSG_{flag.can_id}",
        "signal_name": name or f"FLAG_{flag.can_id}_B{flag.byte}b{flag.bit}",
        "start_bit": flag.byte * 8 + flag.bit,
        "length": flag.width,
        "byte_order": "little",
        "value_type": "unsigned",
        "scale": 1.0, "offset": 0.0,
        "min_val": 0, "max_val": (1 << flag.width) - 1,
        "unit": "", "description": flag.label,
    }
