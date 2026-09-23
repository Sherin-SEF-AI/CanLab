"""Decode any standard NMEA 2000 PGN from the table distilled from canboat.

The table (core/data/n2k_pgns.json, built by tools/build_n2k_table.py from
canboat, Apache License 2.0) gives each field's position, width, resolution,
sign, unit and valid range. The range is what makes the decode honest: an
eight-bit field is valid to 252, and 253, 254 and 255 mean reserved, error
and not available, as they do throughout NMEA 2000. A value outside its
range is therefore never shown as a reading.

The hand-written decoders in core/j1939.py are checked against real frames
and take precedence; this covers the rest. Where both exist, the tests
require them to agree field by field on the real marine recording.

Temperatures are converted from kelvin to degrees Celsius so they read the
same as the hand-written ones. Angles stay in radians, as NMEA 2000 sends
them and as the rest of CanLab reports them.
"""
from __future__ import annotations

import datetime as _dt
import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "n2k_pgns.json"


@lru_cache(maxsize=1)
def table() -> dict:
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pgns": {}, "lookups": {}}


def definition(pgn: int) -> dict | None:
    return table()["pgns"].get(str(pgn))


def name(pgn: int) -> str | None:
    d = definition(pgn)
    return d["name"] if d else None


def is_fast_packet(pgn: int) -> bool | None:
    d = definition(pgn)
    return d["fast"] if d else None


def fast_packet_pgns() -> frozenset:
    return frozenset(int(p) for p, d in table()["pgns"].items() if d["fast"])


def _places(resolution: float) -> int:
    """Decimal places worth keeping for a field of this resolution.

    A fixed six places cut a 1e-16 degree longitude to six centimetres and
    padded a 0.1 kelvin temperature with float noise; this keeps two places
    beyond the resolution, up to what a double can hold.
    """
    import math
    if resolution <= 0:
        return 6
    return int(min(12, max(0, math.ceil(-math.log10(resolution)) + 2)))


def _raw(data: bytes, bit: int, bits: int) -> int | None:
    if bit + bits > len(data) * 8:
        return None
    return (int.from_bytes(bytes(data), "little") >> bit) & ((1 << bits) - 1)


def decode(pgn: int, data: bytes) -> dict:
    """{field: (value, unit)} for a standard PGN, or {} if it is not in the table.

    Not-available and reserved values are left out; an error code is the
    string "error".
    """
    d = definition(pgn)
    if d is None:
        return {}
    lookups = table()["lookups"]
    out: dict = {}
    for (fname, bit, bits, res, signed, unit, kind, lo, hi, offset, lookup) in d["fields"]:
        if fname in ("SID", "Reserved"):
            continue                     # a sequence number, not a reading
        raw = _raw(data, bit, bits)
        if raw is None:
            continue
        if kind == "STRING_FIX":
            text = bytes(data)[bit // 8:(bit + bits) // 8]
            text = text.rstrip(b"\xff\x00 @").decode("ascii", "replace")
            if text:
                out[fname] = (text, "")
            continue
        all_ones = (1 << bits) - 1
        if signed:
            top = (1 << (bits - 1)) - 1
            if raw == top:
                continue                 # not available
            if raw == top - 1 and bits >= 4:
                out[fname] = ("error", "")
                continue
            if raw > top:
                raw -= 1 << bits
        else:
            if raw == all_ones and bits >= 2:
                continue
            if raw == all_ones - 1 and bits >= 4:
                out[fname] = ("error", "")
                continue
        if kind in ("LOOKUP", "INDIRECT_LOOKUP") and lookup:
            label = lookups.get(lookup, {}).get(str(raw))
            if label is not None:
                out[fname] = (label, "")
            elif hi is None or raw <= hi:
                out[fname] = (f"unknown ({raw})", "")
            continue
        value = raw * res + (offset or 0)
        if hi is not None and value > hi * (1 + 1e-9) + 1e-9:
            continue                     # a reserved code
        if lo is not None and value < lo * (1 + 1e-9) - 1e-9 and not signed:
            continue
        if kind == "DATE":
            out[fname] = ((_dt.date(1970, 1, 1) + _dt.timedelta(days=int(value))).isoformat(), "")
            continue
        if kind in ("MMSI",):
            out[fname] = (f"{int(raw):09d}", "")
            continue
        if unit == "K":
            value, unit = value - 273.15, "°C"
        out[fname] = (round(float(value), _places(res)), unit)
    return out
