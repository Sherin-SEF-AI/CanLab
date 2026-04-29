"""AUTOSAR 4.3 System Template ARXML exporter.

Produces a minimal but valid ARXML file containing:
  I-SIGNAL, I-SIGNAL-I-PDU, CAN-FRAME, COMPU-METHOD per signal.

Input schema (same as load_dbc / parse_can_matrix):
  message_id (hex str), message_name, signal_name, start_bit, length,
  byte_order, value_type, scale, offset, min_val, max_val, unit, description.
"""
import xml.etree.ElementTree as ET
from xml.dom import minidom


_NS = "http://autosar.org/schema/r4.3"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"
_SCHEMA = "http://autosar.org/schema/r4.3 AUTOSAR_4-3-0.xsd"


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def to_arxml_string(signal_defs: list[dict], msg_meta: dict | None = None) -> str:
    """Return an ARXML 4.3 string for the given signal definitions."""
    msg_meta = msg_meta or {}

    root = ET.Element("AUTOSAR", {
        "xmlns":              _NS,
        "xmlns:xsi":          _XSI,
        "xsi:schemaLocation": _SCHEMA,
    })

    # Group signals by message
    messages: dict[int, dict] = {}
    for sig in signal_defs:
        try:
            mid = int(sig.get("message_id", "0"), 16)
        except (ValueError, TypeError):
            mid = 0
        if mid not in messages:
            messages[mid] = {
                "name":    sig.get("message_name", f"MSG_{mid:03X}"),
                "signals": [],
                "length":  int(sig.get("msg_length", 8)),
            }
        messages[mid]["signals"].append(sig)
        messages[mid]["length"] = max(
            messages[mid]["length"], int(sig.get("msg_length", 8))
        )

    pkgs = _sub(root, "AR-PACKAGES")

    # ── Signals package ───────────────────────────────────────────────────────
    sig_pkg = _sub(pkgs, "AR-PACKAGE")
    _sub(sig_pkg, "SHORT-NAME", "Signals")
    sig_elements = _sub(sig_pkg, "ELEMENTS")

    for mid, mdata in messages.items():
        for sig in mdata["signals"]:
            sname = sig.get("signal_name", "UnknownSig")
            isig  = _sub(sig_elements, "I-SIGNAL")
            _sub(isig, "SHORT-NAME", sname)
            _sub(isig, "LENGTH", str(sig.get("length", 8)))
            _sub(_sub(isig, "INIT-VALUE"), "NUMERICAL-VALUE", "0")
            unit = sig.get("unit", "")
            if unit:
                _sub(isig, "UNIT-REF", unit)

    # ── PDU / Frame package ───────────────────────────────────────────────────
    frame_pkg = _sub(pkgs, "AR-PACKAGE")
    _sub(frame_pkg, "SHORT-NAME", "Frames")
    frame_elements = _sub(frame_pkg, "ELEMENTS")

    for mid, mdata in messages.items():
        mname = mdata["name"]

        # I-SIGNAL-I-PDU
        pdu = _sub(frame_elements, "I-SIGNAL-I-PDU")
        _sub(pdu, "SHORT-NAME", f"{mname}_PDU")
        _sub(pdu, "LENGTH", str(mdata["length"] * 8))   # in bits
        mapping_set = _sub(pdu, "I-SIGNAL-TO-PDU-MAPPINGS")
        for sig in mdata["signals"]:
            sname    = sig.get("signal_name", "UnknownSig")
            mapping  = _sub(mapping_set, "I-SIGNAL-TO-I-PDU-MAPPING")
            _sub(mapping, "SHORT-NAME", f"{sname}_MAP")
            _sub(mapping, "I-SIGNAL-REF", f"/Signals/{sname}")
            _sub(mapping, "START-POSITION", str(sig.get("start_bit", 0)))
            bo = "LITTLE-ENDIAN" if sig.get("byte_order", "little") == "little" else "BIG-ENDIAN"
            _sub(mapping, "PACKING-BYTE-ORDER", bo)

        # CAN-FRAME
        frame = _sub(frame_elements, "CAN-FRAME")
        _sub(frame, "SHORT-NAME", mname)
        _sub(frame, "FRAME-LENGTH", str(mdata["length"]))
        _sub(frame, "CAN-ID", str(mid))
        _sub(_sub(frame, "PDU-TO-FRAME-MAPPINGS"), "PDU-REF", f"/Frames/{mname}_PDU")

    # ── CompuMethod package ───────────────────────────────────────────────────
    compu_pkg = _sub(pkgs, "AR-PACKAGE")
    _sub(compu_pkg, "SHORT-NAME", "CompuMethods")
    compu_elements = _sub(compu_pkg, "ELEMENTS")

    for mid, mdata in messages.items():
        for sig in mdata["signals"]:
            sname  = sig.get("signal_name", "UnknownSig")
            scale  = float(sig.get("scale", 1.0))
            offset = float(sig.get("offset", 0.0))
            cm     = _sub(compu_elements, "COMPU-METHOD")
            _sub(cm, "SHORT-NAME", f"{sname}_CM")
            cs     = _sub(_sub(cm, "COMPU-INTERNAL-TO-PHYS"), "COMPU-SCALES")
            cscale = _sub(cs, "COMPU-SCALE")
            _sub(cscale, "COMPU-RATIONAL-COEFFS")   # placeholder structure
            _sub(cscale, "NUMERATOR", f"{offset} {scale}")
            _sub(cscale, "DENOMINATOR", "1")
            unit = sig.get("unit", "")
            if unit:
                _sub(cm, "UNIT-REF", unit)

    # Pretty-print
    raw = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ")
    # Remove the extra XML declaration minidom prepends (we'll add our own)
    lines = pretty.splitlines()
    if lines and lines[0].startswith("<?xml"):
        lines = lines[1:]
    header = '<?xml version="1.0" encoding="UTF-8"?>\n'
    return header + "\n".join(lines)
