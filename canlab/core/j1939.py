"""
SAE J1939 PGN / SPN decoder.

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
from typing import Optional

# ── PGN → (name, SPNs) ────────────────────────────────────────────────────────
# SPN entry: (name, start_byte, length_bytes, scale, offset, unit)
# start_byte is 0-based within the 8-byte data field

_PGN_DB: dict[int, dict] = {
    # Electronic Engine Controller 1
    0xF004: {
        "name": "EEC1 — Electronic Engine Controller 1",
        "spns": {
            190: ("Engine Speed",            3, 2, 0.125, 0,   "rpm"),
            512: ("Driver's Demand Engine",  1, 1, 1.0,   -125, "%"),
            513: ("Actual Engine Torque",    2, 1, 1.0,   -125, "%"),
            899: ("Engine Torque Mode",      0, 1, 1.0,   0,   ""),
        },
    },
    # Vehicle Speed / Cruise Control
    0xFEF1: {
        "name": "CCVS — Cruise Control/Vehicle Speed",
        "spns": {
            84:  ("Wheel-Based Vehicle Speed", 1, 2, 1/256, 0, "km/h"),
            595: ("Cruise Control Active",     0, 1, 1.0,   0, ""),
            596: ("Cruise Control Enable",     0, 1, 1.0,   0, ""),
        },
    },
    # Fuel Economy (Liquid)
    0xFEF2: {
        "name": "LFE — Fuel Economy",
        "spns": {
            183: ("Fuel Rate",                0, 2, 0.05,  0, "L/h"),
            184: ("Instantaneous Fuel Econ.", 2, 2, 1/512, 0, "km/L"),
            185: ("Average Fuel Economy",     4, 2, 1/512, 0, "km/L"),
        },
    },
    # Engine Fluid Level / Pressure 1
    0xFEEF: {
        "name": "EFL/P1 — Engine Fluid Level/Pressure 1",
        "spns": {
            94:  ("Fuel Delivery Pressure",   0, 1, 4.0,  0,  "kPa"),
            22:  ("Engine Oil Level",         1, 1, 0.4,  0,  "%"),
            100: ("Engine Oil Pressure",      3, 1, 4.0,  0,  "kPa"),
            110: ("Engine Coolant Temp",      5, 1, 1.0,  -40,"°C"),
        },
    },
    # Ambient Conditions
    0xFEF5: {
        "name": "AMB — Ambient Conditions",
        "spns": {
            171: ("Ambient Air Temperature",  3, 2, 0.03125, -273, "°C"),
            108: ("Barometric Pressure",      1, 1, 0.5,     0,    "kPa"),
        },
    },
    # Vehicle Electrical Power
    0xFEF7: {
        "name": "VEP — Vehicle Electrical Power",
        "spns": {
            158: ("Key Switch Battery Voltage", 0, 2, 0.05,  0,  "V"),
            168: ("Battery Potential",          2, 2, 0.05,  0,  "V"),
        },
    },
    # Transmission
    0xF005: {
        "name": "ETC1 — Electronic Transmission Controller 1",
        "spns": {
            522: ("Transmission Selected Gear", 3, 1, 1.0, -125, ""),
            523: ("Transmission Actual Gear",   4, 1, 1.0, -125, ""),
            524: ("Transmission Current Range", 5, 2, 1.0,    0, ""),
        },
    },
    # Axle / Drive Wheel Speed
    0xFE68: {
        "name": "AWSS — Axle/Drive Wheel Speed",
        "spns": {
            904: ("Front Axle Speed",   0, 2, 1/256, 0, "km/h"),
            905: ("Relative Speed FA1", 2, 1, 1/16,  0, "km/h"),
        },
    },
    # DM1 — Active DTCs
    0xFECA: {
        "name": "DM1 — Active Diagnostic Trouble Codes",
        "spns": {},  # complex encoding handled separately
    },
    # Engine Hours / Revolutions
    0xFEE5: {
        "name": "HOURS — Engine Hours, Revolutions",
        "spns": {
            247: ("Total Engine Hours",    0, 4, 0.05,  0, "h"),
            249: ("Total Engine Revs",     4, 4, 1000.0,0, "rev"),
        },
    },
    # Retarder
    0xF006: {
        "name": "ERC1 — Electronic Retarder Controller 1",
        "spns": {
            520: ("Retarder Torque Mode",         0, 1, 1.0, 0, ""),
            521: ("Retarder Actual Retarding Pct",2, 1, 1.0, 0, "%"),
        },
    },
}

# Source Address → ECU type
_SA_NAMES: dict[int, str] = {
    0x00: "Engine #1",
    0x01: "Engine #2",
    0x03: "Transmission",
    0x10: "Exhaust/Emission Control",
    0x11: "Exhaust/Emission Ctrl #2",
    0x17: "Fuel System",
    0x21: "Brakes — System Controller",
    0x28: "Instrument Cluster #1",
    0x29: "Trip Recorder",
    0x2A: "Vehicle Management System",
    0x2C: "Cab Display #1",
    0x33: "Body Controller",
    0x3D: "Retarder — Exhaust Engine #1",
    0xF0: "Off-Board Diagnostic Tool",
    0xFF: "Global (broadcast)",
}


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
          "sa_name":  "Engine #1",
          "pgn_name": "CCVS — Cruise Control/Vehicle Speed",
        }
    """
    priority = (arb_id >> 26) & 0x07
    dp       = (arb_id >> 24) & 0x01
    pf       = (arb_id >> 16) & 0xFF
    ps       = (arb_id >>  8) & 0xFF
    sa       =  arb_id        & 0xFF

    if pf >= 0xF0:   # PDU2 — PS is group extension
        pgn = (dp << 16) | (pf << 8) | ps
        da  = 0xFF
    else:             # PDU1 — PS is destination address
        pgn = (dp << 16) | (pf << 8)
        da  = ps

    pgn_info = _PGN_DB.get(pgn, {})
    return {
        "priority": priority,
        "pgn":      pgn,
        "sa":       sa,
        "da":       da,
        "sa_name":  _SA_NAMES.get(sa, f"SA 0x{sa:02X}"),
        "pgn_name": pgn_info.get("name", f"PGN 0x{pgn:04X}"),
    }


def decode_pgn(pgn: int, data: bytes) -> dict:
    """
    Decode SPN values from `data` (8 bytes) for the given PGN.

    Returns {spn_name: (value, unit)} or {} if PGN unknown / data too short.
    """
    info = _PGN_DB.get(pgn)
    if not info or not info.get("spns"):
        return {}

    result = {}
    for spn_id, (name, start, length, scale, offset, unit) in info["spns"].items():
        end = start + length
        if end > len(data):
            continue
        raw_bytes = data[start:end]
        raw_int   = int.from_bytes(raw_bytes, "little")
        # 0xFF...F = not available indicator
        if raw_bytes == b"\xFF" * length:
            continue
        value = raw_int * scale + offset
        result[name] = (round(value, 4), unit)

    return result


def scan_for_j1939(df) -> list[dict]:
    """
    Scan a frames DataFrame for J1939 messages (extended 29-bit IDs inferred from
    ID values > 0x7FF when stored as hex strings).

    Returns list of:
        {"id_hex", "priority", "pgn", "pgn_name", "sa", "sa_name", "frame_count"}
    sorted by pgn.
    """
    import pandas as pd
    results = []
    seen    = set()
    for can_id in df["ID"].unique():
        try:
            arb_id = int(can_id, 16)
        except (ValueError, TypeError):
            continue
        if arb_id <= 0x7FF:
            continue   # standard 11-bit ID — not J1939
        parsed = parse_j1939_id(arb_id)
        key = parsed["pgn"]
        if key in seen:
            continue
        seen.add(key)
        count = int((df["ID"] == can_id).sum())
        results.append({
            "id_hex":      can_id,
            "priority":    parsed["priority"],
            "pgn":         parsed["pgn"],
            "pgn_name":    parsed["pgn_name"],
            "sa":          parsed["sa"],
            "sa_name":     parsed["sa_name"],
            "frame_count": count,
        })

    results.sort(key=lambda x: x["pgn"])
    return results
