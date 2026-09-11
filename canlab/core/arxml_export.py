"""Export signal definitions as an AUTOSAR 4.3 System Template (ARXML).

The previous exporter emitted a private dialect — a ``CAN-ID`` element inside
``CAN-FRAME``, and linear coefficients as a bare ``NUMERATOR`` text node — that
only its own importer understood. Loading it anywhere else, cantools included,
yielded zero messages.

This writes the structure an AUTOSAR system loader actually looks for:

    CAN-CLUSTER
      └ CAN-PHYSICAL-CHANNEL
          └ CAN-FRAME-TRIGGERING (IDENTIFIER, addressing mode, FRAME-REF)
    CAN-FRAME (FRAME-LENGTH, PDU-TO-FRAME-MAPPING → PDU-REF)
    I-SIGNAL-I-PDU (I-SIGNAL-TO-I-PDU-MAPPING: START-POSITION, byte order)
    I-SIGNAL (LENGTH, SYSTEM-SIGNAL-REF)
    SYSTEM-SIGNAL (→ COMPU-METHOD, → UNIT)
    COMPU-METHOD (COMPU-RATIONAL-COEFFS: numerator [offset, scale], denom [1])

The round trip is verified against cantools in the tests, which is the only
way to know this stays loadable.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.dom import minidom

from canlab.core.dbc_manager import (dbc_identifier, frame_id_int, norm_signal)

NS = "http://autosar.org/schema/r4.0"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
PKG = "/CanLab"


def _e(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = str(text)
    return el


def _ref(parent, tag, dest, path):
    return _e(parent, tag, path, DEST=dest)


def _package(parent, name):
    """A sub-package; returns its ELEMENTS container.

    References are absolute paths, so the nesting here has to match them:
    sub-packages live under the root package that PKG names.
    """
    pkg = _e(parent, "AR-PACKAGE")
    _e(pkg, "SHORT-NAME", name)
    return _e(pkg, "ELEMENTS")


def to_arxml_string(signal_defs: list[dict], msg_meta: dict | None = None) -> str:
    """Render these signals as an AUTOSAR 4.3 ARXML document."""
    messages: dict[int, dict] = {}
    for raw in signal_defs:
        sig = norm_signal(raw)
        fid = frame_id_int(sig)
        msg = messages.setdefault(fid, {
            "name": dbc_identifier(sig.get("message_name"), f"MSG_{sig['message_id']}"),
            "extended": bool(sig.get("extended")),
            "length": 8,
            "signals": [],
            "used": set(),
        })
        name = dbc_identifier(sig.get("signal_name"), "SIG")
        base, n = name, 2
        while name in msg["used"]:
            name = f"{base}_{n}"
            n += 1
        msg["used"].add(name)
        sig["_name"] = name
        msg["signals"].append(sig)
        msg["length"] = max(msg["length"], int(sig.get("msg_length", 8) or 8),
                            -(-(sig["start_bit"] + sig["length"]) // 8))

    root = ET.Element("AUTOSAR", {
        "xmlns": NS,
        "xmlns:xsi": XSI,
        "xsi:schemaLocation": f"{NS} AUTOSAR_4-3-0.xsd",
    })
    root_packages = _e(root, "AR-PACKAGES")
    root_pkg = _e(root_packages, "AR-PACKAGE")
    _e(root_pkg, "SHORT-NAME", PKG.lstrip("/"))
    packages = _e(root_pkg, "AR-PACKAGES")

    cluster_elems = _package(packages, "Cluster")
    frame_elems = _package(packages, "Frames")
    pdu_elems = _package(packages, "Pdus")
    isignal_elems = _package(packages, "ISignals")
    syssignal_elems = _package(packages, "SystemSignals")
    compu_elems = _package(packages, "CompuMethods")
    unit_elems = _package(packages, "Units")

    # ── cluster / physical channel / frame triggerings ───────────────────
    cluster = _e(cluster_elems, "CAN-CLUSTER")
    _e(cluster, "SHORT-NAME", "CanLabCluster")
    variants = _e(cluster, "CAN-CLUSTER-VARIANTS")
    conditional = _e(variants, "CAN-CLUSTER-CONDITIONAL")
    _e(conditional, "BAUDRATE", "500000")
    channels = _e(conditional, "PHYSICAL-CHANNELS")
    channel = _e(channels, "CAN-PHYSICAL-CHANNEL")
    _e(channel, "SHORT-NAME", "Channel1")
    triggerings = _e(channel, "FRAME-TRIGGERINGS")

    units_written: set[str] = set()

    for fid, msg in sorted(messages.items()):
        mname = msg["name"]

        trig = _e(triggerings, "CAN-FRAME-TRIGGERING")
        _e(trig, "SHORT-NAME", f"FT_{mname}")
        _ref(trig, "FRAME-REF", "CAN-FRAME", f"{PKG}/Frames/{mname}")
        _e(trig, "CAN-ADDRESSING-MODE",
           "EXTENDED" if msg["extended"] else "STANDARD")
        _e(trig, "IDENTIFIER", fid)

        # ── frame -> pdu ─────────────────────────────────────────────────
        frame = _e(frame_elems, "CAN-FRAME")
        _e(frame, "SHORT-NAME", mname)
        _e(frame, "FRAME-LENGTH", msg["length"])
        mappings = _e(frame, "PDU-TO-FRAME-MAPPINGS")
        mapping = _e(mappings, "PDU-TO-FRAME-MAPPING")
        _e(mapping, "SHORT-NAME", f"PM_{mname}")
        _ref(mapping, "PDU-REF", "I-SIGNAL-I-PDU", f"{PKG}/Pdus/PDU_{mname}")

        # ── pdu -> signals ───────────────────────────────────────────────
        pdu = _e(pdu_elems, "I-SIGNAL-I-PDU")
        _e(pdu, "SHORT-NAME", f"PDU_{mname}")
        _e(pdu, "LENGTH", msg["length"])
        sig_mappings = _e(pdu, "I-SIGNAL-TO-PDU-MAPPINGS")

        for sig in msg["signals"]:
            sname = sig["_name"]
            unit_name = dbc_identifier(sig.get("unit") or "NoUnit", "NoUnit")

            sm = _e(sig_mappings, "I-SIGNAL-TO-I-PDU-MAPPING")
            _e(sm, "SHORT-NAME", f"SM_{mname}_{sname}")
            _ref(sm, "I-SIGNAL-REF", "I-SIGNAL", f"{PKG}/ISignals/{sname}")
            _e(sm, "PACKING-BYTE-ORDER",
               "MOST-SIGNIFICANT-BYTE-LAST" if sig["byte_order"] == "little"
               else "MOST-SIGNIFICANT-BYTE-FIRST")
            _e(sm, "START-POSITION", sig["start_bit"])

            isig = _e(isignal_elems, "I-SIGNAL")
            _e(isig, "SHORT-NAME", sname)
            _e(isig, "LENGTH", sig["length"])
            _ref(isig, "SYSTEM-SIGNAL-REF", "SYSTEM-SIGNAL",
                 f"{PKG}/SystemSignals/{sname}")

            sysig = _e(syssignal_elems, "SYSTEM-SIGNAL")
            _e(sysig, "SHORT-NAME", sname)
            if sig.get("description"):
                _e(sysig, "DESC", sig["description"])
            props = _e(sysig, "PHYSICAL-PROPS")
            variants_el = _e(props, "SW-DATA-DEF-PROPS-VARIANTS")
            cond = _e(variants_el, "SW-DATA-DEF-PROPS-CONDITIONAL")
            _ref(cond, "COMPU-METHOD-REF", "COMPU-METHOD",
                 f"{PKG}/CompuMethods/CM_{sname}")
            _ref(cond, "UNIT-REF", "UNIT", f"{PKG}/Units/{unit_name}")

            # ── linear conversion ────────────────────────────────────────
            cm = _e(compu_elems, "COMPU-METHOD")
            _e(cm, "SHORT-NAME", f"CM_{sname}")
            _e(cm, "CATEGORY", "LINEAR")
            _ref(cm, "UNIT-REF", "UNIT", f"{PKG}/Units/{unit_name}")
            internal = _e(cm, "COMPU-INTERNAL-TO-PHYS")
            scales = _e(internal, "COMPU-SCALES")
            scale_el = _e(scales, "COMPU-SCALE")
            _e(scale_el, "LOWER-LIMIT", _limit(sig.get("min_val"), 0))
            _e(scale_el, "UPPER-LIMIT",
               _limit(sig.get("max_val"), (1 << sig["length"]) - 1))
            coeffs = _e(scale_el, "COMPU-RATIONAL-COEFFS")
            numerator = _e(coeffs, "COMPU-NUMERATOR")
            _e(numerator, "V", _num(sig["offset"]))
            _e(numerator, "V", _num(sig["scale"]))
            denominator = _e(coeffs, "COMPU-DENOMINATOR")
            _e(denominator, "V", "1")

            if unit_name not in units_written:
                unit = _e(unit_elems, "UNIT")
                _e(unit, "SHORT-NAME", unit_name)
                _e(unit, "DISPLAY-NAME", sig.get("unit") or "")
                units_written.add(unit_name)

    raw = ET.tostring(root, encoding="utf-8")
    return minidom.parseString(raw).toprettyxml(indent="  ")


def _num(value) -> str:
    """Render a coefficient without a trailing .0 for whole numbers."""
    value = float(value)
    return str(int(value)) if value.is_integer() else repr(value)


def _limit(value, fallback) -> str:
    return _num(fallback if value is None or value == "" else value)
