"""One row per message, showing which bytes just moved and which way.

The frames table is a log: every frame, newest at the bottom, scrolling away
while you read it. That is the wrong shape for the question you actually ask
at the bench, which is "I pressed the button, what changed". SavvyCAN answers
it with its Sniffer window, and Linux answers it with cansniffer: collapse the
bus to one line per arbitration ID and colour the bytes by what they just did.

This is that model, Qt-free. Feed it frames, ask it for a snapshot.

The part that earns its keep is the notch. A running vehicle is never still:
wheel-speed counters tick, a checksum churns, a steering sensor jitters. Notch
records every bit that moved recently and thereafter ignores it, so pressing
notch a few times leaves a display that is quiet, and the bit that lights up
next is the one you caused. Un-notch forgets the mask.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

MAX_BYTES = 64
# SavvyCAN drops an ID from the list once it has been silent this long. A
# message that stopped is information too, so it is greyed for a while first.
DEFAULT_EXPIRY_S = 5.0
# How long a byte keeps its change colour before fading back to steady.
FADE_S = 1.0

UNCHANGED, ROSE, FELL = 0, 1, -1


@dataclass
class SnifferRow:
    can_id: str
    data: list[int]                        # current payload
    direction: list[int]                   # per byte: ROSE, FELL or UNCHANGED
    changed_recently: list[bool]           # within the fade window
    changes: list[int]                     # per byte, how often it has moved
    count: int = 0                         # frames seen
    first_seen: float = 0.0
    last_seen: float = 0.0
    period_s: float = 0.0
    bus: int = 0
    dlc: int = 0

    @property
    def rate_hz(self) -> float:
        return 1.0 / self.period_s if self.period_s > 0 else 0.0

    def age(self, now: float) -> float:
        return max(0.0, now - self.last_seen)

    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)

    def bits(self) -> list[list[int]]:
        """Payload as bits, MSB first per byte, for the bit view."""
        return [[(b >> i) & 1 for i in range(7, -1, -1)] for b in self.data]


@dataclass
class _Track:
    data: list[int] = field(default_factory=list)
    direction: list[int] = field(default_factory=list)
    changed_at: list[float] = field(default_factory=list)
    changes: list[int] = field(default_factory=list)
    notched: list[int] = field(default_factory=list)   # per byte, bits to ignore
    pending: list[int] = field(default_factory=list)   # bits moved since last notch
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    period_s: float = 0.0
    bus: int = 0
    dlc: int = 0

    def _widen(self, n: int) -> None:
        while len(self.data) < n:
            self.data.append(0)
            self.direction.append(UNCHANGED)
            self.changed_at.append(0.0)
            self.changes.append(0)
            self.notched.append(0)
            self.pending.append(0)


class Sniffer:
    """Live per-ID state. Not thread-safe; drive it from one thread."""

    def __init__(self, expiry_s: float = DEFAULT_EXPIRY_S, fade_s: float = FADE_S,
                 live: bool = True):
        self.expiry_s = expiry_s
        self.fade_s = fade_s
        # A live bus stamps frames with wall time, so "now" is wall time and an
        # ID that stops arriving should age out. A loaded log is stamped with
        # the capture's own clock and nothing more is ever going to arrive, so
        # there is no now: the newest frame is the present, and expiry is
        # meaningless. Getting this wrong expires the entire log immediately.
        self.live = live
        self.never_expire = False
        self.mute_notched = False
        self.last_ts = 0.0
        self._tracks: dict[str, _Track] = {}

    def clock(self) -> float:
        """The instant a snapshot should be taken against."""
        return time.time() if self.live else self.last_ts

    # -- feeding ---------------------------------------------------------------
    def update(self, can_id: str, data, ts: float, bus: int = 0, dlc: int | None = None) -> None:
        """One frame. Bytes are compared against the previous frame of this ID."""
        payload = [int(b) & 0xFF for b in data][:MAX_BYTES]
        t = self._tracks.get(can_id)
        if t is None:
            t = _Track(first_seen=ts)
            self._tracks[can_id] = t
        t._widen(len(payload))
        first = t.count == 0
        for i, value in enumerate(payload):
            old = t.data[i]
            if first or value == old:
                continue
            # Only the bits that are not notched count as a change; a byte
            # whose only moving bits are notched stays quiet.
            moved = (old ^ value) & ~t.notched[i] & 0xFF
            t.pending[i] |= (old ^ value) & 0xFF
            if moved:
                t.direction[i] = ROSE if value > old else FELL
                t.changed_at[i] = ts
                t.changes[i] += 1
        t.data[:len(payload)] = payload
        if t.count:
            gap = ts - t.last_seen
            if gap > 0:
                # Exponential mean: a rate that reacts without jittering.
                t.period_s = gap if t.period_s <= 0 else 0.8 * t.period_s + 0.2 * gap
        t.count += 1
        t.last_seen = ts
        self.last_ts = max(self.last_ts, ts)
        t.bus = int(bus or 0)
        t.dlc = int(dlc if dlc is not None else len(payload))

    def update_frame_rows(self, rows) -> int:
        """Feed canonical (ts, id, extended, bus, dlc, data) rows from the hub."""
        n = 0
        for row in rows:
            ts, arb_id, _ext, bus, dlc, data = row
            self.update(_hex_id(arb_id), data, float(ts), bus or 0, dlc)
            n += 1
        return n

    def update_dataframe(self, df) -> int:
        """Feed a DataFrame slice in the canonical frame layout."""
        if df is None or df.empty:
            return 0
        cols = [c for c in df.columns if c.startswith("B") and c[1:].isdigit()]
        cols.sort(key=lambda c: int(c[1:]))
        ts = df["Timestamp"].to_numpy(dtype=float)
        ids = df["ID"].astype(str).to_numpy()
        buses = df["Bus"].to_numpy() if "Bus" in df.columns else [0] * len(df)
        dlcs = df["DLC"].to_numpy() if "DLC" in df.columns else [None] * len(df)
        raw = df[cols].to_numpy() if cols else None
        for i in range(len(df)):
            data = []
            if raw is not None:
                for v in raw[i]:
                    if v != v:            # NaN marks a byte the frame does not carry
                        break
                    data.append(int(v))
            dlc = dlcs[i]
            self.update(ids[i], data, float(ts[i]), int(buses[i] or 0),
                        None if dlc is None or dlc != dlc else int(dlc))
        return len(df)

    # -- notch -----------------------------------------------------------------
    def notch(self) -> int:
        """Ignore every bit that has moved since the last notch. Returns how
        many bits were added to the mask."""
        added = 0
        for t in self._tracks.values():
            for i, bits in enumerate(t.pending):
                new = bits & ~t.notched[i]
                if new:
                    added += bin(new).count("1")
                    t.notched[i] |= new
                t.pending[i] = 0
            t.direction = [UNCHANGED] * len(t.direction)
        return added

    def unnotch(self) -> None:
        for t in self._tracks.values():
            t.notched = [0] * len(t.notched)
            t.pending = [0] * len(t.pending)

    def notched_bits(self) -> int:
        return sum(bin(m).count("1") for t in self._tracks.values() for m in t.notched)

    def clear(self) -> None:
        self._tracks.clear()
        self.last_ts = 0.0

    # -- reading ---------------------------------------------------------------
    def snapshot(self, now: float | None = None) -> list[SnifferRow]:
        """Every live ID, numerically ordered the way SavvyCAN lists them."""
        now = self.clock() if now is None else now
        expires = self.live and not self.never_expire
        out = []
        for can_id, t in self._tracks.items():
            if expires and t.last_seen and now - t.last_seen > self.expiry_s:
                continue
            data = list(t.data)
            if self.mute_notched:
                data = [b & ~m & 0xFF for b, m in zip(data, t.notched)]
            fresh = [(now - c) <= self.fade_s and c > 0 for c in t.changed_at]
            out.append(SnifferRow(
                can_id=can_id, data=data,
                direction=[d if f else UNCHANGED for d, f in zip(t.direction, fresh)],
                changed_recently=fresh, changes=list(t.changes), count=t.count,
                first_seen=t.first_seen, last_seen=t.last_seen,
                period_s=t.period_s, bus=t.bus, dlc=t.dlc))
        out.sort(key=_id_sort_key)
        return out

    def expired(self, now: float | None = None) -> list[str]:
        now = self.clock() if now is None else now
        return [i for i, t in self._tracks.items()
                if t.last_seen and now - t.last_seen > self.expiry_s]

    def __len__(self) -> int:
        return len(self._tracks)


def _id_sort_key(row: SnifferRow):
    try:
        return (0, int(row.can_id, 16))
    except ValueError:
        return (1, 0)


def _hex_id(arb_id) -> str:
    from canlab.core.canid import normalize_id
    return normalize_id(arb_id)
