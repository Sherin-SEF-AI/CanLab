"""Decode a capture into a wide signal-over-time table and export it.

Turns raw frames + a DBC into a tidy Timestamp × signal matrix suitable for
external analysis (pandas, Grafana/InfluxDB via CSV, or Parquet for big logs).
"""
from __future__ import annotations

import pandas as pd

BYTE_COLS = [f"B{i}" for i in range(8)]


def decode_timeseries(frames_df: pd.DataFrame, dbc_signals: list[dict]) -> pd.DataFrame:
    """Decode every frame against the DBC signals into a wide DataFrame.

    Columns: Timestamp, ID, then one column per decoded signal. Rows without a
    matching message decode to just Timestamp/ID (signal cells stay NaN).
    """
    if frames_df.empty or not dbc_signals:
        return pd.DataFrame()

    import cantools
    from core.dbc_manager import signals_to_dbc_string
    from core.canid import normalize_id

    try:
        db = cantools.database.load_string(
            signals_to_dbc_string(dbc_signals), database_format="dbc")
    except Exception:
        return pd.DataFrame()

    by_frame_id = {}
    for msg in db.messages:
        by_frame_id[msg.frame_id] = msg

    records = []
    for _, row in frames_df.iterrows():
        try:
            fid = int(normalize_id(row["ID"]), 16)
        except (ValueError, TypeError):
            continue
        msg = by_frame_id.get(fid)
        rec = {"Timestamp": float(row.get("Timestamp", 0.0)), "ID": row["ID"]}
        if msg is not None:
            n = msg.length
            data = bytes(int(row[f"B{i}"]) if pd.notna(row.get(f"B{i}")) else 0
                         for i in range(min(n, 8)))
            try:
                decoded = db.decode_message(fid, data)
                for k, v in decoded.items():
                    rec[str(k)] = float(v) if isinstance(v, (int, float)) else v
            except Exception:
                pass
        records.append(rec)

    return pd.DataFrame(records)


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
