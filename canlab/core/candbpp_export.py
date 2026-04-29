"""Vector CANdb++ export — DBC + attribute extensions.

Builds on signals_to_dbc_string() and appends BA_DEF_ / BA_DEF_DEF_ / BA_
attribute blocks that CANdb++ (and most modern DBC parsers including cantools)
expect for cycle-time and init-value metadata.

msg_meta shape (same as HYUNDAI_MSG_META in openpilot_export.py, extended):
  {hex_id_str: {"cycle_time_ms": int, "has_counter": bool, "has_checksum": bool}}
"""

from core.dbc_manager import signals_to_dbc_string


def to_candbpp_string(signal_defs: list[dict],
                      msg_meta: dict | None = None) -> str:
    """Return a Vector CANdb++ .dbc string."""
    msg_meta = msg_meta or {}

    # Start with the standard DBC body
    base = signals_to_dbc_string(signal_defs)

    # Collect unique message IDs and signal names for attribute entries
    messages: dict[int, dict] = {}
    all_signals: list[tuple[int, str]] = []   # (msg_id_int, signal_name)
    for sig in signal_defs:
        try:
            mid = int(sig.get("message_id", "0"), 16)
        except (ValueError, TypeError):
            mid = 0
        if mid not in messages:
            messages[mid] = {
                "hex": sig.get("message_id", f"{mid:03X}"),
                "cycle_ms": 0,
            }
        sname = sig.get("signal_name", "")
        if sname:
            all_signals.append((mid, sname))

    # Merge cycle-time from msg_meta
    for hex_id, meta in msg_meta.items():
        try:
            mid = int(hex_id, 16)
        except (ValueError, TypeError):
            continue
        if mid in messages:
            messages[mid]["cycle_ms"] = meta.get("cycle_time_ms", 0)

    lines: list[str] = []

    # ── BA_DEF_ ───────────────────────────────────────────────────────────────
    lines.append('\nBA_DEF_ BU_  "NodeLayerModules" STRING;')
    lines.append('BA_DEF_ BO_  "GenMsgCycleTime" INT 0 65535;')
    lines.append('BA_DEF_ BO_  "GenMsgSendType" STRING;')
    lines.append('BA_DEF_ SG_  "GenSigStartValue" FLOAT -1e+038 1e+038;')
    lines.append('BA_DEF_ SG_  "GenSigSendType" STRING;')

    # ── BA_DEF_DEF_ ───────────────────────────────────────────────────────────
    lines.append('\nBA_DEF_DEF_  "NodeLayerModules" "";')
    lines.append('BA_DEF_DEF_  "GenMsgCycleTime" 0;')
    lines.append('BA_DEF_DEF_  "GenMsgSendType" "cyclic";')
    lines.append('BA_DEF_DEF_  "GenSigStartValue" 0;')
    lines.append('BA_DEF_DEF_  "GenSigSendType" "cyclic";')

    # ── BA_ per message ───────────────────────────────────────────────────────
    lines.append("")
    for mid, mdata in sorted(messages.items()):
        cycle = mdata["cycle_ms"]
        lines.append(f'BA_ "GenMsgCycleTime" BO_ {mid} {cycle};')
        lines.append(f'BA_ "GenMsgSendType" BO_ {mid} "cyclic";')

    # ── BA_ per signal ────────────────────────────────────────────────────────
    for mid, sname in all_signals:
        lines.append(f'BA_ "GenSigStartValue" SG_ {mid} {sname} 0;')
        lines.append(f'BA_ "GenSigSendType" SG_ {mid} {sname} "cyclic";')

    return base + "\n".join(lines) + "\n"
