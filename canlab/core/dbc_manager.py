import cantools
import pandas as pd
from pathlib import Path
from typing import Optional, List


def signal_dict_to_cantools(sig: dict) -> cantools.database.Signal:
    byte_order = "little_endian" if sig.get("byte_order", "little") == "little" else "big_endian"
    is_signed  = sig.get("value_type", "unsigned").lower() == "signed"
    return cantools.database.Signal(
        name        = sig.get("signal_name", "UnknownSignal"),
        start       = int(sig.get("start_bit", 0)),
        length      = int(sig.get("length", 8)),
        byte_order  = byte_order,
        is_signed   = is_signed,
        scale       = float(sig.get("scale", 1.0)),
        offset      = float(sig.get("offset", 0.0)),
        minimum     = float(sig.get("min_val", 0.0)) if sig.get("min_val") is not None else None,
        maximum     = float(sig.get("max_val", 255.0)) if sig.get("max_val") is not None else None,
        unit        = sig.get("unit", ""),
        comment     = sig.get("description", ""),
    )


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
            messages[msg_id] = {"name": msg_name, "signals": [], "length": 8}
        messages[msg_id]["signals"].append(sig)
        messages[msg_id]["length"] = max(
            messages[msg_id]["length"],
            int(sig.get("msg_length", 8)),
        )

    lines = ['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_:", ""]

    for msg_id, msg_data in sorted(messages.items()):
        lines.append(
            f'BO_ {msg_id} {msg_data["name"]}: {msg_data["length"]} Vector__XXX'
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
            lines.append(
                f" SG_ {sname} : {start}|{length}{bo}{signed}"
                f" ({scale},{offset}) [{mn}|{mx}] \"{unit}\" Vector__XXX"
            )
        lines.append("")

    # Signal comments
    for msg_id, msg_data in sorted(messages.items()):
        for sig in msg_data["signals"]:
            desc = (sig.get("description") or "").replace('"', "'")
            if desc:
                lines.append(
                    f'CM_ SG_ {msg_id} {sig.get("signal_name","SIG")} "{desc}";'
                )

    lines.append("")
    return "\n".join(lines)


def load_dbc(filepath: str) -> list[dict]:
    """Load a DBC file and return list of signal dicts."""
    db = cantools.database.load_file(filepath)
    result = []
    for msg in db.messages:
        for sig in msg.signals:
            result.append({
                "message_id":   format(msg.frame_id, "03X"),
                "message_name": msg.name,
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
            })
    return result


def decode_frame(signal_defs: list[dict], can_id: str, frame_bytes: bytes) -> dict:
    """Decode a single frame using signal definitions for matching ID."""
    matching = [s for s in signal_defs if s.get("message_id", "").upper() == can_id.upper()]
    if not matching:
        return {}
    db = cantools.database.Database()
    msg_id = int(can_id, 16)
    ct_sigs = [signal_dict_to_cantools(s) for s in matching]
    msg = cantools.database.Message(frame_id=msg_id, name="MSG", length=8, signals=ct_sigs)
    db.add_message(msg)
    try:
        return db.decode_message(msg_id, frame_bytes)
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
        msg_id   = int(sig.get("message_id", "0x000"), 16)
        msg_name = sig.get("message_name", f"MSG_{msg_id:03X}")
        if msg_id not in messages:
            messages[msg_id] = {"name": msg_name, "signals": [], "length": 8}
        messages[msg_id]["signals"].append(sig)
        messages[msg_id]["length"] = max(
            messages[msg_id]["length"],
            int(sig.get("msg_length", 8)),
        )
    db = cantools.database.Database()
    for msg_id, md in messages.items():
        ct_sigs = []
        for s in md["signals"]:
            try:
                ct_sigs.append(signal_dict_to_cantools(s))
            except Exception:
                pass
        db.add_message(cantools.database.Message(
            frame_id=msg_id, name=md["name"],
            length=md["length"], signals=ct_sigs,
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
