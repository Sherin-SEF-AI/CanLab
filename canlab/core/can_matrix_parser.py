"""
CAN Matrix Importer.

Reads OEM-style signal-definition spreadsheets (.xlsx, .csv) and returns
the same list[dict] schema as dbc_manager.load_dbc().

Column names are matched case-insensitively with fuzzy aliases so common
OEM naming variants are handled automatically.

Required columns (fuzzy matched):
  message_id   → "Message ID", "Msg ID", "CAN ID", "FRAME ID", ...
  signal_name  → "Signal Name", "Signal", "SPN Name", ...
  start_bit    → "Start Bit", "StartBit", "Bit Position", ...
  length       → "Length", "Bit Length", "Bits", ...

Optional columns:
  message_name → "Message Name", "Msg Name", "Frame Name"
  byte_order   → "Byte Order", "Endianness", "Intel/Motorola"
  value_type   → "Value Type", "Signed", "Type"
  scale        → "Scale", "Factor", "LSB"
  offset       → "Offset"
  min_val      → "Min", "Minimum"
  max_val      → "Max", "Maximum"
  unit         → "Unit", "Units"
  description  → "Description", "Comment", "Remark"
"""

import pandas as pd
from pathlib import Path

# ── Column alias maps ─────────────────────────────────────────────────────────

_ALIASES: dict[str, list[str]] = {
    "message_id":   ["message id", "msg id", "can id", "frame id", "msgid", "msg_id", "canid"],
    "message_name": ["message name", "msg name", "frame name", "message", "msg_name"],
    "signal_name":  ["signal name", "signal", "spn name", "sig name", "signame", "signal_name"],
    "start_bit":    ["start bit", "startbit", "bit position", "bit start", "start_bit"],
    "length":       ["length", "bit length", "bits", "len", "signal length"],
    "byte_order":   ["byte order", "endianness", "endian", "intel motorola", "byte_order"],
    "value_type":   ["value type", "signed", "type", "value_type"],
    "scale":        ["scale", "factor", "lsb", "resolution"],
    "offset":       ["offset"],
    "min_val":      ["min", "minimum", "min val", "min_val"],
    "max_val":      ["max", "maximum", "max val", "max_val"],
    "unit":         ["unit", "units", "engineering unit"],
    "description":  ["description", "comment", "remark", "note", "desc"],
}


def _map_columns(columns: list[str]) -> dict[str, str]:
    """Return {canonical_field: actual_column_name} for matched columns."""
    lower_map = {c.lower().strip(): c for c in columns}
    result    = {}
    for field, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in lower_map:
                result[field] = lower_map[alias]
                break
    return result


def _normalize_id(raw) -> str:
    """Normalize a message ID to uppercase 3-digit hex string."""
    if pd.isna(raw):
        return "000"
    s = str(raw).strip()
    try:
        if s.lower().startswith("0x"):
            val = int(s, 16)
        else:
            val = int(float(s))
        return f"{val:03X}"
    except (ValueError, TypeError):
        return "000"


def parse_can_matrix(filepath: str) -> list[dict]:
    """
    Parse an OEM CAN matrix spreadsheet and return list of signal dicts
    compatible with dbc_manager.load_dbc() output.

    Raises ValueError if required columns (message_id, signal_name, start_bit,
    length) cannot be found.
    """
    path = Path(filepath)
    if path.suffix.lower() in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(filepath, header=0, dtype=str)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(filepath, header=0, dtype=str)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")

    col_map = _map_columns(list(df.columns))

    # Validate required columns
    required = ["message_id", "signal_name", "start_bit", "length"]
    missing  = [f for f in required if f not in col_map]
    if missing:
        raise ValueError(
            f"Could not find required columns: {missing}.\n"
            f"Available columns: {list(df.columns)}"
        )

    def _get(row, field, default=""):
        col = col_map.get(field)
        if col is None:
            return default
        val = row.get(col, default)
        return "" if pd.isna(val) else str(val).strip()

    results = []
    for _, row in df.iterrows():
        msg_id = _normalize_id(_get(row, "message_id"))
        if msg_id == "000":
            continue

        sig_name = _get(row, "signal_name")
        if not sig_name:
            continue

        try:
            start_bit = int(float(_get(row, "start_bit", "0")))
            length    = int(float(_get(row, "length", "8")))
        except (ValueError, TypeError):
            continue

        # Byte order: look for "motorola" / "big" / "intel" / "little"
        bo_raw = _get(row, "byte_order", "little").lower()
        byte_order = "big" if any(k in bo_raw for k in ("motorola", "big", "msb")) else "little"

        # Value type
        vt_raw = _get(row, "value_type", "unsigned").lower()
        value_type = "signed" if "sign" in vt_raw else "unsigned"

        def _float(field, default=0.0):
            try:
                return float(_get(row, field, str(default)))
            except (ValueError, TypeError):
                return default

        results.append({
            "message_id":   msg_id,
            "message_name": _get(row, "message_name", f"MSG_{msg_id}"),
            "signal_name":  sig_name,
            "start_bit":    start_bit,
            "length":       length,
            "byte_order":   byte_order,
            "value_type":   value_type,
            "scale":        _float("scale", 1.0),
            "offset":       _float("offset", 0.0),
            "min_val":      _float("min_val", 0.0),
            "max_val":      _float("max_val", 255.0),
            "unit":         _get(row, "unit", ""),
            "description":  _get(row, "description", ""),
        })

    return results
