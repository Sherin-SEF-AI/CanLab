"""Annotated capture: mark when something happened, then rank what tracked it.

This is how signals actually get found. You press the brake and, while you
hold it, the tool knows you are holding it. Afterwards every byte and every
bit on the bus is scored by how well it followed your annotation timeline, and
the ones that followed it are the candidates. Change-on-action compares a
before and an after; this uses the whole timeline, so a signal that goes high
three times while you press three times scores far above one that merely
differed between two windows.

Everything here is Qt-free. Annotations carry the same clock as the frames,
which for live capture is wall time from python-can and for a file is the
capture's own timestamps, so they can be made during a live session or added
afterwards against a loaded log.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

BYTE_COLS = [f"B{i}" for i in range(8)]

# Human timing is not frame timing. Each interval is widened by this much on
# both sides before scoring, so a press that registered a few frames after the
# annotation began still counts. Kept small; it trades a little precision for
# not missing a signal outright.
DEFAULT_TOLERANCE_S = 0.15
MIN_FRAMES_PER_STATE = 5


@dataclass
class Annotation:
    label: str
    start: float
    end: float | None = None            # None while the interval is still open

    @property
    def closed(self) -> bool:
        return self.end is not None

    def contains(self, t: float, tolerance: float = 0.0) -> bool:
        end = self.end if self.end is not None else float("inf")
        return (self.start - tolerance) <= t < (end + tolerance)


@dataclass
class Candidate:
    label: str
    can_id: str
    byte: int
    bit: int | None                     # None means the whole byte
    r: float                            # signed; negative means inverted
    frames_on: int
    frames_off: int
    mean_on: float
    mean_off: float

    @property
    def strength(self) -> float:
        return abs(self.r)

    @property
    def location(self) -> str:
        return f"{self.can_id} B{self.byte}" + (f" bit {self.bit}" if self.bit is not None else "")

    def describe(self) -> str:
        sense = "inverted" if self.r < 0 else "follows"
        if self.bit is None:
            return (f"{self.location}: {sense} '{self.label}', "
                    f"mean {self.mean_off:.1f} off, {self.mean_on:.1f} on")
        return f"{self.location}: {sense} '{self.label}' in {self.strength:.0%} of frames"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["strength"] = self.strength
        d["location"] = self.location
        return d


@dataclass
class AnnotationSet:
    items: list[Annotation] = field(default_factory=list)

    # -- editing -------------------------------------------------------------
    def begin(self, label: str, at: float) -> Annotation:
        """Open an interval. Any open interval with the same label is closed."""
        self.end(label, at)
        a = Annotation(label=label, start=float(at))
        self.items.append(a)
        return a

    def end(self, label: str, at: float) -> None:
        for a in self.items:
            if a.label == label and not a.closed:
                a.end = float(at)

    def add(self, label: str, start: float, end: float) -> Annotation:
        a = Annotation(label, float(start), float(end))
        self.items.append(a)
        return a

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.items):
            self.items.pop(index)

    def clear(self) -> None:
        self.items.clear()

    def labels(self) -> list[str]:
        seen: list[str] = []
        for a in self.items:
            if a.label not in seen:
                seen.append(a.label)
        return seen

    # -- persistence ---------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps([asdict(a) for a in self.items], indent=1)

    @classmethod
    def from_json(cls, text: str) -> "AnnotationSet":
        raw = json.loads(text) if text else []
        return cls([Annotation(**r) for r in raw])

    # -- scoring -------------------------------------------------------------
    def indicator(self, timestamps: np.ndarray, label: str,
                  tolerance: float = DEFAULT_TOLERANCE_S) -> np.ndarray:
        """1 where a frame falls inside any closed interval with this label."""
        ind = np.zeros(len(timestamps), dtype=np.int8)
        for a in self.items:
            if a.label != label or not a.closed:
                continue
            lo, hi = a.start - tolerance, a.end + tolerance
            ind |= ((timestamps >= lo) & (timestamps < hi)).astype(np.int8)
        return ind


def _point_biserial(x: np.ndarray, ind: np.ndarray) -> float:
    """Pearson r between a value and a 0/1 indicator; 0 if degenerate."""
    if x.std() == 0 or ind.std() == 0:
        return 0.0
    return float(np.corrcoef(x, ind)[0, 1])


def rank_candidates(df: pd.DataFrame, annotations: AnnotationSet,
                    label: str | None = None, top: int = 40,
                    tolerance: float = DEFAULT_TOLERANCE_S) -> list[Candidate]:
    """Bytes and bits ranked by how well they tracked the annotation timeline.

    Whole bytes are scored by point-biserial correlation, so a value that is
    higher while the annotation is on scores well however it is encoded. Bits
    are scored by agreement, so a flag that is set exactly while the annotation
    is on scores near 1 and one that is cleared then scores near -1.
    """
    if df is None or df.empty or not annotations.items:
        return []
    labels = [label] if label else annotations.labels()
    out: list[Candidate] = []
    ts_all = df["Timestamp"].to_numpy(dtype=float)

    for lab in labels:
        ind_all = annotations.indicator(ts_all, lab, tolerance)
        if ind_all.sum() < MIN_FRAMES_PER_STATE:
            continue
        for cid, g in df.groupby("ID", sort=False):
            ind = ind_all[g.index.to_numpy()] if g.index.max() < len(ind_all) \
                else annotations.indicator(g["Timestamp"].to_numpy(dtype=float), lab, tolerance)
            n_on, n_off = int(ind.sum()), int(len(ind) - ind.sum())
            if n_on < MIN_FRAMES_PER_STATE or n_off < MIN_FRAMES_PER_STATE:
                continue
            for b, col in enumerate(BYTE_COLS):
                if col not in g.columns:
                    continue
                vals = g[col].to_numpy(dtype=float)
                ok = ~np.isnan(vals)
                if ok.sum() < 2 * MIN_FRAMES_PER_STATE:
                    continue
                v, i = vals[ok], ind[ok]
                if v.std() == 0:
                    continue
                r = _point_biserial(v, i)
                if abs(r) >= 0.3:
                    out.append(Candidate(lab, str(cid), b, None, round(r, 3),
                                         int(i.sum()), int(len(i) - i.sum()),
                                         round(float(v[i == 1].mean()), 2),
                                         round(float(v[i == 0].mean()), 2)))
                vi = v.astype(np.int64)
                for bit in range(8):
                    s = (vi >> bit) & 1
                    if s.min() == s.max():
                        continue
                    agree = float((s == i).mean())
                    r_bit = 2 * agree - 1
                    if abs(r_bit) >= 0.6:
                        out.append(Candidate(lab, str(cid), b, bit, round(r_bit, 3),
                                             int(i.sum()), int(len(i) - i.sum()),
                                             round(float(s[i == 1].mean()), 2),
                                             round(float(s[i == 0].mean()), 2)))
    out.sort(key=lambda c: (-c.strength, c.can_id, c.byte, c.bit if c.bit is not None else -1))
    return out[:top]


def candidate_to_signal(c: Candidate, name: str | None = None) -> dict:
    """A DBC signal for a ranked candidate: one bit, or the whole byte."""
    return {
        "message_id": c.can_id,
        "message_name": f"MSG_{c.can_id}",
        "signal_name": name or (c.label.upper().replace(" ", "_") or "SIGNAL"),
        "start_bit": c.byte * 8 + (c.bit or 0),
        "length": 1 if c.bit is not None else 8,
        "byte_order": "little",
        "value_type": "unsigned",
        "scale": 1.0, "offset": 0.0,
        "min_val": 0, "max_val": 1 if c.bit is not None else 255,
        "unit": "", "description": c.describe(),
    }
