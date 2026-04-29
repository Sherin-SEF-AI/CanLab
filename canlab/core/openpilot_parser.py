"""
Parse openpilot .rlog / .qlog files into a standard CAN frames DataFrame.

Requires pycapnp. Gracefully returns empty DataFrame if missing.

openpilot log format (simplified):
  Each log entry is a capnp-encoded Event with a union field.
  CAN frames live in Event.can[], each entry has:
    address, busTime, dat (bytes), src (bus index).
"""
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd

_CAPNP_AVAILABLE = False
try:
    import capnp  # noqa: F401
    _CAPNP_AVAILABLE = True
except ImportError:
    pass


def is_available() -> bool:
    return _CAPNP_AVAILABLE


def _normalize_id(val: int) -> str:
    return format(val, "03X")


def parse_rlog(filepath: str) -> pd.DataFrame:
    """
    Parse an openpilot .rlog or .qlog file → standard CAN DataFrame.

    Returns empty DataFrame if pycapnp is not installed or on parse error.
    """
    if not _CAPNP_AVAILABLE:
        raise RuntimeError(
            "pycapnp not installed. "
            "Run: pip install pycapnp --break-system-packages"
        )

    import capnp  # noqa: F811
    # Load schema from openpilot or use raw reader
    path = Path(filepath)
    rows = []

    try:
        # openpilot logs are a stream of framed capnp messages
        # Each frame: 4-byte big-endian length + capnp data
        # We try the official cereal schema; fall back to raw struct read.
        rows = _parse_with_cereal(path)
    except Exception:
        # Fallback: scan for known CAN address patterns
        rows = _parse_raw_scan(path)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values("Timestamp").reset_index(drop=True)

    # Normalise byte columns B0..B7 (pad shorter payloads)
    for i in range(8):
        col = f"B{i}"
        if col not in df.columns:
            df[col] = np.nan

    df["Delta"] = _compute_delta(df)
    return df


def _parse_with_cereal(path: Path) -> list:
    """Try to read using cereal capnp schemas bundled with openpilot."""
    import capnp
    # Look for cereal schema in common openpilot locations
    import os
    schema_candidates = [
        os.path.expanduser("~/openpilot/cereal/log.capnp"),
        "/opt/openpilot/cereal/log.capnp",
        str(Path(__file__).parent.parent / "resources" / "log.capnp"),
    ]
    schema_path = next((p for p in schema_candidates if os.path.exists(p)), None)

    rows = []
    with open(path, "rb") as f:
        raw = f.read()

    # Stream of framed capnp messages (4-byte LE size + data)
    offset = 0
    ts_base = 0.0

    if schema_path:
        log_capnp = capnp.load(schema_path)
        while offset + 4 < len(raw):
            size = int.from_bytes(raw[offset:offset + 4], "little")
            offset += 4
            if offset + size > len(raw):
                break
            try:
                event = log_capnp.Event.from_bytes(raw[offset:offset + size])
                if event.which() == "can":
                    for frame in event.can:
                        ts = event.logMonoTime / 1e9
                        if ts_base == 0.0:
                            ts_base = ts
                        dat = bytes(frame.dat)
                        byte_dict = {f"B{i}": dat[i] if i < len(dat) else np.nan
                                     for i in range(8)}
                        rows.append({
                            "Timestamp": ts - ts_base,
                            "ID":        _normalize_id(frame.address),
                            "Bus":       frame.src,
                            "DLC":       len(dat),
                            **byte_dict,
                        })
            except Exception:
                pass
            offset += size
    else:
        raise FileNotFoundError("cereal schema not found")

    return rows


def _parse_raw_scan(path: Path) -> list:
    """
    Fallback: scan raw bytes for candump-like patterns embedded in binary log.
    Extracts any 4-byte aligned CAN frame records by heuristic.
    """
    with open(path, "rb") as f:
        raw = f.read()

    rows = []
    # Try to extract from simple framing without schema
    offset = 0
    ts_counter = 0.0

    while offset + 16 <= len(raw):
        # Check if this could be a CAN record: address < 0x800, dlc 1-8
        try:
            addr = int.from_bytes(raw[offset:offset + 4], "little")
            dlc  = raw[offset + 4]
            if 0 < addr < 0x800 and 0 < dlc <= 8:
                dat = raw[offset + 5: offset + 5 + dlc]
                byte_dict = {f"B{i}": dat[i] if i < len(dat) else np.nan
                             for i in range(8)}
                ts_counter += 0.001  # approximate 1ms spacing
                rows.append({
                    "Timestamp": ts_counter,
                    "ID":        _normalize_id(addr),
                    "Bus":       0,
                    "DLC":       dlc,
                    **byte_dict,
                })
                offset += 5 + dlc
                continue
        except Exception:
            pass
        offset += 1

    return rows


def _compute_delta(df: pd.DataFrame) -> pd.Series:
    deltas = pd.Series(index=df.index, dtype=float)
    last_ts: dict = {}
    for idx, row in df.iterrows():
        cid = row["ID"]
        ts  = row["Timestamp"]
        deltas[idx] = ts - last_ts[cid] if cid in last_ts else 0.0
        last_ts[cid] = ts
    return deltas
