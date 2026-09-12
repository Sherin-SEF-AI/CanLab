"""Log parsers for CanLab.

Every parser returns a DataFrame with the canonical schema:
    Timestamp (float seconds), ID (canonical uppercase hex str), Bus (int),
    DLC (int, payload byte count), Extended (bool), B0..B7 (float, NaN for
    absent bytes), B8..B{n-1} only when some frame carries more than 8 bytes
    (CAN FD), Delta (seconds since the previous frame with the same ID).

Error frames and remote frames are skipped by every parser (they carry no
payload semantics); the count of skipped frames is logged.

Supported capture formats: SavvyCAN / GVRET CSV, candump `-l` logs (classic,
CAN FD `##` lines, error frames), pcap/pcapng with LINKTYPE_CAN_SOCKETCAN,
Vector BLF and ASC (python-can readers), MDF4 (asammdf), openpilot rlog/qlog.
"""
from __future__ import annotations

import logging
import math
import re
import struct
from pathlib import Path

import numpy as np
import pandas as pd

from canlab.core.canid import normalize_id

log = logging.getLogger(__name__)

CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF
CAN_SFF_MASK = 0x7FF
LINKTYPE_CAN_SOCKETCAN = 227
CANFD_FDF = 0x04          # fd_flags bit in the pcap header byte 5

# CAN FD DLC code -> payload length (used where a source reports the code)
DLC_TO_LEN = {
    0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7,
    8: 8, 9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64,
}

BASE_COLUMNS = ["Timestamp", "ID", "Bus", "DLC", "Extended"]
BYTE_COLS8 = [f"B{i}" for i in range(8)]


# ── row helpers ───────────────────────────────────────────────────────────────

def _bus_index(channel) -> int:
    """Map a channel name/number to a small int (can0 -> 0, vcan1 -> 1)."""
    if channel is None:
        return 0
    if isinstance(channel, (int, np.integer)):
        return int(channel)
    m = re.search(r"(\d+)\s*$", str(channel))
    return int(m.group(1)) if m else 0


def make_row(ts: float, arb_id: int, extended: bool, bus, data: bytes, dlc=None) -> dict:
    """Build a canonical row dict. ``dlc`` defaults to ``len(data)``."""
    data = bytes(data)
    n = len(data)
    row = {
        "Timestamp": float(ts),
        "ID":        normalize_id(int(arb_id)),
        "Bus":       _bus_index(bus),
        "DLC":       int(n if dlc is None else dlc),
        "Extended":  bool(extended),
    }
    for i in range(max(8, n)):
        row[f"B{i}"] = float(data[i]) if i < n else np.nan
    return row


def _finish(rows: list[dict]) -> pd.DataFrame:
    """Rows -> sorted canonical DataFrame with Delta."""
    if not rows:
        return pd.DataFrame(columns=BASE_COLUMNS + BYTE_COLS8 + ["Delta"])
    df = pd.DataFrame(rows)
    byte_cols = sorted((c for c in df.columns if re.fullmatch(r"B\d+", c)),
                       key=lambda c: int(c[1:]))
    for c in BYTE_COLS8:
        if c not in df.columns:
            df[c] = np.nan
            byte_cols.append(c)
    byte_cols = sorted(set(byte_cols), key=lambda c: int(c[1:]))
    df = df[BASE_COLUMNS + byte_cols]
    df = df.sort_values("Timestamp", kind="stable").reset_index(drop=True)
    df["Bus"] = df["Bus"].astype(int)
    df["DLC"] = df["DLC"].astype(int)
    df["Extended"] = df["Extended"].astype(bool)
    df["Delta"] = _compute_delta(df)
    return df


def _compute_delta(df: pd.DataFrame) -> pd.Series:
    return df.groupby("ID")["Timestamp"].diff().fillna(0.0)


def _int_or_nan(v, base: int = 16) -> float:
    s = str(v).strip() if v is not None else ""
    if not s or s.lower() == "nan":
        return np.nan
    try:
        return float(int(s, base))
    except ValueError:
        return np.nan


def _hex_or_nan(v) -> float:
    return _int_or_nan(v, 16)


def _bytes_are_hex(columns) -> bool:
    """Whether SavvyCAN data-byte tokens are hex or decimal.

    Real SavvyCAN pads each data byte to exactly two hex digits (00 to FF), but
    decimal exports exist and use variable width (0, 150). Reading a decimal
    export as hex turns 150 into 336 and fails the whole parse, so decide from
    the tokens: a hex letter anywhere means hex; a token wider than two
    characters or a bare single digit means decimal; all two-digit and no
    letters defaults to hex, which is what SavvyCAN itself writes.
    """
    letter = re.compile(r"[A-Fa-f]")
    seen_two_digit = False
    for column in columns:
        if column is None:
            continue
        for token in column.astype(str).head(2000):
            token = token.strip()
            if not token or token.lower() == "nan":
                continue
            if letter.search(token):
                return True
            if len(token) > 2 or len(token) == 1:
                return False
            seen_two_digit = True
    return seen_two_digit


# ── SavvyCAN / GVRET CSV ─────────────────────────────────────────────────────

def parse_savvycan_csv(filepath: str) -> pd.DataFrame:
    """Parse a SavvyCAN/GVRET CSV export.

    Real exports write the ID and D1..D8 as hexadecimal. The ``Time Stamp``
    column is microseconds when it is an integer column; a column with decimal
    points is treated as seconds.
    """
    # index_col=False matters: SavvyCAN writes a trailing comma after the last
    # data byte, so each data row has one more field than the 14-column header.
    # Without it pandas promotes Time Stamp to the index and shifts every column
    # left, putting IDs in the timestamp column and the Extended flag in ID.
    df = pd.read_csv(filepath, dtype=str, skipinitialspace=True,
                     encoding="utf-8-sig", keep_default_na=False,
                     index_col=False)
    df.columns = [str(c).strip().lstrip("﻿") for c in df.columns]
    col_map = {
        "Time Stamp": "Timestamp", "Timestamp": "Timestamp",
        "ID": "ID", "Extended": "Extended", "Dir": "Dir", "Bus": "Bus", "LEN": "DLC",
        "D1": "B0", "D2": "B1", "D3": "B2", "D4": "B3",
        "D5": "B4", "D6": "B5", "D7": "B6", "D8": "B7",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if "Timestamp" not in df.columns or "ID" not in df.columns:
        raise ValueError("SavvyCAN CSV needs 'Time Stamp' and 'ID' columns")

    ts_text = df["Timestamp"].astype(str).str.strip()
    ts = pd.to_numeric(ts_text, errors="coerce")
    seconds = ts_text.str.contains(r"\.", regex=True).any()
    ts = ts if seconds else ts / 1_000_000.0

    ids = df["ID"].astype(str).str.strip()
    id_int = pd.Series([_hex_or_nan(x) for x in ids], index=df.index)

    rows = []
    ext_col = df["Extended"].astype(str).str.strip().str.lower() if "Extended" in df.columns else None
    bus_col = df["Bus"] if "Bus" in df.columns else None
    dlc_col = df["DLC"] if "DLC" in df.columns else None
    byte_vals = [df[c] if c in df.columns else None for c in BYTE_COLS8]
    byte_base = 16 if _bytes_are_hex(byte_vals) else 10
    for i in range(len(df)):
        if math.isnan(id_int.iloc[i]) or math.isnan(ts.iloc[i]):
            continue
        data = []
        for col in byte_vals:
            v = _int_or_nan(col.iloc[i], byte_base) if col is not None else np.nan
            if math.isnan(v):
                break
            data.append(int(v))
        dlc = None
        if dlc_col is not None:
            try:
                dlc = int(str(dlc_col.iloc[i]).strip())
            except ValueError:
                dlc = None
        arb = int(id_int.iloc[i])
        extended = (ext_col.iloc[i] in ("true", "1", "yes")) if ext_col is not None else arb > CAN_SFF_MASK
        bus = bus_col.iloc[i] if bus_col is not None else 0
        try:
            bus = int(str(bus).strip() or 0)
        except ValueError:
            bus = _bus_index(bus)
        rows.append(make_row(ts.iloc[i], arb, extended, bus, bytes(data), dlc))
    return _finish(rows)


# ── candump -l ───────────────────────────────────────────────────────────────

_CANDUMP_RE = re.compile(
    r"^\((?P<ts>\d+(?:\.\d+)?)\)\s+(?P<iface>\S+)\s+"
    r"(?P<id>[0-9A-Fa-f]{3,8})(?P<sep>##|#)(?P<rest>\S*)")


def parse_candump_log(filepath: str) -> pd.DataFrame:
    """Parse ``candump -l`` output: classic ``ID#DATA``, CAN FD ``ID##F DATA``,
    remote ``ID#R`` (skipped) and error frames (skipped)."""
    rows, skipped = [], 0
    with open(filepath, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            m = _CANDUMP_RE.match(line.strip())
            if not m:
                continue
            id_hex, rest = m.group("id"), m.group("rest")
            raw_id = int(id_hex, 16)
            if len(id_hex) == 8 and raw_id & CAN_ERR_FLAG:
                skipped += 1
                continue
            if rest[:1].upper() == "R":
                skipped += 1
                continue
            if m.group("sep") == "##":
                data_hex = rest[1:]          # first nibble = FD flags (BRS/ESI)
            else:
                data_hex = rest
            data_hex = re.sub(r"[^0-9A-Fa-f]", "", data_hex)
            data = bytes(int(data_hex[i:i + 2], 16) for i in range(0, len(data_hex) - 1, 2))
            extended = len(id_hex) == 8
            arb = raw_id & (CAN_EFF_MASK if extended else CAN_SFF_MASK)
            rows.append(make_row(float(m.group("ts")), arb, extended, m.group("iface"), data))
    if skipped:
        log.info("candump: skipped %d error/remote frames", skipped)
    return _finish(rows)


# ── pcap / pcapng (LINKTYPE_CAN_SOCKETCAN) ───────────────────────────────────

def _plausible(word: int) -> bool:
    """Does this ID/flags word look like a real data frame? (Error-flagged words
    are not counted: under the wrong byte order random payload bytes often
    land on the error bit.)"""
    if word & CAN_ERR_FLAG:
        return False
    if word & CAN_EFF_FLAG:
        return (word & CAN_EFF_MASK) > CAN_SFF_MASK
    return (word & ~CAN_RTR_FLAG) <= CAN_SFF_MASK


def _choose_id_order(packets: list[bytes]) -> str:
    """LINKTYPE_CAN_SOCKETCAN specifies a big-endian ID word, but captures made
    with old libpcap versions wrote the host (little-endian) order. Pick the
    order under which more frames are plausible; ties go to the spec."""
    be = sum(_plausible(struct.unpack_from(">I", b, 0)[0]) for b in packets)
    le = sum(_plausible(struct.unpack_from("<I", b, 0)[0]) for b in packets)
    return "<I" if le > be else ">I"


def parse_pcap(filepath: str) -> pd.DataFrame:
    """Parse .pcap/.pcapng files of SocketCAN frames (linktype 227, classic or FD)."""
    import dpkt

    rows, skipped = [], 0
    with open(filepath, "rb") as f:
        opener = dpkt.pcapng.Reader if filepath.lower().endswith(".pcapng") else dpkt.pcap.Reader
        try:
            reader = opener(f)
        except Exception:
            f.seek(0)
            reader = dpkt.pcap.Reader(f)
        linktype = reader.datalink()
        if linktype != LINKTYPE_CAN_SOCKETCAN:
            raise ValueError(
                f"pcap link type {linktype} is not SocketCAN ({LINKTYPE_CAN_SOCKETCAN}); "
                "capture with tcpdump/wireshark on a can interface")
        packets = [(ts, bytes(buf)) for ts, buf in reader if len(buf) >= 8]
        fmt = _choose_id_order([b for _, b in packets])
        if fmt == "<I":
            log.info("pcap: ID word is little-endian (old libpcap); using host order")
        for ts, buf in packets:
            word = struct.unpack_from(fmt, buf, 0)[0]
            if word & (CAN_ERR_FLAG | CAN_RTR_FLAG):
                skipped += 1
                continue
            extended = bool(word & CAN_EFF_FLAG)
            arb = word & (CAN_EFF_MASK if extended else CAN_SFF_MASK)
            length = buf[4]
            fd_flags = buf[5]
            max_len = 64 if (fd_flags & CANFD_FDF or length > 8) else 8
            data = buf[8:8 + min(length, max_len)]
            rows.append(make_row(float(ts), arb, extended, 0, data))
    if skipped:
        log.info("pcap: skipped %d error/remote frames", skipped)
    return _finish(rows)


# ── python-can readers (BLF / ASC) ───────────────────────────────────────────

def _rows_from_can_messages(messages) -> pd.DataFrame:
    rows, skipped = [], 0
    for msg in messages:
        if getattr(msg, "is_error_frame", False) or getattr(msg, "is_remote_frame", False):
            skipped += 1
            continue
        data = bytes(msg.data) if msg.data is not None else b""
        rows.append(make_row(msg.timestamp, msg.arbitration_id, msg.is_extended_id,
                             msg.channel, data[:64]))
    if skipped:
        log.info("reader: skipped %d error/remote frames", skipped)
    return _finish(rows)


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


# ── MDF4 (asammdf) ───────────────────────────────────────────────────────────

def parse_mdf(filepath: str) -> pd.DataFrame:
    """Parse an MDF4 CAN capture (.mf4/.mdf, e.g. CANedge) via asammdf.

    Requires the optional ``asammdf`` dependency (``pip install canlab[mdf]``).
    """
    try:
        from asammdf import MDF
    except ImportError as e:
        raise ImportError(
            "Reading MDF4 (.mf4/.mdf) captures requires the 'asammdf' package. "
            "Install it with: pip install asammdf") from e

    def _get(mdf, name):
        try:
            return np.asarray(mdf.get(name).samples)
        except Exception:
            return None

    rows = []
    with MDF(filepath) as mdf:
        bases = []
        for name in mdf.channels_db:
            base = name.split(".")[0]
            if "CAN_DataFrame" in base and base not in bases:
                bases.append(base)
        for base in bases:
            try:
                ids_sig = mdf.get(f"{base}.ID")
            except Exception:
                continue
            timestamps = np.asarray(ids_sig.timestamps)
            id_vals = np.asarray(ids_sig.samples)
            dlcs = _get(mdf, f"{base}.DLC")
            lengths = _get(mdf, f"{base}.DataLength")
            data_bytes = _get(mdf, f"{base}.DataBytes")
            ide = _get(mdf, f"{base}.IDE")
            bus = _get(mdf, f"{base}.BusChannel")
            if data_bytes is None:
                continue
            for i in range(len(timestamps)):
                raw = np.asarray(data_bytes[i]).ravel()
                if lengths is not None and i < len(lengths):
                    n = int(lengths[i])
                elif dlcs is not None and i < len(dlcs):
                    n = DLC_TO_LEN.get(int(dlcs[i]), int(dlcs[i]))
                else:
                    n = len(raw)
                data = bytes(int(b) & 0xFF for b in raw[:min(n, 64)])
                arb = int(id_vals[i])
                extended = bool(ide[i]) if ide is not None and i < len(ide) else arb > CAN_SFF_MASK
                b = int(bus[i]) if bus is not None and i < len(bus) else 0
                rows.append(make_row(float(timestamps[i]), arb & CAN_EFF_MASK, extended, b, data))
    return _finish(rows)


# ── dispatch ─────────────────────────────────────────────────────────────────

def parse_log_file(filepath: str) -> pd.DataFrame:
    """Auto-detect the capture format from the suffix/header and parse it."""
    path = Path(filepath)
    suffix = path.suffix.lower()
    try:
        if suffix in (".rlog", ".qlog"):
            from canlab.core.openpilot_parser import parse_rlog
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
            return parse_candump_log(filepath)
        with open(filepath, encoding="utf-8-sig", errors="replace") as f:
            header = f.readline()
        if "Time Stamp" in header or "D1" in header:
            return parse_savvycan_csv(filepath)
        return parse_candump_log(filepath)
    except Exception as e:
        raise ValueError(f"Failed to parse {filepath}: {e}") from e


def _normalize_id(val) -> str:
    # Kept for backwards compatibility; canonical logic lives in core.canid.
    return normalize_id(val)
