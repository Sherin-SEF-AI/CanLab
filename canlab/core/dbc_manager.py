"""DBC signal-definition model: the single path between CanLab's signal dicts
and cantools.

Every decode/encode goes through ``signals_to_dbc_string`` →
``cantools.database.load_string``. That keeps one exporter honest (whatever we
can decode we can also write to disk and reload) and avoids the cantools
constructor API, which changes between releases.

Canonical signal dict keys: message_id (uppercase hex str, no 0x), message_name,
signal_name, start_bit (DBC numbering), length, byte_order "little"|"big",
value_type "unsigned"|"signed", scale, offset, min_val, max_val, unit,
description. Optional: msg_length, extended, mux_role "M", mux_value,
value_table {raw: label}.
"""
from __future__ import annotations

import functools
import logging
import math
import re
from typing import Iterable, Optional

import cantools
import numpy as np
import pandas as pd

from canlab.core.canid import normalize_id

log = logging.getLogger(__name__)

EXTENDED_FLAG = 0x80000000
BYTE_COLS = [f"B{i}" for i in range(64)]
_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


# ── normalisation helpers ─────────────────────────────────────────────────────

def dbc_identifier(name, fallback: str = "SIG") -> str:
    """Make ``name`` a legal DBC identifier ([A-Za-z_][A-Za-z0-9_]*)."""
    s = _IDENT_RE.sub("_", str(name or "").strip())
    if not s:
        s = fallback
    if s[0].isdigit():
        s = "_" + s
    return s


def dbc_string(text) -> str:
    """Escape free text for a DBC double-quoted string."""
    return (str(text or "")
            .replace("\\", "\\\\").replace('"', '\\"')
            .replace("\r", " ").replace("\n", " "))


def norm_signal(sig: dict) -> dict:
    """Return a copy of ``sig`` with canonical key values (case, types, id form)."""
    out = dict(sig)
    bo = str(sig.get("byte_order", "little") or "little").lower()
    out["byte_order"] = "big" if bo in ("big", "big_endian", "motorola", "msb", "0") else "little"
    vt = str(sig.get("value_type", "unsigned") or "unsigned").lower()
    out["value_type"] = "signed" if vt in ("signed", "int", "s", "-") else "unsigned"
    out["message_id"] = normalize_id(sig.get("message_id", "0") or "0")
    start = sig.get("start_bit", 0)
    length = sig.get("length", 8)
    out["start_bit"] = 0 if start in (None, "") else int(start)
    out["length"] = 8 if length in (None, "") else int(length)
    scale = sig.get("scale", 1.0)
    out["scale"] = float(scale) if scale not in (None, 0, "") else 1.0
    out["offset"] = float(sig.get("offset", 0.0) or 0.0)
    fid = frame_id_int(out)
    out["extended"] = bool(sig.get("extended", False)) or fid > 0x7FF
    return out


def frame_id_int(sig: dict) -> int:
    try:
        return int(normalize_id(sig.get("message_id", "0") or "0"), 16)
    except (ValueError, TypeError):
        return 0


def dbc_frame_id(sig: dict) -> int:
    """The BO_ id: the raw id with the 0x80000000 flag for 29-bit messages."""
    fid = frame_id_int(sig)
    extended = bool(sig.get("extended", False)) or fid > 0x7FF
    return (fid | EXTENDED_FLAG) if extended else fid


def _needed_bytes(sig: dict) -> int:
    """Bytes a signal needs the message to have (little-endian exact, big-endian bound)."""
    start, length = sig["start_bit"], sig["length"]
    if sig["byte_order"] == "little":
        return math.ceil((start + length) / 8)
    # Motorola: walk the sawtooth to the last bit.
    bit = start
    for _ in range(length - 1):
        bit = bit + 15 if bit % 8 == 0 else bit - 1
    return (bit // 8) + 1


# ── DBC text ─────────────────────────────────────────────────────────────────

def _group_messages(signal_defs: Iterable[dict]) -> dict[int, dict]:
    messages: dict[int, dict] = {}
    for raw in signal_defs:
        sig = norm_signal(raw)
        fid = dbc_frame_id(sig)
        if fid not in messages:
            messages[fid] = {
                "name": dbc_identifier(sig.get("message_name"), f"MSG_{sig['message_id']}"),
                "signals": [],
                # Grown from the signals and any declared msg_length. Starting
                # at 8 forced every message to 8 bytes on export, so a 3-byte
                # message re-imported as 8.
                "length": 0,
                "used_names": set(),
            }
        m = messages[fid]
        sname = dbc_identifier(sig.get("signal_name"), "SIG")
        base, n = sname, 2
        while sname in m["used_names"]:
            sname = f"{base}_{n}"
            n += 1
        m["used_names"].add(sname)
        sig["_dbc_name"] = sname
        m["signals"].append(sig)
        declared = int(sig.get("msg_length") or 0)
        m["length"] = max(m["length"], declared, _needed_bytes(sig))
    for m in messages.values():
        m["length"] = max(1, m["length"])       # a DBC message cannot be 0 bytes
    return messages


def signals_to_dbc_string(signal_defs: list[dict]) -> str:
    """Render signal dicts as a cantools-loadable DBC file."""
    messages = _group_messages(signal_defs)
    lines = ['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_:", ""]

    for fid, m in sorted(messages.items()):
        lines.append(f'BO_ {fid} {m["name"]}: {m["length"]} Vector__XXX')
        for sig in m["signals"]:
            bo = "@1" if sig["byte_order"] == "little" else "@0"
            signed = "-" if sig["value_type"] == "signed" else "+"
            mn = sig.get("min_val")
            mx = sig.get("max_val")
            mn = 0 if mn is None or mn == "" else mn
            mx = 0 if mx is None or mx == "" else mx
            # Multiplexing: "M" marks the multiplexor selector, "m<n>" marks a
            # signal that is only present when the selector equals n.
            if sig.get("mux_role") == "M":
                mux = " M"
            elif sig.get("mux_value") is not None:
                mux = f" m{int(sig['mux_value'])}"
            else:
                mux = ""
            lines.append(
                f' SG_ {sig["_dbc_name"]}{mux} : {sig["start_bit"]}|{sig["length"]}{bo}{signed}'
                f' ({sig["scale"]},{sig["offset"]}) [{mn}|{mx}]'
                f' "{dbc_string(sig.get("unit", ""))}" Vector__XXX'
            )
        lines.append("")

    for fid, m in sorted(messages.items()):
        for sig in m["signals"]:
            desc = dbc_string(sig.get("description") or "")
            if desc:
                lines.append(f'CM_ SG_ {fid} {sig["_dbc_name"]} "{desc}";')
    lines.append("")

    for fid, m in sorted(messages.items()):
        for sig in m["signals"]:
            table = sig.get("value_table") or {}
            if table:
                pairs = " ".join(f'{int(k)} "{dbc_string(v)}"' for k, v in sorted(table.items(), key=lambda kv: int(kv[0])))
                lines.append(f'VAL_ {fid} {sig["_dbc_name"]} {pairs} ;')
    lines.append("")
    return "\n".join(lines)


# ── cantools database ────────────────────────────────────────────────────────

def build_database(signal_defs: list[dict]) -> cantools.database.Database:
    """Build a cantools Database from signal dicts (raises ValueError on bad input)."""
    text = signals_to_dbc_string(signal_defs)
    try:
        return cantools.database.load_string(text, database_format="dbc")
    except Exception as e:  # cantools raises several error types
        raise ValueError(f"Signal definitions do not form a valid DBC: {e}") from e


def _hashable(v):
    if isinstance(v, dict):
        return tuple(sorted((str(k), _hashable(x)) for k, x in v.items()))
    if isinstance(v, (list, tuple, set)):
        return tuple(_hashable(x) for x in v)
    if isinstance(v, float) and math.isnan(v):
        return "nan"
    return v


def _freeze(signal_defs: Iterable[dict]) -> tuple:
    return tuple(tuple(sorted((k, _hashable(v)) for k, v in d.items())) for d in signal_defs)


def _thaw(k, v):
    # _freeze turns the value_table dict into a tuple of pairs; restore it.
    if k == "value_table" and isinstance(v, tuple):
        return {int(a): b for a, b in v}
    return v


@functools.lru_cache(maxsize=64)
def _db_for_key(key: tuple) -> cantools.database.Database:
    defs = [{k: _thaw(k, v) for k, v in items} for items in key]
    return build_database(defs)


def database_for(signal_defs: list[dict]) -> cantools.database.Database:
    """Cached Database for exactly these signal dicts (safe to call per frame)."""
    return _db_for_key(_freeze(signal_defs))


def get_db(state=None) -> Optional[cantools.database.Database]:
    """The app-wide cached Database for ``state.dbc_signals`` (None when empty).

    The cache lives on ``state.dbc_db``; AppState clears it whenever
    ``dbc_updated`` fires, so this rebuilds lazily on first use after a change
    and emits ``dbc_db_updated``.
    """
    if state is None:
        from canlab.core.state import get_state
        state = get_state()
    if not state.dbc_signals:
        state.dbc_db = None
        return None
    if state.dbc_db is None:
        state.dbc_db = build_database(state.dbc_signals)
        try:
            state.dbc_db_updated.emit()
        except Exception:
            log.debug("dbc_db_updated emit failed", exc_info=True)
    return state.dbc_db


# ── decode / encode ──────────────────────────────────────────────────────────

def _lookup_id(can_id) -> int:
    return int(normalize_id(can_id), 16) if isinstance(can_id, str) else int(can_id)


def decode_frame(signal_defs: list[dict], can_id, frame_bytes: bytes) -> dict:
    """Decode one frame against the given signal dicts; {} if the id is unknown."""
    fid = _lookup_id(can_id)
    matching = [s for s in signal_defs if frame_id_int(s) == fid]
    if not matching:
        return {}
    try:
        db = database_for(matching)
        return db.decode_message(fid, bytes(frame_bytes), decode_choices=False,
                                 allow_truncated=True)
    except Exception:
        log.debug("decode_frame failed for 0x%X", fid, exc_info=True)
        return {}


def frame_bytes_from_row(row, n: int = 8) -> bytes:
    """Bytes for a frames_df row (NaN bytes -> 0), ``n`` bytes long."""
    out = bytearray(n)
    for i in range(n):
        v = row.get(f"B{i}") if hasattr(row, "get") else None
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            try:
                out[i] = int(v) & 0xFF
            except (ValueError, TypeError):
                pass
    return bytes(out)


def decode_series(signal_defs: list[dict], can_id, frames: pd.DataFrame) -> pd.DataFrame:
    """Decode every row of a per-ID frame slice.

    Returns a DataFrame with ``Timestamp`` plus one column per decoded signal
    (NaN where a row failed to decode); empty when nothing matches.
    """
    fid = _lookup_id(can_id)
    matching = [s for s in signal_defs if frame_id_int(s) == fid]
    if not matching or frames is None or frames.empty:
        return pd.DataFrame()
    try:
        db = database_for(matching)
        msg = db.get_message_by_frame_id(fid)
    except Exception:
        log.debug("decode_series: no database for 0x%X", fid, exc_info=True)
        return pd.DataFrame()
    cols = [c for c in BYTE_COLS[:msg.length] if c in frames.columns]
    raw = frames[cols].to_numpy(dtype=float)
    raw = np.nan_to_num(raw, nan=0.0).astype(np.uint8)
    ts = frames["Timestamp"].to_numpy(dtype=float)
    records = []
    for i in range(len(frames)):
        try:
            d = db.decode_message(fid, raw[i].tobytes(), decode_choices=False,
                                  allow_truncated=True)
        except Exception:
            d = {}
        records.append({"Timestamp": ts[i], **{k: float(v) for k, v in d.items()
                                                if isinstance(v, (int, float))}})
    return pd.DataFrame(records)


def encode_frame(signal_defs: list[dict], can_id, values: dict) -> bytes:
    """Encode physical ``values`` ({signal_name: value}) into a frame payload.

    Signals of the message not present in ``values`` are encoded as raw 0.
    Values are not range-checked (injection sweeps deliberately go out of range).
    """
    fid = _lookup_id(can_id)
    matching = [s for s in signal_defs if frame_id_int(s) == fid]
    if not matching:
        raise ValueError(f"No signal definitions for id 0x{fid:X}")
    db = database_for(matching)
    msg = db.get_message_by_frame_id(fid)
    full = {}
    for s in msg.signals:
        full[s.name] = s.offset if s.offset is not None else 0
    # accept either the sanitised DBC name or the original signal_name
    name_map = {dbc_identifier(s.get("signal_name"), "SIG"): dbc_identifier(s.get("signal_name"), "SIG")
                for s in matching}
    for k, v in values.items():
        key = k if k in full else name_map.get(dbc_identifier(k, "SIG"), k)
        full[key] = v
    return bytes(msg.encode(full, scaling=True, padding=False, strict=False))


# ── load / round trip ────────────────────────────────────────────────────────

def _message_to_signal_dicts(db: cantools.database.Database) -> list[dict]:
    result = []
    for msg in db.messages:
        extended = bool(msg.is_extended_frame)
        mid = format(msg.frame_id, "08X" if extended else "03X")
        for sig in msg.signals:
            d = {
                "message_id":   mid,
                "message_name": msg.name,
                "msg_length":   int(msg.length),
                "extended":     extended,
                "signal_name":  sig.name,
                "start_bit":    int(sig.start),
                "length":       int(sig.length),
                "byte_order":   "little" if sig.byte_order == "little_endian" else "big",
                "value_type":   "signed" if sig.is_signed else "unsigned",
                "scale":        float(sig.scale),
                "offset":       float(sig.offset),
                "min_val":      sig.minimum,
                "max_val":      sig.maximum,
                "unit":         sig.unit or "",
                "description":  sig.comment or "",
            }
            if sig.choices:
                d["value_table"] = {int(k): str(v) for k, v in sig.choices.items()}
            if sig.is_multiplexer:
                d["mux_role"] = "M"
            elif sig.multiplexer_ids:
                d["mux_value"] = int(sig.multiplexer_ids[0])
            result.append(d)
    return result


def load_dbc(filepath: str) -> list[dict]:
    """Load a DBC (or any cantools-readable file) into signal dicts."""
    db = cantools.database.load_file(filepath)
    return _message_to_signal_dicts(db)


def export_opendbc(signal_defs: list[dict], msg_meta: Optional[dict] = None) -> str:
    """Export signals in opendbc / comma.ai format."""
    from canlab.core.openpilot_export import to_opendbc_string
    return to_opendbc_string(signal_defs, msg_meta)


def validate_signals(signal_defs: list[dict]) -> list[str]:
    """Return human-readable validation errors (empty list = valid)."""
    errors: list[str] = []
    for i, raw in enumerate(signal_defs):
        try:
            sig = norm_signal(raw)
        except Exception as e:
            errors.append(f"Signal {i}: unreadable definition ({e})")
            continue
        name = raw.get("signal_name") or f"Signal {i}"
        msg_bits = max(8, int(raw.get("msg_length", 8) or 8)) * 8
        if not 0 <= sig["start_bit"] < msg_bits:
            errors.append(f"{name}: start_bit {sig['start_bit']} out of range [0,{msg_bits - 1}]")
            continue
        if not 1 <= sig["length"] <= 64:
            errors.append(f"{name}: length {sig['length']} out of range [1,64]")
            continue
        if _needed_bytes(sig) * 8 > msg_bits:
            errors.append(f"{name}: bits exceed the {msg_bits // 8}-byte message")
            continue
        try:
            build_database([sig])
        except ValueError as e:
            errors.append(f"{name}: {e}")
    return errors
