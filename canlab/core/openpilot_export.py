"""Export DBC in strict opendbc / commaai format with checksum/counter annotations."""
from typing import Optional


_CHECKSUM_COMMENT = 'checksum "xor8" {count} {start}'
_COUNTER_COMMENT  = 'counter {start} 4 big_endian'


def _make_val_table(choices: dict) -> str:
    """Turn {0: 'OFF', 1: 'ON'} into VAL_ entry pieces."""
    pairs = " ".join(f'{k} "{v}"' for k, v in sorted(choices.items()))
    return pairs


def to_opendbc_string(signal_defs: list, msg_meta: Optional[dict] = None) -> str:
    """
    Produce a DBC string in opendbc / comma.ai convention.

    msg_meta: {msg_id_hex -> {"checksum_byte": int, "counter_nibble": int}}
    ``msg_meta`` is normally built by
    :func:`canlab.core.vehicle_profile.message_meta` from the selected profile.
    """
    if msg_meta is None:
        msg_meta = {}

    # Group signals by message. The key is the DBC frame id, which carries
    # 0x80000000 for a 29-bit message: without it cantools and opendbc both
    # refuse the file, because a bare 0x9F11202 is not a valid standard id.
    # Every BO_, CM_ and VAL_ line has to use the same number.
    from canlab.core.dbc_manager import dbc_frame_id, frame_id_int
    messages: dict[int, dict] = {}
    for sig in signal_defs:
        try:
            mid = dbc_frame_id(sig)
        except (ValueError, TypeError):
            mid = 0
        if mid not in messages:
            messages[mid] = {
                "name":    sig.get("message_name", f"MSG_{frame_id_int(sig):03X}"),
                "signals": [],
                "length":  int(sig.get("msg_length", 8)),
            }
        messages[mid]["signals"].append(sig)

    lines: list[str] = []
    lines.append('VERSION ""')
    lines.append("")
    lines.append("NS_ :")
    lines.append("")
    lines.append("BS_:")   # bit-timing section must be empty (or baud:btr1,btr2)
    lines.append("")
    lines.append("BU_: VECTOR__INDEPENDENT")
    lines.append("")

    val_lines: list[str] = []
    comment_lines: list[str] = []

    for mid, mdata in sorted(messages.items()):
        mname   = mdata["name"]
        mlen    = mdata["length"]
        sigs    = mdata["signals"]

        lines.append(f"BO_ {mid} {mname}: {mlen} VECTOR__INDEPENDENT")

        for sig in sigs:
            sname   = sig.get("signal_name", "UnknownSignal")
            start   = int(sig.get("start_bit", 0))
            length  = int(sig.get("length", 8))
            border  = "@1" if sig.get("byte_order", "little") == "little" else "@0"
            signed  = "-" if sig.get("value_type", "unsigned").lower() == "signed" else "+"
            scale   = float(sig.get("scale", 1.0))
            offset  = float(sig.get("offset", 0.0))
            minv    = float(sig.get("min_val", 0.0)) if sig.get("min_val") is not None else 0.0
            maxv    = float(sig.get("max_val", 255.0)) if sig.get("max_val") is not None else 255.0
            unit    = sig.get("unit", "") or ""
            mux     = ""  # future: multiplexer support

            # The signedness token (+/-) is mandatory: cantools/opendbc's SG_
            # regex requires @[01][+-]; omitting it makes every file unparseable.
            lines.append(
                f" SG_ {sname} {mux}: {start}|{length}{border}{signed} "
                f"({scale},{offset}) [{minv}|{maxv}] \"{unit}\" VECTOR__INDEPENDENT"
            )

            desc = sig.get("description", "")
            if desc:
                comment_lines.append(
                    f'CM_ SG_ {mid} {sname} "{desc}";'
                )

            choices = sig.get("value_table", {})
            if choices:
                pairs = _make_val_table(choices)
                val_lines.append(f"VAL_ {mid} {sname} {pairs};")

        # opendbc-specific comment block for checksum/counter
        meta = msg_meta.get(format(mid, "03X"), msg_meta.get(mid, {}))
        if meta.get("has_checksum"):
            cs_byte  = meta.get("checksum_byte", 7)
            cs_start = cs_byte * 8
            comment_lines.append(
                f'CM_ BO_ {mid} "checksum_start_bit:{cs_start} checksum_size:8 '
                f'checksum_type:xor8";'
            )
        if meta.get("has_counter"):
            ctr_byte = meta.get("counter_byte", 0)
            comment_lines.append(
                f'CM_ BO_ {mid} "counter_start_bit:{ctr_byte * 8 + 4} counter_size:4";'
            )

        lines.append("")

    # Append CM_ lines
    if comment_lines:
        lines.extend(comment_lines)
        lines.append("")

    # Append VAL_ lines
    if val_lines:
        lines.extend(val_lines)
        lines.append("")

    return "\n".join(lines)
