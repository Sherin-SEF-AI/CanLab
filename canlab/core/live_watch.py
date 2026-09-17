"""Watch a live bus against a baseline and say when it leaves it.

The anomaly detector was fitted and scored offline: load a capture, fit,
score every frame, read a table. Nothing looked at the bus as it ran, and the
``anomaly_detected`` signal had no sender. This module is the sender. It
holds a fitted ``ZScoreBaseline`` and is handed each batch of new frames as
the store receives them; it returns events, and the tab decides what to do
with them (a table row, a status bar flash, a mark on the timeline).

Four kinds of event:

``bytes``    a frame's payload sits far from the baseline for its ID,
             at most one per ID per cooldown so a stuck value reports once,
             not at every frame.
``new_id``   an ID the baseline never saw, once.
``burst``    an ID arriving far faster than its fitted period.
``silent``   an ID that stopped arriving, once, re-armed when it returns.

Time is the frame clock, never the wall clock, so a loaded log replays the
same events every time and a test can be exact. ``now`` defaults to the
newest timestamp in the batch. Nothing here transmits.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from canlab.core.anomaly_detector import ZScoreBaseline, byte_matrix

KINDS = ("bytes", "silent", "burst", "new_id")


@dataclass
class WatchEvent:
    ts: float
    can_id: str
    kind: str
    score: float
    detail: str = ""
    bus: int = 0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"ts": round(float(self.ts), 6), "id": self.can_id, "kind": self.kind,
                "score": round(float(self.score), 4), "detail": self.detail,
                "bus": int(self.bus)}


class LiveWatch:
    """Fit once, then observe batch after batch."""

    def __init__(self, *, threshold: float = 0.6, cooldown_s: float = 2.0,
                 sigma: float = 4.0, silent_factor: float = 5.0,
                 min_silent_s: float = 1.0, burst_factor: float = 3.0,
                 min_burst: int = 10, max_events: int = 5000):
        self.threshold = float(threshold)
        self.cooldown_s = float(cooldown_s)
        self.silent_factor = float(silent_factor)
        self.min_silent_s = float(min_silent_s)
        self.burst_factor = float(burst_factor)
        self.min_burst = int(min_burst)
        self.baseline = ZScoreBaseline(threshold_sigma=sigma)
        self._events: deque[WatchEvent] = deque(maxlen=int(max_events))
        self._fit_info: dict = {}
        self.clear()

    # ── fitting ──────────────────────────────────────────────────────────────

    def fit(self, frames_df: pd.DataFrame) -> dict:
        """Fit the baseline on a clean stretch; returns what it learned."""
        self.baseline.fit(frames_df)
        span = 0.0
        if len(frames_df):
            ts = pd.to_numeric(frames_df["Timestamp"], errors="coerce")
            span = float(ts.max() - ts.min())
        self._fit_info = {"ids": len(self.baseline.fitted_ids()),
                          "frames": int(len(frames_df)), "span_s": round(span, 3)}
        self.clear()
        return dict(self._fit_info)

    @property
    def is_fitted(self) -> bool:
        return self.baseline.is_fitted

    def clear(self) -> None:
        """Forget what has been observed, keep the baseline."""
        self._last_seen: dict[str, float] = {}
        self._last_event: dict[tuple[str, str], float] = {}
        self._silent: set[str] = set()
        self._new_ids: set[str] = set()
        self._now: float | None = None
        self._observed_frames = 0
        self._batches = 0
        self._by_kind = {k: 0 for k in KINDS}
        self._events.clear()

    def reset_events(self) -> None:
        self._events.clear()
        self._by_kind = {k: 0 for k in KINDS}

    # ── observing ────────────────────────────────────────────────────────────

    def observe(self, frames_df: pd.DataFrame, now: float | None = None) -> list[WatchEvent]:
        """Score a batch of new frames; return the events it raised, in time order."""
        if not self.is_fitted:
            return []
        events: list[WatchEvent] = []
        n = len(frames_df)
        if n:
            ts_all = pd.to_numeric(frames_df["Timestamp"], errors="coerce").to_numpy(dtype=float)
            if now is None:
                now = float(np.nanmax(ts_all))
            ids = frames_df["ID"].astype(str).to_numpy()
            buses = (pd.to_numeric(frames_df["Bus"], errors="coerce").fillna(0).to_numpy(dtype=int)
                     if "Bus" in frames_df.columns else np.zeros(n, dtype=int))
            mat = byte_matrix(frames_df)
            for can_id in pd.unique(ids):
                where = np.flatnonzero(ids == can_id)
                events.extend(self._observe_id(str(can_id), ts_all[where], mat[where],
                                               int(buses[where[0]])))
            self._observed_frames += n
            self._batches += 1
        if now is None:
            now = self._now
        if now is not None:
            self._now = now
            events.extend(self._check_silence(now))
        events.sort(key=lambda e: e.ts)
        for ev in events:
            self._events.append(ev)
            self._by_kind[ev.kind] = self._by_kind.get(ev.kind, 0) + 1
        return events

    def _observe_id(self, can_id: str, ts: np.ndarray, mat: np.ndarray,
                    bus: int) -> list[WatchEvent]:
        out: list[WatchEvent] = []
        first, last = float(np.nanmin(ts)), float(np.nanmax(ts))
        if can_id in self._silent:
            self._silent.discard(can_id)          # back: re-armed for next time
        was_seen = can_id in self._last_seen
        self._last_seen[can_id] = max(last, self._last_seen.get(can_id, last))

        if can_id not in self.baseline._mean:
            if can_id not in self._new_ids:
                self._new_ids.add(can_id)
                out.append(WatchEvent(first, can_id, "new_id", 1.0,
                                      "not in the baseline", bus))
            return out

        scores = self.baseline.score_matrix(can_id, mat)
        hits = np.flatnonzero(scores >= self.threshold)
        if len(hits) and self._ready(can_id, "bytes", float(ts[hits[0]])):
            worst = int(hits[np.argmax(scores[hits])])
            devs = self.baseline.byte_deviations(can_id, mat[worst])[:3]
            detail = ", ".join(f"B{i}={int(v)} (z {z:+.1f})" for i, v, z in devs)
            if len(hits) > 1:
                detail += f"; {len(hits)} frames in this batch"
            out.append(WatchEvent(float(ts[hits[0]]), can_id, "bytes",
                                  float(scores[worst]), detail, bus,
                                  {"frames": int(len(hits))}))

        period = self.baseline.period_stats(can_id)
        if period and period["median_dt"] > 0 and was_seen:
            expected = (last - first) / period["median_dt"] + 1.0
            count = len(ts)
            if count >= self.min_burst and count > self.burst_factor * expected \
                    and self._ready(can_id, "burst", last):
                rate = count / max(last - first, period["median_dt"])
                out.append(WatchEvent(first, can_id, "burst",
                                      min(1.0, count / (self.burst_factor * expected)),
                                      f"{count} frames in {last - first:.2f} s, about "
                                      f"{rate:.0f}/s against a fitted "
                                      f"{1 / period['median_dt']:.0f}/s", bus,
                                      {"frames": count}))
        return out

    def _check_silence(self, now: float) -> list[WatchEvent]:
        out: list[WatchEvent] = []
        for can_id, last in self._last_seen.items():
            if can_id in self._silent:
                continue
            period = self.baseline.period_stats(can_id)
            if not period:
                continue
            limit = max(self.silent_factor * period["median_dt"], self.min_silent_s)
            gap = now - last
            if gap > limit:
                self._silent.add(can_id)
                out.append(WatchEvent(now, can_id, "silent", min(1.0, gap / (2 * limit)),
                                      f"no frame for {gap:.2f} s, expected every "
                                      f"{1000 * period['median_dt']:.0f} ms",
                                      extra={"gap_s": round(gap, 3)}))
        return out

    def _ready(self, can_id: str, kind: str, at: float) -> bool:
        key = (can_id, kind)
        last = self._last_event.get(key)
        if last is not None and at - last < self.cooldown_s:
            return False
        self._last_event[key] = at
        return True

    # ── reading back ─────────────────────────────────────────────────────────

    def events(self, since_ts: float | None = None, limit: int = 100) -> list[WatchEvent]:
        """Newest last; ``since_ts`` keeps events after that frame time."""
        items = list(self._events)
        if since_ts is not None:
            items = [e for e in items if e.ts > since_ts]
        return items[-int(limit):] if limit else items

    def stats(self) -> dict:
        return {"fitted": self.is_fitted, **self._fit_info,
                "observed_frames": self._observed_frames, "batches": self._batches,
                "events": len(self._events), "by_kind": dict(self._by_kind),
                "silent_now": sorted(self._silent), "new_ids": sorted(self._new_ids),
                "last_ts": self._now, "threshold": self.threshold,
                "cooldown_s": self.cooldown_s}
