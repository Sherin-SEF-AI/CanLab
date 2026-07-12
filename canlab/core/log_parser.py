import pandas as pd
import numpy as np
import re
from pathlib import Path


def parse_savvycan_csv(filepath: str) -> pd.DataFrame:
    """Parse GVRET SavvyCAN CSV format."""
    df = pd.read_csv(filepath, skipinitialspace=True)
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
    for col in byte_cols:
        if col not in df.columns:
            df[col] = np.nan
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "Bus" not in df.columns:
        df["Bus"] = 0
    if "DLC" not in df.columns:
        df["DLC"] = 8

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


def parse_log_file(filepath: str) -> pd.DataFrame:
    """Auto-detect format and parse. Supports .csv, .log, .rlog, .qlog, .pcap, .pcapng."""
    path = Path(filepath)
    suffix = path.suffix.lower()
    try:
        if suffix in (".rlog", ".qlog"):
            from core.openpilot_parser import parse_rlog
            return parse_rlog(filepath)
        if suffix in (".pcap", ".pcapng"):
            return parse_pcap(filepath)
        if suffix == ".log":
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
