import cantools
import pandas as pd
from pathlib import Path
from typing import Optional, List

from core.canid import normalize_id


def _db_add_message(db, msg) -> None:
    """Add a message to a cantools Database across versions.

    cantools 40 renamed the public ``add_message`` to ``_add_message``. Prefer
    whichever exists so decoding keeps working on both old and new cantools.
    """
    adder = getattr(db, "add_message", None) or getattr(db, "_add_message")
    adder(msg)


def signal_dict_to_cantools(sig: dict) -> cantools.database.Signal:
    """Build a cantools Signal from our signal dict, across cantools versions.

    cantools 40 removed the ``scale``/``offset``/``comment`` constructor kwargs
    in favour of a ``conversion`` object. Passing the old kwargs raised
    ``TypeError``, which every decode path swallowed — so on cantools ≥ 40 (what
    ``cantools>=39`` actually resolves to) DBC decoding silently produced nothing.
    This adapter uses the conversion API when present and falls back to the
    legacy kwargs otherwise.
    """
    byte_order = "little_endian" if sig.get("byte_order", "little") == "little" else "big_endian"
    is_signed  = sig.get("value_type", "unsigned").lower() == "signed"
    scale      = float(sig.get("scale", 1.0))
    offset     = float(sig.get("offset", 0.0))
    minimum    = float(sig["min_val"]) if sig.get("min_val") is not None else None
    maximum    = float(sig["max_val"]) if sig.get("max_val") is not None else None
    name       = sig.get("signal_name", "UnknownSignal")
    unit       = sig.get("unit", "")

    try:
        from cantools.database.conversion import BaseConversion
        conversion = BaseConversion.factory(scale=scale, offset=offset)
        return cantools.database.Signal(
            name=name, start=int(sig.get("start_bit", 0)),
            length=int(sig.get("length", 8)), byte_order=byte_order,
            is_signed=is_signed, conversion=conversion,
            minimum=minimum, maximum=maximum, unit=unit,
        )
    except ImportError:
        # cantools < 40 — legacy scalar kwargs.
        return cantools.database.Signal(
            name=name, start=int(sig.get("start_bit", 0)),
            length=int(sig.get("length", 8)), byte_order=byte_order,
            is_signed=is_signed, scale=scale, offset=offset,
            minimum=minimum, maximum=maximum, unit=unit,
            comment=sig.get("description", ""),
        )


def _min_msg_length(sig: dict) -> int:
    """Smallest DLC that can hold this signal's bits (fallback when unknown)."""
    try:
        return max(1, (int(sig.get("start_bit", 0)) + int(sig.get("length", 8)) + 7) // 8)
    except (ValueError, TypeError):
        return 8


def _dbc_frame_id(msg_id: int, extended: bool) -> int:
    """Encode the DBC BO_ frame id, setting bit 31 for extended (29-bit) IDs.

    cantools and Vector tools flag an extended frame by OR-ing 0x80000000 into
    the BO_ id. Without this, a 29-bit ID like 0x18FEF100 round-trips as a
    standard 11-bit frame and collides with unrelated IDs.
    """
    return (msg_id | 0x80000000) if extended else msg_id


def signals_to_dbc_string(signal_defs: list[dict]) -> str:
    """Convert list of signal dicts to a DBC file string."""
    messages: dict[int, dict] = {}
    for sig in signal_defs:
        try:
            msg_id = int(sig.get("message_id", "0"), 16)
        except (ValueError, TypeError):
            msg_id = 0
        msg_name = sig.get("message_name", f"MSG_{msg_id:03X}")
        if msg_id not in messages:
            messages[msg_id] = {"name": msg_name, "signals": [],
                                "length": 0, "extended": False}
        messages[msg_id]["signals"].append(sig)
        # Preserve the real message length when known (imports set msg_length);
        # otherwise grow to fit the widest signal instead of forcing 8, so short
        # and CAN-FD (>8) messages survive the round-trip.
        declared = sig.get("msg_length")
        length = int(declared) if declared else _min_msg_length(sig)
        messages[msg_id]["length"] = max(messages[msg_id]["length"], length)
        if sig.get("extended") or msg_id > 0x7FF:
            messages[msg_id]["extended"] = True

    lines = ['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_:", ""]

    for msg_id, msg_data in sorted(messages.items()):
        frame_id = _dbc_frame_id(msg_id, msg_data["extended"])
        length   = msg_data["length"] or 8
        lines.append(
            f'BO_ {frame_id} {msg_data["name"]}: {length} Vector__XXX'
        )
        for sig in msg_data["signals"]:
            sname  = sig.get("signal_name", "SIG")
            start  = int(sig.get("start_bit", 0))
            length = int(sig.get("length", 8))
            bo     = "@1" if sig.get("byte_order", "little") == "little" else "@0"
            signed = "+" if sig.get("value_type", "unsigned") == "unsigned" else "-"
            scale  = float(sig.get("scale", 1.0))
            offset = float(sig.get("offset", 0.0))
            mn     = sig.get("min_val") or 0
            mx     = sig.get("max_val") or 0
            unit   = sig.get("unit", "")
            # Multiplexing: "M" marks the multiplexor selector, "m<n>" marks a
            # signal that is only present when the selector equals n.
            if sig.get("mux_role") == "M":
                mux = " M"
            elif sig.get("mux_value") is not None:
                mux = f" m{int(sig['mux_value'])}"
            else:
                mux = ""
            lines.append(
                f" SG_ {sname}{mux} : {start}|{length}{bo}{signed}"
                f" ({scale},{offset}) [{mn}|{mx}] \"{unit}\" Vector__XXX"
            )
        lines.append("")

    # Signal comments
    for msg_id, msg_data in sorted(messages.items()):
        frame_id = _dbc_frame_id(msg_id, msg_data["extended"])
        for sig in msg_data["signals"]:
            desc = (sig.get("description") or "").replace('"', "'")
            if desc:
                lines.append(
                    f'CM_ SG_ {frame_id} {sig.get("signal_name","SIG")} "{desc}";'
                )

    lines.append("")
    return "\n".join(lines)


def load_dbc(filepath: str) -> list[dict]:
    """Load a DBC file and return list of signal dicts."""
    db = cantools.database.load_file(filepath)
    result = []
    for msg in db.messages:
        is_ext = bool(getattr(msg, "is_extended_frame", False))
        for sig in msg.signals:
            result.append({
                "message_id":   format(msg.frame_id, "03X"),
                "message_name": msg.name,
                "msg_length":   msg.length,        # preserve DLC across round-trips
                "extended":     is_ext,            # preserve 29-bit frame flag
                "signal_name":  sig.name,
                "start_bit":    sig.start,
                "length":       sig.length,
                "byte_order":   "little" if sig.byte_order == "little_endian" else "big",
                "value_type":   "signed" if sig.is_signed else "unsigned",
                "scale":        sig.scale,
                "offset":       sig.offset,
                "min_val":      sig.minimum,
                "max_val":      sig.maximum,
                "unit":         sig.unit or "",
                "description":  sig.comment or "",
                "mux_role":     "M" if getattr(sig, "is_multiplexer", False) else None,
                "mux_value":    (sig.multiplexer_ids[0]
                                 if getattr(sig, "multiplexer_ids", None) else None),
            })
    return result


def decode_frame(signal_defs: list[dict], can_id: str, frame_bytes: bytes) -> dict:
    """Decode a single frame using signal definitions for matching ID."""
    from core.canid import normalize_id
    target = normalize_id(can_id)
    matching = [s for s in signal_defs
                if normalize_id(s.get("message_id", "")) == target]
    if not matching:
        return {}
    db = cantools.database.Database()
    msg_id = int(normalize_id(can_id), 16)
    ct_sigs = [signal_dict_to_cantools(s) for s in matching]
    length = max((_min_msg_length(s) for s in matching), default=8)
    declared = [int(s["msg_length"]) for s in matching if s.get("msg_length")]
    if declared:
        length = max(declared)
    msg = cantools.database.Message(frame_id=msg_id, name="MSG",
                                    length=length, signals=ct_sigs)
    _db_add_message(db, msg)
    try:
        return db.decode_message(msg_id, frame_bytes, decode_choices=False)
    except Exception:
        return {}


def build_db_from_signals(signal_defs: list[dict]) -> Optional[cantools.database.Database]:
    """
    Build and cache a cantools.Database from the current dbc_signals list.
    Stores result in state.dbc_db and emits dbc_db_updated.
    Call this whenever dbc_signals changes (load_dbc, DBC Builder add/edit/remove).
    """
    if not signal_defs:
        return None
    messages: dict = {}
    for sig in signal_defs:
        try:
            msg_id = int(normalize_id(sig.get("message_id", "0")), 16)
        except (ValueError, TypeError):
            msg_id = 0
        msg_name = sig.get("message_name", f"MSG_{msg_id:03X}")
        if msg_id not in messages:
            messages[msg_id] = {"name": msg_name, "signals": [],
                                "length": 0, "extended": False}
        messages[msg_id]["signals"].append(sig)
        declared = sig.get("msg_length")
        length = int(declared) if declared else _min_msg_length(sig)
        messages[msg_id]["length"] = max(messages[msg_id]["length"], length)
        if sig.get("extended") or msg_id > 0x7FF:
            messages[msg_id]["extended"] = True
    db = cantools.database.Database()
    for msg_id, md in messages.items():
        ct_sigs = []
        for s in md["signals"]:
            try:
                ct_sigs.append(signal_dict_to_cantools(s))
            except Exception:
                pass
        _db_add_message(db, cantools.database.Message(
            frame_id=msg_id, name=md["name"],
            length=md["length"] or 8, signals=ct_sigs,
            is_extended_frame=md["extended"],
        ))
    try:
        from core.state import get_state
        state = get_state()
        state.dbc_db = db
        state.dbc_db_updated.emit()
    except Exception:
        pass
    return db


def decode_frame_fast(can_id: str, frame_bytes: bytes) -> dict:
    """
    Decode using cached state.dbc_db (faster than decode_frame — no DB rebuild).
    Falls back to decode_frame if cache is missing.
    """
    try:
        from core.state import get_state
        db = get_state().dbc_db
        if db:
            msg_id = int(can_id, 16)
            return db.decode_message(msg_id, frame_bytes)
    except Exception:
        pass
    try:
        from core.state import get_state
        return decode_frame(get_state().dbc_signals, can_id, frame_bytes)
    except Exception:
        return {}


def export_opendbc(signal_defs: list[dict], msg_meta: Optional[dict] = None) -> str:
    """Export signals in opendbc / comma.ai format."""
    from core.openpilot_export import to_opendbc_string
    return to_opendbc_string(signal_defs, msg_meta)


def validate_signals(signal_defs: list[dict]) -> list[str]:
    """Return list of validation errors."""
    errors = []
    for i, sig in enumerate(signal_defs):
        name = sig.get("signal_name", f"Signal {i}")
        start = int(sig.get("start_bit", 0))
        length = int(sig.get("length", 1))
        if start < 0 or start > 63:
            errors.append(f"{name}: start_bit {start} out of range [0,63]")
        if length < 1 or length > 64:
            errors.append(f"{name}: length {length} out of range [1,64]")
        if start + length > 64:
            errors.append(f"{name}: start_bit+length exceeds 64 bits")
    return errors
