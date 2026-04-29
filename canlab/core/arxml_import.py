"""AUTOSAR ARXML importer — returns the same list[dict] schema as load_dbc()."""
import xml.etree.ElementTree as ET
from pathlib import Path


def _strip_ns(tag: str) -> str:
    """Remove namespace prefix {...} from an element tag."""
    return tag.split("}")[-1] if "}" in tag else tag


def _find(el: ET.Element, tag: str):
    """Case-insensitive namespace-stripped child search."""
    for child in el:
        if _strip_ns(child.tag).upper() == tag.upper():
            return child
    return None


def _findall(el: ET.Element, tag: str):
    return [c for c in el if _strip_ns(c.tag).upper() == tag.upper()]


def _text(el: ET.Element | None, default: str = "") -> str:
    return el.text.strip() if el is not None and el.text else default


def parse_arxml(filepath: str) -> list[dict]:
    """Parse an AUTOSAR 4.x ARXML file and return signal dicts."""
    tree = ET.parse(filepath)
    root = tree.getroot()

    # Collect I-SIGNAL info keyed by short name
    isignals: dict[str, dict] = {}
    for isig in root.iter():
        if _strip_ns(isig.tag) == "I-SIGNAL":
            sname = _text(_find(isig, "SHORT-NAME"))
            if sname:
                isignals[sname] = {
                    "length":    int(_text(_find(isig, "LENGTH"), "8")),
                    "unit_ref":  _text(_find(isig, "UNIT-REF")),
                }

    # Collect COMPU-METHOD scale/offset keyed by "{sig_name}_CM"
    compu: dict[str, dict] = {}
    for cm in root.iter():
        if _strip_ns(cm.tag) == "COMPU-METHOD":
            sname = _text(_find(cm, "SHORT-NAME"))
            if sname.endswith("_CM"):
                base = sname[:-3]
                # Try to extract numerator coefficients
                num_el = None
                for el in cm.iter():
                    if _strip_ns(el.tag) == "NUMERATOR":
                        num_el = el
                        break
                parts = _text(num_el).split() if num_el is not None else []
                try:
                    offset = float(parts[0]) if len(parts) > 0 else 0.0
                    scale  = float(parts[1]) if len(parts) > 1 else 1.0
                except ValueError:
                    scale, offset = 1.0, 0.0
                compu[base] = {"scale": scale, "offset": offset}

    # Collect frames: CAN-FRAME and I-SIGNAL-I-PDU with mappings
    frame_map: dict[str, dict] = {}   # msg_name -> {can_id, length, signals: list}
    pdu_map:   dict[str, list] = {}   # pdu_name -> [{sname, start_bit, byte_order}]

    for pdu in root.iter():
        if _strip_ns(pdu.tag) != "I-SIGNAL-I-PDU":
            continue
        pdu_name = _text(_find(pdu, "SHORT-NAME"))
        sigs = []
        # Iterate within this PDU element only (not the whole document)
        for mapping in pdu.iter():
            if _strip_ns(mapping.tag) != "I-SIGNAL-TO-I-PDU-MAPPING":
                continue
            sig_ref   = _text(_find(mapping, "I-SIGNAL-REF")).rsplit("/", 1)[-1]
            start_bit = int(_text(_find(mapping, "START-POSITION"), "0"))
            bo_raw    = _text(_find(mapping, "PACKING-BYTE-ORDER"), "LITTLE-ENDIAN")
            bo        = "little" if "LITTLE" in bo_raw.upper() else "big"
            sigs.append({"signal_name": sig_ref, "start_bit": start_bit, "byte_order": bo})
        pdu_map[pdu_name] = sigs

    for frame in root.iter():
        if _strip_ns(frame.tag) != "CAN-FRAME":
            continue
        fname  = _text(_find(frame, "SHORT-NAME"))
        can_id = _text(_find(frame, "CAN-ID"), "0")
        flen   = int(_text(_find(frame, "FRAME-LENGTH"), "8"))
        # PDU ref
        pdu_ref = ""
        for el in frame.iter():
            if _strip_ns(el.tag) == "PDU-REF":
                pdu_ref = el.text.rsplit("/", 1)[-1] if el.text else ""
                break
        frame_map[fname] = {
            "can_id":  can_id,
            "length":  flen,
            "pdu_ref": pdu_ref,
        }

    # Assemble signal dicts
    results: list[dict] = []
    for msg_name, fdata in frame_map.items():
        can_id_str = fdata["can_id"]
        try:
            mid_int = int(can_id_str)
            mid_hex = f"{mid_int:03X}"
        except ValueError:
            mid_hex = can_id_str

        pdu_sigs = pdu_map.get(fdata["pdu_ref"], [])
        for ps in pdu_sigs:
            sname = ps["signal_name"]
            isig  = isignals.get(sname, {})
            cm    = compu.get(sname, {})
            results.append({
                "message_id":   mid_hex,
                "message_name": msg_name,
                "signal_name":  sname,
                "start_bit":    ps["start_bit"],
                "length":       isig.get("length", 8),
                "byte_order":   ps["byte_order"],
                "value_type":   "unsigned",
                "scale":        cm.get("scale", 1.0),
                "offset":       cm.get("offset", 0.0),
                "min_val":      None,
                "max_val":      None,
                "unit":         isig.get("unit_ref", ""),
                "description":  "",
                "msg_length":   fdata["length"],
            })

    return results
