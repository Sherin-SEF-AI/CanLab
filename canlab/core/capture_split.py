"""Cut a capture down to the part that matters.

A five-minute drive is half a million frames and you want the four seconds
around the door unlock. Every analysis in the application runs over whatever
is loaded, so trimming first makes the detectors faster and their output
shorter, and it makes the file you hand someone else the size of the evidence
rather than the size of the drive.

SavvyCAN calls this the Bisector and splits by frame number, percentage, ID
range or bus. Same four here, plus a time window, which is the one people
reach for when they have annotated the capture or read a timestamp off the
frames table.

Qt-free: the dialog picks the arguments, this decides the rows.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from canlab.core.canid import normalize_id

MODES = ("time", "frames", "percent", "ids", "bus")


@dataclass
class SplitResult:
    kept: pd.DataFrame
    dropped: pd.DataFrame
    description: str

    @property
    def n_kept(self) -> int:
        return len(self.kept)

    @property
    def n_dropped(self) -> int:
        return len(self.dropped)

    def summary(self) -> str:
        total = self.n_kept + self.n_dropped
        share = (self.n_kept / total * 100) if total else 0.0
        ids = self.kept["ID"].nunique() if self.n_kept else 0
        span = 0.0
        if self.n_kept:
            span = float(self.kept["Timestamp"].max() - self.kept["Timestamp"].min())
        return (f"{self.n_kept} of {total} frames ({share:.1f}%), {ids} IDs, "
                f"{span:.2f} s: {self.description}")


def _mask_to_result(df: pd.DataFrame, mask: np.ndarray, description: str,
                    invert: bool) -> SplitResult:
    if invert:
        mask = ~mask
        description = f"everything except {description}"
    kept = df[mask]
    dropped = df[~mask]
    # A trimmed capture is a capture in its own right, so the row numbering
    # starts again; leaving the old index makes every downstream .iloc lie.
    return SplitResult(kept.reset_index(drop=True), dropped.reset_index(drop=True),
                       description)


def split_by_time(df: pd.DataFrame, start_s: float, end_s: float,
                  *, relative: bool = True, invert: bool = False) -> SplitResult:
    """Frames inside a time window. Relative means seconds from the first frame,
    which is what the frames table and the annotations show."""
    ts = df["Timestamp"].to_numpy(dtype=float)
    base = float(ts.min()) if relative and len(ts) else 0.0
    lo, hi = base + float(start_s), base + float(end_s)
    if hi < lo:
        lo, hi = hi, lo
    return _mask_to_result(df, (ts >= lo) & (ts <= hi),
                           f"{start_s:g} s to {end_s:g} s"
                           + ("" if relative else " (absolute)"), invert)


def split_by_frames(df: pd.DataFrame, first: int, last: int,
                    *, invert: bool = False) -> SplitResult:
    """Frames by position, counted from 0 and inclusive at both ends."""
    n = len(df)
    lo, hi = sorted((max(0, int(first)), min(n - 1, int(last)))) if n else (0, -1)
    idx = np.arange(n)
    return _mask_to_result(df, (idx >= lo) & (idx <= hi),
                           f"frames {lo} to {hi}", invert)


def split_by_percent(df: pd.DataFrame, first_pct: float, last_pct: float,
                     *, invert: bool = False) -> SplitResult:
    """A percentage slice, for chopping a capture in half without arithmetic."""
    n = len(df)
    lo_pct, hi_pct = sorted((float(first_pct), float(last_pct)))
    lo = int(round(n * max(0.0, lo_pct) / 100.0))
    hi = int(round(n * min(100.0, hi_pct) / 100.0)) - 1
    idx = np.arange(n)
    return _mask_to_result(df, (idx >= lo) & (idx <= max(lo, hi)),
                           f"{lo_pct:g}% to {hi_pct:g}%", invert)


def split_by_ids(df: pd.DataFrame, ids, *, invert: bool = False) -> SplitResult:
    """Only these arbitration IDs. Accepts a list, or "100-1FF" style ranges."""
    wanted = _expand_ids(ids)
    if not wanted:
        return _mask_to_result(df, np.zeros(len(df), dtype=bool), "no IDs selected", invert)
    mask = df["ID"].astype(str).isin(wanted).to_numpy()
    shown = ", ".join(sorted(wanted)[:6]) + (" …" if len(wanted) > 6 else "")
    return _mask_to_result(df, mask, f"{len(wanted)} ID(s): {shown}", invert)


def split_by_bus(df: pd.DataFrame, bus: int, *, invert: bool = False) -> SplitResult:
    if "Bus" not in df.columns:
        return _mask_to_result(df, np.ones(len(df), dtype=bool),
                               "no Bus column, kept everything", invert)
    mask = (df["Bus"].fillna(-1).astype(int) == int(bus)).to_numpy()
    return _mask_to_result(df, mask, f"bus {int(bus)}", invert)


def _expand_ids(ids) -> set[str]:
    """"100, 1A0-1A4, 0x200" into a set of canonical hex IDs."""
    if isinstance(ids, str):
        parts = [p for p in ids.replace(";", ",").split(",") if p.strip()]
    else:
        parts = list(ids)
    out: set[str] = set()
    for part in parts:
        text = str(part).strip()
        if not text:
            continue
        if "-" in text[1:]:
            lo_s, _, hi_s = text.partition("-")
            try:
                lo = int(lo_s.strip().lower().replace("0x", ""), 16)
                hi = int(hi_s.strip().lower().replace("0x", ""), 16)
            except ValueError:
                continue
            if hi < lo:
                lo, hi = hi, lo
            # A wide range is a mistake, not a request for a million entries.
            for v in range(lo, min(hi, lo + 0x20000) + 1):
                out.add(normalize_id(v))
        else:
            # normalize_id upper-cases whatever it is handed rather than
            # raising, so the value has to be checked as hex here or a typo
            # silently becomes an arbitration ID that matches nothing.
            try:
                int(text.lower().replace("0x", ""), 16)
            except ValueError:
                continue
            out.add(normalize_id(text))
    return out


def split(df: pd.DataFrame, mode: str, **kw) -> SplitResult:
    """Dispatch by mode name, for the dialog and the command line."""
    if df is None or df.empty:
        empty = pd.DataFrame(columns=getattr(df, "columns", []))
        return SplitResult(empty, empty, "nothing loaded")
    fn = {"time": split_by_time, "frames": split_by_frames, "percent": split_by_percent,
          "ids": split_by_ids, "bus": split_by_bus}.get(mode)
    if fn is None:
        raise ValueError(f"unknown split mode {mode!r}; use one of {', '.join(MODES)}")
    return fn(df, **kw)
