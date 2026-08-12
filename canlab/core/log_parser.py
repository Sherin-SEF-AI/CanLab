import pandas as pd
import numpy as np
import re
from pathlib import Path

"""
Log parsers for CanLab.

Every parser returns a DataFrame with the canonical schema:
    Timestamp (float, seconds), ID (canonical hex string), Bus (int),
    DLC (int), B0..B7 (ints, NaN for missing bytes), Delta (per-ID diff).
Some parsers also set Extended (bool).

Supported capture formats:
    SavvyCAN CSV, candump .log, openpilot .rlog/.qlog, .pcap/.pcapng,
    Vector .blf, Vector .asc, and MDF4 .mf4/.mdf (CANedge).
"""


def _hexbyte(v):
    """Parse one SavvyCAN data-byte token (hex string) to an int, or NaN."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return np.nan
    s = str(v).strip()
    if not s:
        return np.nan
    try:
        return int(s, 16) & 0xFF
    except ValueError:
        return np.nan


def _bytes_are_hex(df: pd.DataFrame, cols: list) -> bool:
    """Decide whether SavvyCAN data-byte columns are hex or decimal.

    Real SavvyCAN pads each data byte to exactly two hex digits (``00``–``FF``).
    The bundled decimal sample uses variable-width decimal (``0``, ``150``). So:
    a hex letter anywhere ⇒ hex; a token wider than 2 chars or a bare single
    digit ⇒ decimal; otherwise (all tokens exactly two digits) default to hex,
    which is SavvyCAN's actual format.
    """
    import re
    letter = re.compile(r"[A-Fa-f]")
    sample_tokens = []
    for c in cols:
        vals = df[c].dropna().astype(str).head(2000).tolist()
        sample_tokens.extend(vals)
        if len(sample_tokens) >= 4000:
            break
    saw_two_digit = False
    for tok in sample_tokens:
        tok = tok.strip()
        if not tok:
            continue
        if letter.search(tok):
            return True                 # definitely hex
        if len(tok) > 2 or len(tok) == 1:
            return False                # decimal (SavvyCAN always pads to 2)
        saw_two_digit = True
    # All tokens were exactly two digits with no letters: treat as hex
    # (SavvyCAN) when we actually saw such tokens; empty ⇒ harmless default.
    return saw_two_digit


def parse_savvycan_csv(filepath: str) -> pd.DataFrame:
    """Parse GVRET SavvyCAN CSV format.

    SavvyCAN writes a trailing comma after the last data byte (``…,00,``), so
    every data row has one more field than the 14-column header. Without
    ``index_col=False`` pandas silently promotes the first column (Time Stamp)
    to the row index and shifts every remaining column left by one — real IDs
    land in the timestamp column, the ID column fills with the Extended flag,
    and the whole capture decodes as garbage. ``index_col=False`` keeps the
    columns aligned; the extra trailing field is dropped as an unnamed column.
    """
    # Read the ID and data-byte columns as strings. Otherwise an all-numeric ID
    # column (e.g. "018", "111") is inferred as int64 — dropping the leading zero
    # and turning "018" into decimal 18, which normalize_id then renders as
    # 0x12 ("012"). Byte columns must stay strings so hex tokens survive.
    str_cols = {c: str for c in ("ID", "D1", "D2", "D3", "D4",
                                 "D5", "D6", "D7", "D8")}
    df = pd.read_csv(filepath, skipinitialspace=True, index_col=False,
                     dtype=str_cols)
    # Drop the phantom column created by SavvyCAN's trailing comma, if present.
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
    df.columns = [c.strip() for c in df.columns]

    col_map = {
        "Time Stamp": "Timestamp",
        "ID":         "ID",
        "Extended":   "Extended",
        "Dir":        "Dir",
        "Bus":        "Bus",
        "LEN":        "DLC",
        "D1": "B0", "D2": "B1", "D3": "B2", "D4": "B3",
        "D5": "B4", "D6": "B5", "D7": "B6", "D8": "B7",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    if "Timestamp" in df.columns:
        df["Timestamp"] = pd.to_numeric(df["Timestamp"], errors="coerce") / 1_000_000.0

    if "ID" in df.columns:
        df["ID"] = df["ID"].apply(_normalize_id)

    byte_cols = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]
    present = [c for c in byte_cols if c in df.columns]
    as_hex = _bytes_are_hex(df, present)
    for col in byte_cols:
        if col not in df.columns:
            df[col] = np.nan
        elif as_hex:
            # Real SavvyCAN writes data bytes in hex ("0A", "FF"). Parsing them
            # with to_numeric read "10" as decimal 10 (not 0x10) and turned any
            # value with a hex letter into NaN — corrupting every byte.
            df[col] = df[col].map(_hexbyte)
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "Bus" not in df.columns:
        df["Bus"] = 0
    if "DLC" not in df.columns:
        df["DLC"] = 8
    else:
        df["DLC"] = pd.to_numeric(df["DLC"], errors="coerce").fillna(8).astype(int)

    df = df.dropna(subset=["Timestamp", "ID"])
    df = df.sort_values("Timestamp").reset_index(drop=True)

    df["Delta"] = _compute_delta(df)

    return df


def parse_candump_log(filepath: str) -> pd.DataFrame:
    """Parse standard candump log format: (timestamp) interface ID#DATA"""
    rows = []
    pattern = re.compile(
        r"\((\d+\.\d+)\)\s+(\S+)\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)"
    )
    with open(filepath) as f:
        for line in f:
            m = pattern.match(line.strip())
            if not m:
                continue
            ts, iface, can_id, data_hex = m.groups()
            data_hex = data_hex.upper()
            byte_vals = [int(data_hex[i:i+2], 16) for i in range(0, len(data_hex), 2)]
            while len(byte_vals) < 8:
                byte_vals.append(np.nan)
            rows.append({
                "Timestamp": float(ts),
                "ID":        _normalize_id(can_id),
                "Bus":       iface,
                "DLC":       len(data_hex) // 2,
                **{f"B{i}": byte_vals[i] for i in range(8)},
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Timestamp").reset_index(drop=True)
        df["Delta"] = _compute_delta(df)
    return df


def parse_pcap(filepath: str) -> pd.DataFrame:
    """Parse .pcap / .pcapng files containing CAN frames (Linux SocketCAN linktype 227)."""
    import dpkt

    rows = []
    opener = dpkt.pcapng.Reader if filepath.lower().endswith(".pcapng") else dpkt.pcap.Reader

    with open(filepath, "rb") as f:
        try:
            reader = opener(f)
        except Exception:
            # pcapng reader may fail on plain pcap — fall back
            f.seek(0)
            reader = dpkt.pcap.Reader(f)

        for ts, buf in reader:
            # SocketCAN linktype = 227 (DLT_CAN_SOCKETCAN)
            # Frame layout: 4-byte CAN ID (LE) | 1-byte DLC | 3-byte pad | 8-byte data
            if len(buf) < 8:
                continue
            try:
                import struct
                can_id_raw, dlc = struct.unpack_from("<IB", buf, 0)
                # Mask out flags: bit 31 = EFF (extended), bit 30 = RTR, bit 29 = ERR
                extended = bool(can_id_raw & 0x80000000)
                can_id   = can_id_raw & 0x1FFFFFFF
                dlc      = min(dlc, 8)
                data     = buf[8: 8 + dlc]
                byte_vals = list(data) + [np.nan] * (8 - len(data))
                rows.append({
                    "Timestamp": float(ts),
                    "ID":        format(can_id, "03X"),
                    "Bus":       0,
                    "DLC":       dlc,
                    "Extended":  extended,
                    **{f"B{i}": byte_vals[i] for i in range(8)},
                })
            except Exception:
                continue

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("Timestamp").reset_index(drop=True)
    df["Delta"] = _compute_delta(df)
    return df


def _rows_from_can_messages(messages) -> pd.DataFrame:
    """Build a canonical-schema DataFrame from an iterable of python-can Messages."""
    rows = []
    for msg in messages:
        # Skip error frames / remote frames without payload semantics.
        if getattr(msg, "is_error_frame", False):
            continue
        data = bytes(msg.data) if msg.data is not None else b""
        dlc = msg.dlc if msg.dlc is not None else len(data)
        byte_vals = list(data[:8]) + [np.nan] * (8 - min(len(data), 8))
        channel = msg.channel
        if isinstance(channel, str):
            m = re.search(r"\d+", channel)
            bus = int(m.group()) if m else 0
        elif isinstance(channel, int):
            bus = channel
        else:
            bus = 0
        rows.append({
            "Timestamp": float(msg.timestamp),
            "ID":        _normalize_id(msg.arbitration_id),
            "Bus":       bus,
            "DLC":       int(dlc),
            "Extended":  bool(msg.is_extended_id),
            **{f"B{i}": byte_vals[i] for i in range(8)},
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("Timestamp").reset_index(drop=True)
    df["Delta"] = _compute_delta(df)
    return df


def parse_blf(filepath: str) -> pd.DataFrame:
    """Parse a Vector BLF capture (.blf) via python-can's BLFReader."""
    import can
    with can.BLFReader(filepath) as reader:
        return _rows_from_can_messages(reader)


def parse_asc(filepath: str) -> pd.DataFrame:
    """Parse a Vector ASCII capture (.asc) via python-can's ASCReader."""
    import can
    with can.ASCReader(filepath) as reader:
        return _rows_from_can_messages(reader)


def parse_mdf(filepath: str) -> pd.DataFrame:
    """Parse an MDF4 CAN capture (.mf4/.mdf, e.g. CANedge) via asammdf.

    Requires the optional ``asammdf`` dependency. Raises a clear ImportError
    telling the user how to install it when the package is missing.
    """
    try:
        from asammdf import MDF
    except ImportError as e:
        raise ImportError(
            "Reading MDF4 (.mf4/.mdf) captures requires the 'asammdf' package. "
            "Install it with: pip install asammdf"
        ) from e

    rows = []
    with MDF(filepath) as mdf:
        # asammdf exposes raw CAN frames through the bus-logging helper; each
        # returned Signal carries a structured record with ID/DLC/DataBytes.
        try:
            bus_signals = mdf.get_bus_signals("CAN") if hasattr(mdf, "get_bus_signals") else []
        except Exception:
            bus_signals = []

        # Preferred path: iterate raw CAN_DataFrame records directly.
        frame_names = [
            name for name in mdf.channels_db
            if "CAN_DataFrame" in name
        ]
        seen = set()
        for name in frame_names:
            base = name.split(".")[0]
            if base in seen:
                continue
            seen.add(base)
            try:
                ids = mdf.get(f"{base}.ID")
                timestamps = ids.timestamps
                id_vals = np.asarray(ids.samples)
                dlcs = np.asarray(mdf.get(f"{base}.DLC").samples)
                data_bytes = np.asarray(mdf.get(f"{base}.DataBytes").samples)
                try:
                    ide = np.asarray(mdf.get(f"{base}.IDE").samples)
                except Exception:
                    ide = None
            except Exception:
                continue

            for i in range(len(timestamps)):
                dlc = int(dlcs[i]) if i < len(dlcs) else 0
                raw = data_bytes[i]
                data = bytes(int(b) & 0xFF for b in np.asarray(raw).ravel()[:8])
                byte_vals = list(data[:8]) + [np.nan] * (8 - min(len(data), 8))
                extended = bool(ide[i]) if ide is not None else int(id_vals[i]) > 0x7FF
                rows.append({
                    "Timestamp": float(timestamps[i]),
                    "ID":        _normalize_id(int(id_vals[i])),
                    "Bus":       0,
                    "DLC":       dlc,
                    "Extended":  extended,
                    **{f"B{j}": byte_vals[j] for j in range(8)},
                })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("Timestamp").reset_index(drop=True)
    df["Delta"] = _compute_delta(df)
    return df


def parse_log_file(filepath: str) -> pd.DataFrame:
    """Auto-detect format and parse.

    Supports .csv, .log, .rlog, .qlog, .pcap, .pcapng, .blf, .asc,
    and MDF4 .mf4/.mdf (CANedge).
    """
    path = Path(filepath)
    suffix = path.suffix.lower()
    try:
        if suffix in (".rlog", ".qlog"):
            from core.openpilot_parser import parse_rlog
            return parse_rlog(filepath)
        if suffix in (".pcap", ".pcapng"):
            return parse_pcap(filepath)
        if suffix == ".blf":
            return parse_blf(filepath)
        if suffix == ".asc":
            return parse_asc(filepath)
        if suffix in (".mf4", ".mdf"):
            return parse_mdf(filepath)
        if suffix == ".log":
            # candump marks CAN FD frames with a double '##' (id##flags+data).
            # The classic parser's single-'#' regex mangles those, so detect FD
            # frames up front and use the FD-aware parser when present.
            if _candump_has_fd(filepath):
                return parse_candump_fd(filepath)
            return parse_candump_log(filepath)
        # Try SavvyCAN first
        with open(filepath) as f:
            header = f.readline()
        if "Time Stamp" in header or "D1" in header:
            return parse_savvycan_csv(filepath)
        # Fall back to candump
        return parse_candump_log(filepath)
    except Exception as e:
        raise ValueError(f"Failed to parse {filepath}: {e}") from e


def _candump_has_fd(filepath: str, sniff_lines: int = 2000) -> bool:
    """True if any of the first sniff_lines candump lines is a CAN FD frame."""
    fd_re = re.compile(r"\)\s+\S+\s+[0-9A-Fa-f]+##")
    try:
        with open(filepath) as f:
            for i, line in enumerate(f):
                if i >= sniff_lines:
                    break
                if fd_re.search(line):
                    return True
    except Exception:
        return False
    return False


def parse_candump_fd(filepath: str) -> pd.DataFrame:
    """
    Parse candump logs that contain CAN FD frames (DLC > 8).
    Lines with ## prefix (FD frames) are supported alongside classic frames.
    FD frames get B0..B{n-1} columns; missing classic columns filled with NaN.
    """
    rows = []
    pattern_classic = re.compile(
        r"\((\d+\.\d+)\)\s+(\S+)\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)"
    )
    # CAN FD: (ts) iface ID##FLAGS DATA
    pattern_fd = re.compile(
        r"\((\d+\.\d+)\)\s+(\S+)\s+([0-9A-Fa-f]+)##([0-9A-Fa-f])([0-9A-Fa-f]*)"
    )
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            m_fd = pattern_fd.match(line)
            m_cl = pattern_classic.match(line)

            if m_fd:
                ts, iface, can_id, _flags, data_hex = m_fd.groups()
            elif m_cl:
                ts, iface, can_id, data_hex = m_cl.groups()
            else:
                continue

            data_hex = data_hex.upper()
            byte_vals = [int(data_hex[i:i+2], 16) for i in range(0, len(data_hex), 2)]
            dlc = len(byte_vals)
            row: dict = {
                "Timestamp": float(ts),
                "ID":        _normalize_id(can_id),
                "Bus":       iface,
                "DLC":       dlc,
            }
            for i in range(max(dlc, 8)):
                row[f"B{i}"] = byte_vals[i] if i < dlc else np.nan
            rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Timestamp").reset_index(drop=True)
        df["Delta"] = _compute_delta(df)
    return df


def _normalize_id(val) -> str:
    # Kept for backwards compatibility; canonical logic lives in core.canid.
    from core.canid import normalize_id
    return normalize_id(val)


def _compute_delta(df: pd.DataFrame) -> pd.Series:
    return df.groupby("ID")["Timestamp"].diff().fillna(0.0)
