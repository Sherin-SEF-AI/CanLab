"""
Read openpilot logs (rlog, qlog; plain, .bz2 or .zst) into CanLab frames.

An openpilot log is a stream of capnp ``Event`` messages. CAN traffic is the
``can`` event: a list of frames with an address, the payload and ``src``, the
panda's bus number. The schema is comma.ai's cereal, vendored under
``core/data/cereal`` (MIT, see the NOTICE there), so only pycapnp is needed.

Three things the previous reader got wrong, each of which stopped a real log
from opening:

- Logs are distributed compressed (``rlog.bz2``, ``rlog.zst``) and were read
  as raw capnp.
- The schema was looked for in places nobody has it, and when found was
  loaded without an import path, which its absolute imports need.
- A panda echoes the frames openpilot itself sends with ``src`` 128 plus the
  bus. Those are openpilot's transmissions, not the car's traffic; they are
  now left out of the capture unless asked for, and counted.

The same log carries the car's GPS, which is a ready reference for the
calibrator: ``gps_reference`` returns it as ``ReferenceSeries``.
"""
from __future__ import annotations

import bz2
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

SCHEMA_DIR = Path(__file__).resolve().parent / "data" / "cereal"
#: panda marks a frame it transmitted with 128 added to the bus number
SENT_FLAG = 0x80

_CAPNP_AVAILABLE = False
try:
    import capnp  # noqa: F401
    _CAPNP_AVAILABLE = True
except ImportError:
    log.debug("pycapnp not installed", exc_info=True)


def is_available() -> bool:
    return _CAPNP_AVAILABLE


def _schema():
    import capnp
    capnp.remove_import_hook()
    path = SCHEMA_DIR / "log.capnp"
    if not path.is_file():
        raise FileNotFoundError(f"cereal schema missing at {path}")
    return capnp.load(str(path), imports=[str(SCHEMA_DIR)])


_LOG = None


def schema():
    """The loaded cereal ``log`` module, cached."""
    global _LOG
    if _LOG is None:
        _LOG = _schema()
    return _LOG


def read_bytes(path) -> bytes:
    """The raw event stream of a log, decompressed if it needs to be."""
    p = Path(path)
    data = p.read_bytes()
    if data[:3] == b"BZh":
        return bz2.decompress(data)
    if data[:4] == b"\x28\xb5\x2f\xfd":                   # zstd frame magic
        try:
            import zstandard
        except ImportError as e:
            raise RuntimeError("This log is zstd-compressed; pip install zstandard "
                               "to read it, or decompress it first.") from e
        return zstandard.ZstdDecompressor().decompressobj().decompress(data)
    return data


def events(path):
    """Every Event in a log, in order."""
    return schema().Event.read_multiple_bytes(read_bytes(path))


def parse_rlog(filepath: str, include_sent: bool = False) -> pd.DataFrame:
    """
    An openpilot log's CAN traffic as a standard frames DataFrame.

    Timestamps are seconds from the first event. ``Bus`` is the panda bus.
    Frames openpilot transmitted (``src`` >= 128) are dropped unless
    ``include_sent``; how many were dropped is in ``df.attrs["sent_frames"]``.

    Raises RuntimeError if pycapnp is missing or the file is not a log.
    """
    if not _CAPNP_AVAILABLE:
        raise RuntimeError("Reading openpilot logs needs pycapnp: "
                           "pip install canlab[openpilot]")
    from canlab.core.log_parser import _finish, make_row
    path = Path(filepath)
    try:
        stream = events(path)
        rows, sent, t0 = [], 0, None
        for event in stream:
            if event.which() != "can":
                continue
            ts = event.logMonoTime / 1e9
            if t0 is None:
                t0 = ts
            for frame in event.can:
                src = int(frame.src)
                if src & SENT_FLAG and not include_sent:
                    sent += 1
                    continue
                address = int(frame.address)
                rows.append(make_row(ts - t0, address, address > 0x7FF, src,
                                     bytes(frame.dat)[:64]))
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"{path.name} is not an openpilot log this schema can read: {e}") from e
    df = _finish(rows)
    df.attrs["sent_frames"] = sent
    df.attrs["t0_mono"] = t0
    return df


def gps_reference(filepath: str):
    """The log's GPS as reference series on the same clock as ``parse_rlog``:
    speed (m/s), latitude, longitude and altitude, from gpsLocationExternal
    (the external receiver) or, failing that, gpsLocation."""
    from canlab.core.reference_series import ReferenceSeries
    t0, rows = None, {"gpsLocationExternal": [], "gpsLocation": []}
    for event in events(filepath):
        kind = event.which()
        if t0 is None and kind == "can":
            t0 = event.logMonoTime / 1e9
        if kind in rows:
            g = getattr(event, kind)
            rows[kind].append((event.logMonoTime / 1e9, g.speed, g.latitude,
                               g.longitude, g.altitude))
    source = "gpsLocationExternal" if rows["gpsLocationExternal"] else "gpsLocation"
    fixes = rows[source]
    if not fixes or t0 is None:
        return []
    ts = [r[0] - t0 for r in fixes]
    name = Path(filepath).name
    return [ReferenceSeries("speed", ts, [r[1] for r in fixes], unit="m/s", source=name,
                            meta={"from": source}),
            ReferenceSeries("latitude", ts, [r[2] for r in fixes], unit="deg", source=name),
            ReferenceSeries("longitude", ts, [r[3] for r in fixes], unit="deg", source=name),
            ReferenceSeries("altitude", ts, [r[4] for r in fixes], unit="m", source=name)]
