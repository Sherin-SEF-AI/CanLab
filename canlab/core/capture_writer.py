"""Write frames to disk as they arrive, in files of a bounded size.

A logger left in a car for a day cannot hold the day in memory and cannot
write one file that only becomes readable when the run ends cleanly. The
segment writer appends SavvyCAN CSV rows to the current file, flushes on a
short clock, and starts a new file when the current one has enough frames or
has covered enough time. Each segment is a complete capture the desktop and
the CLI open on their own; the kit also folds them into one project later.

The row format is the one ``canlab-cli convert`` writes, through the same
function, so both stay byte for byte the same.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

SAVVYCAN_HEADER = "Time Stamp,ID,Extended,Dir,Bus,LEN,D1,D2,D3,D4,D5,D6,D7,D8\n"


def format_savvycan_row(ts: float, arb: int, extended: bool, bus, dlc: int,
                        data: bytes) -> str:
    """One SavvyCAN CSV line: microsecond timestamp, 8-digit hex ID, two-digit
    hex bytes for the ``dlc`` bytes present, empty cells after, and the
    trailing comma SavvyCAN writes."""
    cells = [f"{b:02X}" for b in bytes(data)[:8]]
    cells += [""] * (8 - len(cells))
    try:
        bus_no = int(bus or 0)
    except (TypeError, ValueError):
        bus_no = 0
    return (f"{int(round(float(ts) * 1e6))},{int(arb):08X},"
            f"{'true' if extended else 'false'},Rx,{bus_no},{int(dlc)},"
            f"{','.join(cells)},\n")


class SegmentWriter:
    """Append rows to numbered CSV segments, rotating by count or by time."""

    def __init__(self, directory, prefix: str = "capture", *,
                 max_frames: int = 200_000, max_seconds: float = 600.0,
                 max_bytes: int | None = None, flush_every_s: float = 1.0):
        self.directory = Path(directory)
        self.prefix = prefix or "capture"
        self.max_frames = max(1, int(max_frames))
        self.max_seconds = float(max_seconds) if max_seconds else 0.0
        self.max_bytes = int(max_bytes) if max_bytes else 0
        self.flush_every_s = float(flush_every_s)
        self.segments: list[Path] = []
        self.frames = 0                 # every frame written, all segments
        self._fh = None
        self._index = 0
        self._seg_frames = 0
        self._seg_bytes = 0
        self._seg_t0: float | None = None
        self._last_flush = time.monotonic()

    @property
    def current(self) -> Path | None:
        return self.segments[-1] if self._fh is not None else None

    def _open(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._index += 1
        path = self.directory / f"{self.prefix}-{self._index:04d}.csv"
        self._fh = open(path, "w", newline="")
        self._fh.write(SAVVYCAN_HEADER)
        self.segments.append(path)
        self._seg_frames = 0
        self._seg_bytes = len(SAVVYCAN_HEADER)
        self._seg_t0 = None

    def _full(self, ts: float) -> bool:
        if self._seg_frames >= self.max_frames:
            return True
        if self.max_seconds and self._seg_t0 is not None and ts - self._seg_t0 >= self.max_seconds:
            return True
        return bool(self.max_bytes and self._seg_bytes >= self.max_bytes)

    def write_rows(self, rows: Iterable[tuple]) -> int:
        """Rows are the hub's tuples ``(ts, arb, extended, bus, dlc, data)``."""
        n = 0
        for ts, arb, extended, bus, dlc, data in rows:
            if self._fh is None or self._full(ts):
                self.rotate()
            line = format_savvycan_row(ts, arb, extended, bus, dlc, data)
            self._fh.write(line)
            self._seg_bytes += len(line)
            self._seg_frames += 1
            self.frames += 1
            if self._seg_t0 is None:
                self._seg_t0 = float(ts)
            n += 1
        if self._fh is not None and time.monotonic() - self._last_flush >= self.flush_every_s:
            self.flush()
        return n

    def flush(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            try:
                os.fsync(self._fh.fileno())
            except OSError:
                pass
        self._last_flush = time.monotonic()

    def rotate(self) -> Path | None:
        """Close the current segment (if any) and open the next."""
        closed = self.current
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._open()
        return closed

    def close(self) -> list[Path]:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        # an empty last segment is nobody's capture
        if self.segments and self._seg_frames == 0:
            try:
                self.segments[-1].unlink()
            except OSError:
                pass
            self.segments.pop()
        return list(self.segments)


def iter_segments(paths: Iterable[os.PathLike | str]) -> Iterator[pd.DataFrame]:
    """The segments as DataFrames, one at a time, in the order given."""
    from canlab.core.log_parser import parse_savvycan_csv
    for p in paths:
        df = parse_savvycan_csv(str(p))
        if not df.empty:
            yield df
