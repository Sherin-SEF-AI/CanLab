"""Decode a capture into a wide signal-over-time table and export it.

Turns raw frames + a DBC into a tidy Timestamp × signal matrix suitable for
external analysis (pandas, Grafana/InfluxDB via CSV, or Parquet for big logs).
"""
from __future__ import annotations

import pandas as pd
import logging

log = logging.getLogger(__name__)

BYTE_COLS = [f"B{i}" for i in range(8)]


def decode_timeseries(frames_df: pd.DataFrame, dbc_signals: list[dict]) -> pd.DataFrame:
    """Decode every frame against the DBC signals into a wide DataFrame.

    Columns: Timestamp, ID, then one column per decoded signal. Rows without a
    matching message decode to just Timestamp/ID (signal cells stay NaN).
    """
    if frames_df.empty or not dbc_signals:
        return pd.DataFrame()

    from canlab.core.dbc_manager import build_database, decode_series
    from canlab.core.canid import normalize_id

    db = build_database(dbc_signals)          # raises ValueError on bad definitions
    known = {m.frame_id for m in db.messages}

    parts = []
    for can_id, grp in frames_df.groupby("ID", sort=False):
        try:
            fid = int(normalize_id(can_id), 16)
        except (ValueError, TypeError):
            continue
        if fid in known:
            dec = decode_series(dbc_signals, can_id, grp)
            if dec.empty:
                dec = pd.DataFrame({"Timestamp": grp["Timestamp"].to_numpy(dtype=float)})
        else:
            dec = pd.DataFrame({"Timestamp": grp["Timestamp"].to_numpy(dtype=float)})
        dec.insert(1, "ID", str(can_id))
        parts.append(dec)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    return out.sort_values("Timestamp", kind="stable").reset_index(drop=True)


def export_timeseries(frames_df: pd.DataFrame, dbc_signals: list[dict],
                      path: str, fmt: str | None = None) -> int:
    """Decode and write a signal time-series to CSV or Parquet.

    fmt defaults to the file extension (.csv / .parquet). Returns the row count.
    Parquet requires pyarrow; raises a clear error if it's missing.
    """
    df = decode_timeseries(frames_df, dbc_signals)
    if df.empty:
        raise ValueError("Nothing to export: no frames or no DBC signals decoded.")

    fmt = (fmt or path.rsplit(".", 1)[-1]).lower()
    if fmt in ("parquet", "pq"):
        try:
            df.to_parquet(path, index=False)
        except ImportError as e:
            raise ImportError(
                "Parquet export requires pyarrow. Install it with: pip install pyarrow"
            ) from e
    else:
        df.to_csv(path, index=False)
    return len(df)
