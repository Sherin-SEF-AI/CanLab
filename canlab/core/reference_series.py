"""Reference series: what a vehicle recorded alongside its CAN bus.

Reference calibration needs a physical quantity on a clock: speed from a GPS
logger, RPM from an OBD dongle, a value read off a dashboard video. Those
arrive as CSV files with whatever columns the tool wrote, or as GPX tracks from
a phone. This module turns either into ``ReferenceSeries`` objects, one per
quantity, so the calibrator never sees a file format.

CSV: the time column is the one named timestamp, time or t (any case), else
the first column. Numeric times are taken as seconds; ISO strings are parsed
to epoch seconds. Every other numeric column becomes a series. A unit in the
header, ``speed (km/h)`` or ``speed [km/h]``, is kept with the series.

GPX: track points with a time become latitude, longitude, altitude (where the
file has elevation) and speed. Speed is the haversine distance between
consecutive points over the time between them, in km/h, passed through a
3-point median filter because a phone's position jitters and the derivative
of jitter is noise. Points without a time are skipped; a stationary track
gives zero speed.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

EARTH_RADIUS_M = 6_371_008.8

_TIME_NAMES = ("timestamp", "time", "t", "time_s", "seconds", "time stamp", "epoch")
_UNIT_RE = re.compile(r"^\s*(.*?)\s*[\(\[]\s*([^\)\]]*?)\s*[\)\]]\s*$")


@dataclass
class ReferenceSeries:
    """One physical quantity against time."""

    name: str
    ts: np.ndarray
    values: np.ndarray
    unit: str = ""
    source: str = ""
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        ts = np.asarray(self.ts, dtype=float)
        vals = np.asarray(self.values, dtype=float)
        ok = np.isfinite(ts) & np.isfinite(vals)
        order = np.argsort(ts[ok], kind="stable")
        self.ts = ts[ok][order]
        self.values = vals[ok][order]

    @property
    def samples(self) -> int:
        return int(len(self.ts))

    @property
    def span_s(self) -> float:
        return float(self.ts[-1] - self.ts[0]) if len(self.ts) > 1 else 0.0

    def as_dict(self) -> dict:
        return {"name": self.name, "unit": self.unit, "samples": self.samples,
                "span_s": round(self.span_s, 3), "source": self.source,
                "t_start": float(self.ts[0]) if len(self.ts) else None}


def load_reference_file(path: str | Path) -> list[ReferenceSeries]:
    """Every series a reference file holds, by its extension."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"no such reference file: {path}")
    if p.suffix.lower() == ".gpx":
        return load_gpx(p)
    return load_csv(p)


# ── CSV ──────────────────────────────────────────────────────────────────────

def split_name_unit(header: str) -> tuple[str, str]:
    """``"speed (km/h)"`` -> ``("speed", "km/h")``; a bare name keeps no unit."""
    text = str(header).strip()
    m = _UNIT_RE.match(text)
    if m and m.group(1):
        return m.group(1).strip(), m.group(2).strip()
    return text, ""


def _time_column(df: pd.DataFrame) -> str:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for name in _TIME_NAMES:
        if name in lowered:
            return lowered[name]
    return df.columns[0]


def _seconds(col: pd.Series) -> np.ndarray:
    """Numeric times as they are; anything else parsed as dates to epoch seconds."""
    numeric = pd.to_numeric(col, errors="coerce")
    if numeric.notna().sum() >= max(1, int(0.5 * len(col))):
        return numeric.to_numpy(dtype=float)
    # ISO first so a column mixing "…:08Z" and "…:10.5Z" parses whole; then
    # per-row inference for the date formats other loggers write.
    parsed = pd.to_datetime(col, errors="coerce", utc=True, format="ISO8601")
    if parsed.isna().all():
        parsed = pd.to_datetime(col, errors="coerce", utc=True, format="mixed")
    seconds = (parsed - pd.Timestamp(0, tz="UTC")) / pd.Timedelta(seconds=1)
    return seconds.to_numpy(dtype=float)


def load_csv(path: str | Path) -> list[ReferenceSeries]:
    """One series per numeric column, on the file's time column."""
    p = Path(path).expanduser()
    df = pd.read_csv(p)
    if df.shape[1] < 2:
        raise ValueError(f"{p.name}: a reference CSV needs a time column and a value column")
    tcol = _time_column(df)
    ts = _seconds(df[tcol])
    out: list[ReferenceSeries] = []
    for col in df.columns:
        if col == tcol:
            continue
        vals = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
        if np.isfinite(vals).sum() < 2:
            continue
        name, unit = split_name_unit(col)
        series = ReferenceSeries(name or str(col), ts, vals, unit=unit, source=str(p))
        if series.samples >= 2:
            out.append(series)
    if not out:
        raise ValueError(f"{p.name}: no numeric value column found")
    return out


# ── GPX ──────────────────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres; works on scalars and arrays."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=float))
                              for x in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _gpx_time(text: str | None) -> float | None:
    if not text:
        return None
    s = text.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _median3(x: np.ndarray) -> np.ndarray:
    if len(x) < 3:
        return x.copy()
    out = x.copy()
    out[1:-1] = np.median(np.stack([x[:-2], x[1:-1], x[2:]]), axis=0)
    return out


def load_gpx(path: str | Path) -> list[ReferenceSeries]:
    """latitude, longitude, altitude and speed from a GPX track."""
    p = Path(path).expanduser()
    root = ET.parse(p).getroot()
    pts: list[tuple[float, float, float, float | None]] = []
    for el in root.iter():
        if _local(el.tag) not in ("trkpt", "rtept"):
            continue
        try:
            lat = float(el.get("lat"))
            lon = float(el.get("lon"))
        except (TypeError, ValueError):
            continue
        ele = None
        when = None
        for child in el:
            name = _local(child.tag)
            if name == "ele":
                try:
                    ele = float(child.text)
                except (TypeError, ValueError):
                    ele = None
            elif name == "time":
                when = _gpx_time(child.text)
        if when is None:
            continue
        pts.append((when, lat, lon, ele))
    if len(pts) < 2:
        raise ValueError(f"{p.name}: fewer than two timed track points")
    pts.sort(key=lambda r: r[0])
    # A repeated timestamp has no speed; keep the first of each.
    kept = [pts[0]]
    for row in pts[1:]:
        if row[0] > kept[-1][0]:
            kept.append(row)
    ts = np.array([r[0] for r in kept])
    lat = np.array([r[1] for r in kept])
    lon = np.array([r[2] for r in kept])
    dist = haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
    dt = np.diff(ts)
    seg = dist / dt * 3.6                         # km/h over each segment
    speed = np.concatenate([[seg[0]], seg]) if len(seg) else np.zeros(len(ts))
    speed = _median3(speed)
    src = str(p)
    out = [
        ReferenceSeries("speed", ts, speed, unit="km/h", source=src,
                        meta={"method": "haversine, 3-point median"}),
        ReferenceSeries("latitude", ts, lat, unit="deg", source=src),
        ReferenceSeries("longitude", ts, lon, unit="deg", source=src),
    ]
    ele = np.array([r[3] if r[3] is not None else math.nan for r in kept], dtype=float)
    if np.isfinite(ele).sum() >= 2:
        out.insert(1, ReferenceSeries("altitude", ts, ele, unit="m", source=src))
    return out
