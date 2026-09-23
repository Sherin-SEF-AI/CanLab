"""
SAE J1939 and NMEA 2000 PGN decoder.

Both protocols sit on the same 29-bit CAN frame and split the identifier the
same way, so one ID decoder serves both. They part company at the data page:
J1939 messages are almost all data page 0, while NMEA 2000 puts its own PGNs on
data page 1 in the 126 208 to 130 836 range. Reading a marine bus with the
J1939 tables produces a screen full of plausible-looking source addresses and
no PGN names at all, which is worse than saying nothing, so the protocol is
worked out from the identifier and the right table is used.

J1939 uses 29-bit extended CAN IDs:
  bits 28-26 : Priority (3 bits)
  bit  25    : Reserved
  bit  24    : Data Page
  bits 23-16 : PGN high byte (PDU Format)
  bits 15-8  : PGN low byte / Destination Address (if PDU1: PF < 240)
  bits  7-0  : Source Address

For PDU2 (PF >= 240): PGN = (DP<<16) | (PF<<8) | PS
For PDU1 (PF <  240): PGN = (DP<<16) | (PF<<8) [destination = PS byte]
"""

# ── the J1939 tables ─────────────────────────────────────────────────────────
# Layouts, names and source addresses live in core/j1939_db.py, where each one
# can be read against the specification and each is pinned by a test.

from canlab.core.j1939_db import (  # noqa: E402
    ERROR, NOT_AVAILABLE, PGNS, SOURCE_ADDRESSES, decode_spn, proprietary_name,
)

#: Kept under its old name: other modules and the MCP tools look PGNs up here.
_PGN_DB = PGNS
_SA_NAMES = SOURCE_ADDRESSES


# ── Public API ────────────────────────────────────────────────────────────────

def is_j1939(arb_id: int, is_extended: bool = True) -> bool:
    """Return True if this looks like a J1939 29-bit ID."""
    return is_extended and arb_id > 0x7FF


def parse_j1939_id(arb_id: int) -> dict:
    """
    Decompose a 29-bit J1939 arbitration ID.

    Returns:
        {
          "priority": 6,
          "pgn":      0xFEF1,
          "sa":       0x00,
          "da":       0xFF,         # 0xFF = broadcast
          "sa_name":  "Engine #1 (0x00)",
          "pgn_name": "CCVS - Cruise Control/Vehicle Speed",
        }
    """
    priority = (arb_id >> 26) & 0x07
    dp       = (arb_id >> 24) & 0x03
    pf       = (arb_id >> 16) & 0xFF
    ps       = (arb_id >>  8) & 0xFF
    sa       =  arb_id        & 0xFF

    if pf >= 0xF0:   # PDU2: PS is group extension
        pgn = (dp << 16) | (pf << 8) | ps
        da  = 0xFF
    else:             # PDU1: PS is destination address
        pgn = (dp << 16) | (pf << 8)
        da  = ps

    if is_nmea2000(pgn):
        single = n2k_single_frame(pgn)
        return {
            "priority": priority,
            "pgn":      pgn,
            "sa":       sa,
            "da":       da,
            "sa_name":  f"Device 0x{sa:02X}",
            "pgn_name": pgn_name(pgn),
            "protocol": "NMEA 2000",
            "single_frame": single,
            "transport": False,
        }

    return {
        "priority": priority,
        "pgn":      pgn,
        "sa":       sa,
        "da":       da,
        "sa_name":  sa_name(sa),
        "pgn_name": pgn_name(pgn),
        "protocol": "J1939",
        "single_frame": True,
        # The two transport-protocol envelopes: their payload is a message
        # for another PGN, reassembled by core/multiframe.py.
        "transport": pgn in (0xEC00, 0xEB00),
    }


def pgn_name(pgn: int) -> str:
    """The name of a PGN in whichever table owns it, or a placeholder."""
    if is_nmea2000(pgn):
        if pgn in _N2K_NAMES:
            return _N2K_NAMES[pgn][0]
        from canlab.core import n2k_db
        found = n2k_db.name(pgn)
        if found:
            return found
        if 130816 <= pgn <= 131071 or pgn == 126720:
            return "Proprietary fast packet (manufacturer defined)"
        return f"PGN {pgn}"
    entry = PGNS.get(pgn)
    if entry is not None:
        return entry.name
    return proprietary_name(pgn) or f"PGN 0x{pgn:04X}"


def sa_name(sa: int) -> str:
    """The J1939 preferred name for a source address, or its number."""
    name = SOURCE_ADDRESSES.get(sa)
    return f"{name} (0x{sa:02X})" if name else f"SA 0x{sa:02X}"


# ── NMEA 2000 ────────────────────────────────────────────────────────────────
# Marine electronics. Same frame format, different PGN space, different units:
# angles are radians, speeds metres per second, temperatures kelvin.
#
# Only single-frame PGNs carry field definitions here. NMEA 2000 also has a
# "fast packet" transport that spreads one message over several frames with a
# sequence byte, and decoding a fast-packet PGN from a single frame produces
# confident nonsense: PGN 129029 read that way dates a 2021 recording to 2002.
# Those PGNs are named and left undecoded until reassembly exists.

#: PGN → (name, is_single_frame)
_N2K_NAMES: dict[int, tuple[str, bool]] = {
    59392: ("ISO Acknowledgement", True),
    59904: ("ISO Request", True),
    60928: ("ISO Address Claim", True),
    126208: ("NMEA Group Function", False),
    126992: ("System Time", True),
    126993: ("Heartbeat", True),
    126996: ("Product Information", False),
    126998: ("Configuration Information", False),
    127245: ("Rudder", True),
    127250: ("Vessel Heading", True),
    127251: ("Rate of Turn", True),
    127257: ("Attitude", True),
    127258: ("Magnetic Variation", True),
    127488: ("Engine Parameters, Rapid Update", True),
    127489: ("Engine Parameters, Dynamic", False),
    127493: ("Transmission Parameters, Dynamic", True),
    127497: ("Trip Parameters, Engine", False),
    127505: ("Fluid Level", True),
    127508: ("Battery Status", True),
    128259: ("Speed", True),
    128267: ("Water Depth", True),
    128275: ("Distance Log", False),
    129025: ("Position, Rapid Update", True),
    129026: ("COG & SOG, Rapid Update", True),
    129029: ("GNSS Position Data", False),
    129033: ("Local Time Offset", True),
    129038: ("AIS Class A Position Report", False),
    129039: ("AIS Class B Position Report", False),
    129283: ("Cross Track Error", True),
    129284: ("Navigation Data", False),
    129285: ("Route/WP Information", False),
    129539: ("GNSS DOPs", True),
    129540: ("GNSS Satellites in View", False),
    130306: ("Wind Data", True),
    130310: ("Environmental Parameters", True),
    130311: ("Environmental Parameters", True),
    130312: ("Temperature", True),
    130313: ("Humidity", True),
    130314: ("Actual Pressure", True),
    130316: ("Temperature, Extended Range", True),
    130323: ("Meteorological Station Data", False),
}

#: PGN → {field name: (start_byte, length_bytes, scale, offset, unit, signed)}
#: Every layout below was checked against a real recording; see the tests.
_N2K_FIELDS: dict[int, dict] = {
    127250: {
        "Heading":   (1, 2, 1e-4, 0.0, "rad", False),
        "Deviation": (3, 2, 1e-4, 0.0, "rad", True),
        "Variation": (5, 2, 1e-4, 0.0, "rad", True),
    },
    127251: {"Rate of Turn": (1, 4, 3.125e-8, 0.0, "rad/s", True)},
    127257: {
        "Yaw":   (1, 2, 1e-4, 0.0, "rad", True),
        "Pitch": (3, 2, 1e-4, 0.0, "rad", True),
        "Roll":  (5, 2, 1e-4, 0.0, "rad", True),
    },
    128259: {
        "Speed Through Water": (1, 2, 0.01, 0.0, "m/s", False),
        "Speed Over Ground":   (3, 2, 0.01, 0.0, "m/s", False),
    },
    128267: {
        "Water Depth": (1, 4, 0.01, 0.0, "m", False),
        "Offset":      (5, 2, 0.001, 0.0, "m", True),
    },
    129025: {
        "Latitude":  (0, 4, 1e-7, 0.0, "deg", True),
        "Longitude": (4, 4, 1e-7, 0.0, "deg", True),
    },
    129026: {
        "Course Over Ground": (2, 2, 1e-4, 0.0, "rad", False),
        "Speed Over Ground":  (4, 2, 0.01, 0.0, "m/s", False),
    },
    130306: {
        "Wind Speed": (1, 2, 0.01, 0.0, "m/s", False),
        "Wind Angle": (3, 2, 1e-4, 0.0, "rad", False),
    },
    130310: {"Water Temperature": (1, 2, 0.01, -273.15, "\u00b0C", False)},
    130311: {"Temperature": (2, 2, 0.01, -273.15, "\u00b0C", False)},
    130312: {"Temperature": (3, 2, 0.01, -273.15, "\u00b0C", False)},
    130314: {"Pressure": (3, 4, 0.1, 0.0, "Pa", False)},
    130316: {"Temperature": (3, 3, 0.001, -273.15, "\u00b0C", False)},
}

#: Data page 1 identifiers in this range belong to NMEA 2000, not J1939.
N2K_PGN_RANGE = (126208, 130836)


def is_nmea2000(pgn: int) -> bool:
    """True if this PGN is in the NMEA 2000 range rather than J1939's."""
    return N2K_PGN_RANGE[0] <= pgn <= N2K_PGN_RANGE[1]


def _n2k_value(data: bytes, start: int, length: int, signed: bool):
    """One little-endian field, or None if the sender marked it unavailable."""
    if start + length > len(data):
        return None
    raw_bytes = data[start:start + length]
    if raw_bytes == b"\xFF" * length:
        return None                      # NMEA 2000's "data not available"
    raw = int.from_bytes(raw_bytes, "little")
    if signed:
        # A signed field says "not available" with its largest positive code
        # (0x7FFF and so on), the way an unsigned one uses all ones. Without
        # this a satellite's missing range residual read as 21,474 metres.
        if raw == (1 << (length * 8 - 1)) - 1:
            return None
        if raw >= 1 << (length * 8 - 1):
            raw -= 1 << (length * 8)
    return raw


def _decode_gnss_position(data: bytes) -> dict:
    """PGN 129029, 43 bytes once reassembled from seven fast-packet frames.

    Checked against a Lake Erie recording: the latitude and longitude agree
    with the single-frame rapid-position message on the same bus, and the
    altitude is the lake's published surface elevation.
    """
    import datetime as _dt
    out: dict = {}
    days = _n2k_value(data, 1, 2, False)
    if days is not None:
        out["Date"] = ((_dt.date(1970, 1, 1) + _dt.timedelta(days=days)).isoformat(), "")
    secs = _n2k_value(data, 3, 4, False)
    if secs is not None:
        out["Time"] = (round(secs * 1e-4, 4), "s UTC")
    for name, start, unit in (("Latitude", 7, "deg"), ("Longitude", 15, "deg")):
        raw = _n2k_value(data, start, 8, True)
        if raw is not None:
            out[name] = (round(raw * 1e-16, 7), unit)
    alt = _n2k_value(data, 23, 8, True)
    if alt is not None:
        out["Altitude"] = (round(alt * 1e-6, 3), "m")
    if len(data) > 31 and data[31] != 0xFF:
        out["GNSS Type"] = (data[31] & 0x0F, "")
        out["Method"] = ((data[31] >> 4) & 0x0F, "")
    if len(data) > 32 and data[32] != 0xFF:
        out["Integrity"] = (data[32] & 0x03, "")
    svs = _n2k_value(data, 33, 1, False)
    if svs is not None:
        out["Satellites Used"] = (svs, "")
    for name, start in (("HDOP", 34), ("PDOP", 36)):
        raw = _n2k_value(data, start, 2, True)
        if raw is not None:
            out[name] = (round(raw * 0.01, 2), "")
    sep = _n2k_value(data, 38, 4, True)
    if sep is not None:
        out["Geoidal Separation"] = (round(sep * 0.01, 2), "m")
    refs = _n2k_value(data, 42, 1, False)
    if refs is not None:
        out["Reference Stations"] = (refs, "")
    return out


def _decode_satellites_in_view(data: bytes) -> dict:
    """PGN 129540: a header and then twelve bytes per satellite."""
    out: dict = {}
    if len(data) > 1 and data[1] != 0xFF:
        out["Mode"] = (data[1] & 0x03, "")
    count = _n2k_value(data, 2, 1, False)
    if count is None:
        return out
    out["Satellites in View"] = (count, "")
    sats = []
    for i in range(count):
        base = 3 + 12 * i
        if base + 12 > len(data):
            break
        prn = _n2k_value(data, base, 1, False)
        elev = _n2k_value(data, base + 1, 2, True)
        azim = _n2k_value(data, base + 3, 2, False)
        snr = _n2k_value(data, base + 5, 2, True)
        resid = _n2k_value(data, base + 7, 4, True)
        status = data[base + 11] & 0x0F if data[base + 11] != 0xFF else None
        sats.append({
            "prn": prn,
            "elevation_rad": None if elev is None else round(elev * 1e-4, 4),
            "azimuth_rad": None if azim is None else round(azim * 1e-4, 4),
            "snr_db": None if snr is None else round(snr * 0.01, 2),
            "range_residual_m": None if resid is None else round(resid * 1e-5, 5),
            "status": status,
        })
    out["satellites"] = sats
    return out


#: Multi-frame NMEA 2000 PGNs with a decoder, used only on a reassembled
#: buffer. One 8-byte frame of these still decodes to nothing: read alone it
#: gives a confident wrong answer.
_N2K_DECODERS = {
    129029: _decode_gnss_position,
    129540: _decode_satellites_in_view,
}


def n2k_single_frame(pgn: int) -> bool:
    """Whether an NMEA 2000 PGN fits one frame; unknown ones are assumed to."""
    if pgn in _N2K_NAMES:
        return _N2K_NAMES[pgn][1]
    from canlab.core import n2k_db
    fast = n2k_db.is_fast_packet(pgn)
    return True if fast is None else not fast


def decode_n2k(pgn: int, data: bytes, reassembled: bool | None = None) -> dict:
    """Decode an NMEA 2000 PGN into {field: (value, unit)}.

    The hand-written decoders, checked against frames from a real marine
    recording, come first. Every other standard PGN is decoded from the table
    distilled from canboat (core/n2k_db.py); on that same recording the two
    agree on every value they share.

    A fast-packet PGN is decoded only from a reassembled message: its single
    frames carry a sequence byte and a length byte, so reading one alone
    shifts every field. ``reassembled`` says which one ``data`` is; left as
    None, a buffer longer than a frame is taken to be reassembled.
    """
    data = bytes(data)
    if reassembled is None:
        reassembled = len(data) > 8
    decoder = _N2K_DECODERS.get(pgn)
    if decoder is not None:
        return decoder(data) if reassembled else {}
    fields = _N2K_FIELDS.get(pgn)
    if fields:
        out = {}
        for name, (start, length, scale, offset, unit, signed) in fields.items():
            raw = _n2k_value(data, start, length, signed)
            if raw is None:
                continue
            # Rounded to the field's own resolution. A fixed six places turned
            # a rate of turn of -0.0001047 rad/s into -0.000105 and cut a
            # 1e-7 degree position to ten centimetres.
            from canlab.core.n2k_db import _places
            out[name] = (round(raw * scale + offset, _places(scale)), unit)
        return out
    if not n2k_single_frame(pgn) and not reassembled:
        return {}
    from canlab.core import n2k_db
    return n2k_db.decode(pgn, data)


# FMI (Failure Mode Identifier) short names — SAE J1939-73 Appendix A.
_FMI_NAMES = {
    0: "Above normal (most severe)", 1: "Below normal (most severe)",
    2: "Erratic/intermittent", 3: "Voltage above normal", 4: "Voltage below normal",
    5: "Current below normal/open", 6: "Current above normal/grounded",
    7: "Mechanical system not responding", 8: "Abnormal frequency/pulse width",
    9: "Abnormal update rate", 10: "Abnormal rate of change",
    11: "Root cause not known", 12: "Bad intelligent device", 13: "Out of calibration",
    14: "Special instructions", 15: "Above normal (least severe)",
    16: "Above normal (moderate)", 17: "Below normal (least severe)",
    18: "Below normal (moderate)", 19: "Received network data in error",
    31: "Condition exists",
}


def decode_dm1(data: bytes) -> dict:
    """Decode a DM1 (active DTCs, PGN 0xFECA) frame.

    Returns {"lamps": {...}, "dtcs": [{"spn","fmi","fmi_name","cm","oc"}]}.
    Each DTC is 4 bytes after the 2-byte lamp header: SPN (19 bits), FMI (5),
    CM (1), OC (7) per SAE J1939-73.
    """
    if len(data) < 2:
        return {"lamps": {}, "dtcs": []}
    lamp = data[0]
    lamps = {
        "malfunction": (lamp >> 6) & 0x03,   # MIL
        "red_stop":    (lamp >> 4) & 0x03,
        "amber_warn":  (lamp >> 2) & 0x03,
        "protect":     lamp & 0x03,
    }
    dtcs = []
    body = data[2:]
    for i in range(0, len(body) - 3, 4):
        b2, b3, b4, b5 = body[i], body[i + 1], body[i + 2], body[i + 3]
        if (b2, b3, b4, b5) in ((0, 0, 0, 0), (0xFF, 0xFF, 0xFF, 0xFF)):
            continue   # no/!available DTC slot
        spn = b2 | (b3 << 8) | ((b4 & 0xE0) << 11)   # 19-bit SPN
        fmi = b4 & 0x1F
        cm  = (b5 >> 7) & 0x01
        oc  = b5 & 0x7F
        dtcs.append({
            "spn": spn, "fmi": fmi, "fmi_name": _FMI_NAMES.get(fmi, f"FMI {fmi}"),
            "cm": cm, "oc": oc,
        })
    return {"lamps": lamps, "dtcs": dtcs}


def decode_pgn(pgn: int, data: bytes, reassembled: bool | None = None) -> dict:
    """
    Decode the parameters of one message. Returns {name: (value, unit)}.

    A measured value is a number. A discrete field (a switch, a mode) is its
    label. A parameter the sender reports as an error is the string "error",
    so a failed sensor is visible rather than read as a reading. A parameter
    the sender marks "not available", or that falls in a reserved range, is
    left out, because none of those is a value.

    DM1 (0xFECA) is decoded into structured DTCs via decode_dm1, and NMEA 2000
    PGNs by decode_n2k.
    """
    if is_nmea2000(pgn):
        return decode_n2k(pgn, data, reassembled)
    if pgn == 0xFECA:
        return decode_dm1(data)
    entry = PGNS.get(pgn)
    if entry is None or not entry.spns:
        return {}
    result = {}
    for spec in entry.spns:
        value = decode_spn(spec, data)
        if value is NOT_AVAILABLE:
            continue
        if isinstance(value, (int, float)):
            value = round(float(value), 4)
        result[spec.name] = (value, spec.unit if value != ERROR else "")
    return result


def decode_spns(pgn: int, data: bytes) -> list[dict]:
    """The same decode with the SPN numbers and the raw status kept, for
    tools that need to cite the parameter rather than show it."""
    entry = PGNS.get(pgn)
    if entry is None or is_nmea2000(pgn):
        return []
    out = []
    for spec in entry.spns:
        value = decode_spn(spec, data)
        status = ("not available" if value is NOT_AVAILABLE
                  else "error" if value == ERROR else "ok")
        out.append({"spn": spec.spn, "name": spec.name, "unit": spec.unit,
                    "value": None if status != "ok" else
                    (round(float(value), 4) if isinstance(value, (int, float)) else value),
                    "status": status})
    return out


def scan_for_j1939(df) -> list[dict]:
    """
    One row per parameter group and sender in a frames DataFrame.

    A PGN sent by several ECUs (CCVS from the engine and the body controller,
    DM1 from nine modules on the truck log) is a row per sender, each with its
    own frame count. Keying by PGN alone kept the first sender and dropped the
    rest, so a scan understated both who was talking and how much.

    Returns dicts with "id_hex", "priority", "pgn", "pgn_name", "sa",
    "sa_name", "protocol", "single_frame", "frame_count".
    """
    if df is None or df.empty or "ID" not in df.columns:
        return []
    counts = df["ID"].value_counts()
    results = []
    for can_id, count in counts.items():
        try:
            arb_id = int(can_id, 16)
        except (ValueError, TypeError):
            continue
        if arb_id <= 0x7FF:
            continue   # standard 11-bit ID, not J1939
        parsed = parse_j1939_id(arb_id)
        results.append({
            "id_hex":      can_id,
            "priority":    parsed["priority"],
            "pgn":         parsed["pgn"],
            "pgn_name":    parsed["pgn_name"],
            "sa":          parsed["sa"],
            "sa_name":     parsed["sa_name"],
            "protocol":    parsed["protocol"],
            "single_frame": parsed["single_frame"],
            "frame_count": int(count),
        })

    # Named messages first, then by how much of the bus they are. Sorting by
    # PGN put the four unnamed low-numbered messages at the top of a marine
    # capture, so the visible rows all read "no idea" while the twenty named
    # ones sat below the fold.
    results.sort(key=lambda x: (x["pgn_name"].startswith("PGN "),
                                -x["frame_count"], x["pgn"], x["sa"]))
    return results
