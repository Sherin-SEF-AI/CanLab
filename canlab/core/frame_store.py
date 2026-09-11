"""Capped, append-cheap store for captured CAN frames.

The app used to keep frames in a pandas DataFrame and ``pd.concat`` a new batch
onto it during live capture. That copies the whole capture on every batch, so
cost grew with the log and a busy bus eventually froze the UI; nothing bounded
memory either.

This keeps frames in preallocated NumPy columns instead: appending is O(1)
amortised, the buffer is capped (oldest frames are dropped in blocks), and the
DataFrame every consumer expects is materialised lazily and cached until the
next change. Per-ID statistics and lookups are maintained incrementally, so the
ID tree and inspector no longer rescan the whole capture.

The materialised frame keeps the canonical schema:
``Timestamp, ID, Bus, DLC, Extended, B0..B7[..B63], Delta``.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd

from canlab.core.canid import normalize_id

log = logging.getLogger(__name__)

DEFAULT_CAP = 500_000
INITIAL_ROWS = 4096
CLASSIC_WIDTH = 8
FD_WIDTH = 64
_BYTE_RE = re.compile(r"^B(\d+)$")


@dataclass
class IdStats:
    """Running per-ID summary, updated on append (never rescanned)."""
    count: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    dlc: int = 0
    bus: int = 0
    last_index: int = -1

    @property
    def mean_period(self) -> float:
        """Mean seconds between frames (0.0 when only one was seen)."""
        span = self.last_ts - self.first_ts
        return span / (self.count - 1) if self.count > 1 else 0.0

    @property
    def frequency(self) -> float:
        p = self.mean_period
        return 1.0 / p if p > 0 else 0.0


class FrameStore:
    def __init__(self, cap: int = DEFAULT_CAP):
        self._cap = max(1000, int(cap))
        self._lock = threading.RLock()
        self._width = CLASSIC_WIDTH
        self._alloc = 0
        self._n = 0
        self._dropped = 0
        self._id_labels: list[str] = []
        self._id_index: dict[str, int] = {}
        self._bus_labels: list = []
        self._bus_index: dict = {}
        self._stats: dict[str, IdStats] = {}
        self._last_ts_by_code: dict[int, float] = {}
        self._cached: pd.DataFrame | None = None
        self._dirty = True
        self._allocate(INITIAL_ROWS)

    # ── storage ──────────────────────────────────────────────────────────
    def _allocate(self, rows: int) -> None:
        rows = min(max(rows, INITIAL_ROWS), self._cap)
        self._ts = np.zeros(rows, dtype=np.float64)
        self._delta = np.zeros(rows, dtype=np.float64)
        self._id_code = np.zeros(rows, dtype=np.int32)
        self._bus_code = np.zeros(rows, dtype=np.int16)
        self._dlc = np.zeros(rows, dtype=np.int16)
        self._ext = np.zeros(rows, dtype=bool)
        self._data = np.zeros((rows, self._width), dtype=np.uint8)
        self._alloc = rows

    def _grow_or_compact(self) -> None:
        """Make room for one more frame: grow, or drop the oldest quarter."""
        if self._alloc < self._cap:
            new = min(self._alloc * 2, self._cap)
            self._ts = np.resize(self._ts, new)
            self._delta = np.resize(self._delta, new)
            self._id_code = np.resize(self._id_code, new)
            self._bus_code = np.resize(self._bus_code, new)
            self._dlc = np.resize(self._dlc, new)
            self._ext = np.resize(self._ext, new)
            data = np.zeros((new, self._width), dtype=np.uint8)
            data[:self._n] = self._data[:self._n]
            self._data = data
            self._alloc = new
            return
        keep = (self._cap * 3) // 4
        start = self._n - keep
        for arr in (self._ts, self._delta, self._id_code, self._bus_code,
                    self._dlc, self._ext):
            arr[:keep] = arr[start:self._n]
        self._data[:keep] = self._data[start:self._n]
        self._n = keep
        self._dropped += start
        self._reindex_stats()

    def _widen(self, width: int) -> None:
        """Switch to CAN FD width the first time a long frame arrives."""
        if width <= self._width:
            return
        data = np.zeros((self._alloc, FD_WIDTH), dtype=np.uint8)
        data[:self._n, :self._width] = self._data[:self._n]
        self._data = data
        self._width = FD_WIDTH

    def _reindex_stats(self) -> None:
        """After dropping old rows, rebuild the per-ID summaries."""
        self._stats.clear()
        codes = self._id_code[:self._n]
        ts = self._ts[:self._n]
        for code in np.unique(codes):
            where = np.nonzero(codes == code)[0]
            label = self._id_labels[code]
            self._stats[label] = IdStats(
                count=int(where.size),
                first_ts=float(ts[where[0]]),
                last_ts=float(ts[where[-1]]),
                dlc=int(self._dlc[where[-1]]),
                bus=int(self._bus_code[where[-1]]),
                last_index=int(where[-1]),
            )

    def _intern_id(self, label: str) -> int:
        code = self._id_index.get(label)
        if code is None:
            code = len(self._id_labels)
            self._id_labels.append(label)
            self._id_index[label] = code
        return code

    def _intern_bus(self, bus) -> int:
        try:
            bus = int(bus)
        except (TypeError, ValueError):
            bus = str(bus)
        code = self._bus_index.get(bus)
        if code is None:
            code = len(self._bus_labels)
            self._bus_labels.append(bus)
            self._bus_index[bus] = code
        return code

    # ── append ───────────────────────────────────────────────────────────
    def append(self, ts: float, arb_id, extended: bool, bus, dlc: int,
               data: bytes) -> None:
        """Append one frame. ``arb_id`` may be an int or a canonical hex string."""
        with self._lock:
            self._append_locked(ts, arb_id, extended, bus, dlc, data)
            self._dirty = True

    def _append_locked(self, ts, arb_id, extended, bus, dlc, data) -> None:
        if self._n >= self._alloc:
            self._grow_or_compact()
        n = len(data)
        if n > self._width:
            self._widen(FD_WIDTH)
            n = min(n, FD_WIDTH)
        label = arb_id if isinstance(arb_id, str) else normalize_id(arb_id)
        code = self._intern_id(label)
        i = self._n
        ts = float(ts)
        prev = self._last_ts_by_code.get(code)
        self._ts[i] = ts
        self._delta[i] = 0.0 if prev is None else ts - prev
        self._last_ts_by_code[code] = ts
        self._id_code[i] = code
        self._bus_code[i] = self._intern_bus(bus)
        self._dlc[i] = int(dlc)
        self._ext[i] = bool(extended)
        if n:
            self._data[i, :n] = np.frombuffer(data[:n], dtype=np.uint8)
        if n < self._width:
            self._data[i, n:] = 0
        st = self._stats.get(label)
        if st is None:
            self._stats[label] = IdStats(1, ts, ts, int(dlc),
                                         int(self._bus_code[i]), i)
        else:
            st.count += 1
            st.last_ts = ts
            st.dlc = int(dlc)
            st.bus = int(self._bus_code[i])
            st.last_index = i
        self._n += 1

    def append_batch(self, frames) -> int:
        """Append a batch of frames.

        Accepts the receive hub's compact tuples
        ``(timestamp, arb_id, extended, bus, dlc, data)`` or canonical row
        dicts (what the log parsers produce).
        """
        if not frames:
            return 0
        with self._lock:
            for f in frames:
                if isinstance(f, dict):
                    dlc = int(f.get("DLC", 0))
                    data = bytes(int(f[f"B{i}"]) for i in range(dlc)
                                 if f.get(f"B{i}") == f.get(f"B{i}"))
                    self._append_locked(f["Timestamp"], f["ID"],
                                        bool(f.get("Extended", False)),
                                        f.get("Bus", 0), dlc, data)
                else:
                    self._append_locked(*f)
            self._dirty = True
            return len(frames)

    def extend_dataframe(self, df: pd.DataFrame) -> None:
        """Append a parsed capture (canonical schema) to what is already held."""
        if df is None or df.empty:
            return
        byte_cols = sorted((c for c in df.columns if _BYTE_RE.match(str(c))),
                           key=lambda c: int(str(c)[1:]))
        if byte_cols and int(str(byte_cols[-1])[1:]) >= CLASSIC_WIDTH:
            with self._lock:
                self._widen(FD_WIDTH)
        ts = df["Timestamp"].to_numpy(dtype=float)
        ids = df["ID"].to_numpy()
        dlc = (df["DLC"].to_numpy(dtype=int) if "DLC" in df.columns
               else np.full(len(df), len(byte_cols), dtype=int))
        ext = (df["Extended"].to_numpy(dtype=bool) if "Extended" in df.columns
               else np.zeros(len(df), dtype=bool))
        bus = df["Bus"].to_numpy() if "Bus" in df.columns else np.zeros(len(df), dtype=int)
        cols = [np.nan_to_num(df[c].to_numpy(dtype=float), nan=0.0).astype(np.uint8)
                for c in byte_cols]
        with self._lock:
            for i in range(len(df)):
                n = int(dlc[i])
                data = bytes(int(c[i]) for c in cols[:n]) if n else b""
                self._append_locked(ts[i], ids[i], bool(ext[i]), bus[i], n, data)
            self._dirty = True

    def load_dataframe(self, df: pd.DataFrame) -> None:
        """Replace everything with ``df`` (project load / new capture)."""
        self.clear()
        self.extend_dataframe(df)

    def clear(self) -> None:
        with self._lock:
            self._n = 0
            self._dropped = 0
            self._width = CLASSIC_WIDTH
            self._id_labels.clear(); self._id_index.clear()
            self._bus_labels.clear(); self._bus_index.clear()
            self._stats.clear(); self._last_ts_by_code.clear()
            self._allocate(INITIAL_ROWS)
            self._cached = None
            self._dirty = True

    # ── read ─────────────────────────────────────────────────────────────
    def __len__(self) -> int:
        return self._n

    @property
    def dropped(self) -> int:
        """Frames discarded because the cap was reached."""
        return self._dropped

    @property
    def cap(self) -> int:
        return self._cap

    def set_cap(self, cap: int) -> None:
        with self._lock:
            self._cap = max(1000, int(cap))

    def _build(self, idx) -> pd.DataFrame:
        """Materialise the rows at ``idx`` (a slice or an index array)."""
        ts = self._ts[:self._n][idx]
        if ts.size == 0:
            return _empty_frame(self._width)
        codes = self._id_code[:self._n][idx]
        dlc = self._dlc[:self._n][idx].astype(int)
        data = self._data[:self._n][idx]
        labels = np.asarray(self._id_labels, dtype=object)
        bus_labels = np.asarray(self._bus_labels)   # int array for int bus indices
        out = {
            "Timestamp": np.array(ts, dtype=float, copy=True),
            "ID": labels[codes],
            "Bus": bus_labels[self._bus_code[:self._n][idx]],
            "DLC": dlc,
            "Extended": np.array(self._ext[:self._n][idx], copy=True),
        }
        width = max(CLASSIC_WIDTH, int(dlc.max()) if dlc.size else CLASSIC_WIDTH)
        width = min(width, self._width)
        present = np.arange(self._width)[None, :] < dlc[:, None]
        for i in range(width):
            col = data[:, i].astype(np.float64)
            col[~present[:, i]] = np.nan
            out[f"B{i}"] = col
        out["Delta"] = np.array(self._delta[:self._n][idx], dtype=float, copy=True)
        index = (np.arange(self._n)[idx] if not isinstance(idx, slice)
                 else np.arange(self._n)[idx])
        return pd.DataFrame(out, index=pd.Index(index, name=None), copy=False)

    def materialize(self) -> pd.DataFrame:
        """The whole capture as a DataFrame (cached until the next change)."""
        with self._lock:
            if self._dirty or self._cached is None:
                self._cached = self._build(slice(0, self._n))
                self._dirty = False
            return self._cached

    # Handing a worker thread the live frame is what the name makes explicit.
    snapshot = materialize

    def tail(self, n: int) -> pd.DataFrame:
        with self._lock:
            start = max(0, self._n - int(n))
            return self._build(slice(start, self._n))

    def frames_for_id(self, hex_id, tail: int | None = None) -> pd.DataFrame:
        label = hex_id if isinstance(hex_id, str) else normalize_id(hex_id)
        label = normalize_id(label)
        with self._lock:
            code = self._id_index.get(label)
            if code is None or self._n == 0:
                return _empty_frame(self._width)
            where = np.nonzero(self._id_code[:self._n] == code)[0]
            if tail is not None and where.size > tail:
                where = where[-int(tail):]
            return self._build(where)

    def last_frame(self, hex_id) -> dict | None:
        """The most recent frame for one ID, without materialising anything."""
        label = normalize_id(hex_id if isinstance(hex_id, str) else hex_id)
        with self._lock:
            st = self._stats.get(label)
            if st is None or st.last_index < 0:
                return None
            i = st.last_index
            n = int(self._dlc[i])
            row = {
                "Timestamp": float(self._ts[i]),
                "ID": label,
                "Bus": self._bus_labels[int(self._bus_code[i])],
                "DLC": n,
                "Extended": bool(self._ext[i]),
                "Delta": float(self._delta[i]),
            }
            for j in range(self._width):
                row[f"B{j}"] = float(self._data[i, j]) if j < n else float("nan")
            return row

    def unique_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._stats.keys())

    def id_stats(self) -> dict[str, IdStats]:
        with self._lock:
            return dict(self._stats)

    def buses(self) -> list:
        with self._lock:
            return list(self._bus_labels)


def _empty_frame(width: int = CLASSIC_WIDTH) -> pd.DataFrame:
    cols = ["Timestamp", "ID", "Bus", "DLC", "Extended"]
    cols += [f"B{i}" for i in range(max(width, CLASSIC_WIDTH))]
    cols += ["Delta"]
    return pd.DataFrame({c: pd.Series(dtype="float64" if c not in ("ID", "Bus", "Extended") else "object")
                         for c in cols})
