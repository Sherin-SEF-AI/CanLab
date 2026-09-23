"""J1939 parameter layouts, pinned against frames from a real truck.

Every frame below is copied verbatim from the 145,534-frame J1939 log in the
acceptance corpus (a CANedge recording from CSS Electronics). The expected
values are what SAE J1939-71 says those bytes mean, and they agree with each
other the way a running truck's readings must: two ECUs report the same road
speed, the absolute inlet pressure is the boost pressure plus the barometer.

The table these replace put coolant temperature in the wrong message, filed
ETC1, the wheel speeds and ERC1 under other PGNs, read the battery voltages
from the current bytes, and decoded every switch as a whole byte. Nothing
tested a layout, which is how all of that survived.
"""
import pandas as pd
import pytest

from canlab.core.j1939 import (
    decode_pgn, decode_spns, parse_j1939_id, pgn_name, sa_name, scan_for_j1939,
)
from canlab.core.j1939_db import PGNS, classify


def frame(hexstr: str) -> bytes:
    return bytes.fromhex(hexstr)


# ── verbatim frames from the truck ───────────────────────────────────────────

def test_engine_temperatures_come_from_et1():
    out = decode_pgn(0xFEEE, frame("86 4C 20 2F FF FF 49 FF"))
    assert out["Engine Coolant Temperature"] == (94.0, "°C")
    assert out["Engine Fuel Temperature 1"] == (36.0, "°C")
    assert out["Engine Oil Temperature 1"] == (104.0, "°C")
    assert out["Engine Intercooler Temperature"] == (33.0, "°C")
    assert "Engine Turbocharger Oil Temperature" not in out        # FF FF: not fitted


def test_coolant_temperature_is_not_read_from_efl_p1():
    """The old table read byte 6 of EFL/P1 as coolant temperature. That byte is
    half of the crankcase pressure."""
    out = decode_pgn(0xFEEF, frame("B1 FF FF 49 FF FF FF FA"))
    assert "Engine Coolant Temperature" not in out
    assert out["Engine Fuel Delivery Pressure"] == (708.0, "kPa")
    assert out["Engine Oil Pressure"] == (292.0, "kPa")
    assert out["Engine Coolant Level"] == (100.0, "%")
    assert "Engine Oil Level" not in out and "Engine Crankcase Pressure" not in out


def test_ambient_conditions_from_the_engine_and_the_body_controller():
    engine = decode_pgn(0xFEF5, frame("CA FF FF FF FF 49 FF FF"))
    assert engine["Barometric Pressure"] == (101.0, "kPa")
    assert engine["Engine Air Inlet Temperature"] == (33.0, "°C")
    body = decode_pgn(0xFEF5, frame("FF FF FF A0 22 FF FF FF"))
    assert body == {"Ambient Air Temperature": (4.0, "°C")}         # January


def test_battery_voltage_is_read_from_its_own_bytes():
    out = decode_pgn(0xFEF7, frame("FF FF FF FF 1A 01 1A 01"))
    assert out == {"Battery Potential / Power Input 1": (14.1, "V"),
                   "Keyswitch Battery Potential": (14.1, "V")}


def test_ccvs_switches_are_two_bit_fields_in_their_own_bytes():
    """The old table decoded Cruise Control Active from byte 1, which holds the
    parking brake and axle switches, and reported it as 195."""
    out = decode_pgn(0xFEF1, frame("C3 40 47 00 00 00 00 30"))
    assert out["Wheel-Based Vehicle Speed"] == (71.25, "km/h")
    assert out["Parking Brake Switch"] == ("not set", "")
    assert out["Cruise Control Active"] == ("off", "")
    assert out["Brake Switch"] == ("released", "")
    assert out["Clutch Switch"] == ("released", "")
    assert out["Cruise Control States"] == ("off/disabled", "")
    assert "Two Speed Axle Switch" not in out                      # 11: not available


def test_two_ecus_agree_on_road_speed():
    """EBC2 from the brakes and CCVS from the engine, a moment apart."""
    brakes = decode_pgn(0xFEBF, frame("D4 46 7E 7C 76 77 FF FF"))
    engine = decode_pgn(0xFEF1, frame("C3 40 47 00 00 00 00 30"))
    assert brakes["Front Axle Speed"][0] == pytest.approx(70.828, abs=1e-3)
    assert abs(brakes["Front Axle Speed"][0] - engine["Wheel-Based Vehicle Speed"][0]) < 1.0
    assert brakes["Relative Speed; Front Axle, Left Wheel"] == (0.0625, "km/h")
    assert brakes["Relative Speed; Rear Axle #1, Left Wheel"] == (-0.4375, "km/h")
    assert "Relative Speed; Rear Axle #2, Left Wheel" not in brakes


def test_absolute_inlet_pressure_is_boost_plus_the_barometer():
    out = decode_pgn(0xFEF6, frame("FF 2B 49 5E FF C6 46 FF"))
    boost = out["Engine Intake Manifold #1 Pressure"][0]
    inlet = out["Engine Air Inlet Pressure"][0]
    assert (boost, inlet) == (86.0, 188.0)
    assert inlet - boost == pytest.approx(101, abs=2)                # AMB said 101 kPa
    assert out["Engine Exhaust Gas Temperature"] == (293.1875, "°C")


def test_engine_controller_1():
    out = decode_pgn(0xF004, frame("21 B7 B7 40 27 00 F4 B7"))
    assert out["Engine Speed"] == (1256.0, "rpm")
    assert out["Engine Torque Mode"] == ("accelerator pedal / operator selection", "")
    assert out["Driver's Demand Engine - Percent Torque"] == (58.0, "%")
    assert out["Actual Engine - Percent Torque"] == (58.0, "%")


def test_the_retarder_names_itself_as_the_controlling_device():
    out = decode_pgn(0xF000, frame("00 7D FF FF 0F 7D FF FF"))
    assert out["Actual Retarder - Percent Torque"] == (0.0, "%")
    assert out["Source Address of Controlling Device for Retarder Control"][0] == 15.0
    assert parse_j1939_id(0x18F0000F)["sa_name"] == "Retarder - Engine (0x0F)"


def test_lifetime_counters():
    hours = decode_pgn(0xFEE5, frame("96 9F 02 00 C9 E6 08 00"))
    assert hours["Engine Total Hours of Operation"] == (8596.3, "h")
    assert hours["Engine Total Revolutions"] == (583369000.0, "r")
    mean_rpm = hours["Engine Total Revolutions"][0] / hours["Engine Total Hours of Operation"][0] / 60
    assert 900 < mean_rpm < 1400                                      # a working diesel's life
    dist = decode_pgn(0xFEE0, frame("E9 B8 38 00 E9 B8 38 00"))
    assert dist["Total Vehicle Distance"] == (464669.125, "km")


def test_time_date_and_position_from_the_telematics_unit():
    td = decode_pgn(0xFEE6, frame("C4 30 09 01 34 23 83 7E"))
    assert (td["Year"][0], td["Month"][0], td["Day"][0]) == (2020.0, 1.0, 13.0)
    assert (td["Hours"][0], td["Minutes"][0], td["Seconds"][0]) == (9.0, 48.0, 49.0)
    vp = decode_pgn(0xFEF3, frame("8C 1A 5A 95 E1 8C DC 4F"))
    assert vp["Latitude"][0] == pytest.approx(40.5710, abs=1e-4)
    assert vp["Longitude"][0] == pytest.approx(-76.0146, abs=1e-4)
    assert parse_j1939_id(0x18FEE64A)["sa_name"] == "Communications Unit, Cellular (0x4A)"


def test_torque_speed_control_from_the_brakes():
    out = decode_pgn(0x0000, frame("FC FF FA FA FF FF FF FF"))
    assert out["Engine Override Control Mode"] == ("override disabled", "")
    assert out["Override Control Mode Priority"] == ("low", "")
    assert out["Engine Requested Speed/Speed Limit"] == (8031.875, "rpm")


# ── the J1939-71 value ranges ────────────────────────────────────────────────

def test_an_error_code_is_reported_as_an_error_not_a_reading():
    out = decode_pgn(0xFEEE, frame("FE FF FF FE FF FF FF FF"))      # 0xFE, 0xFExx
    assert out["Engine Coolant Temperature"] == ("error", "")
    assert out["Engine Oil Temperature 1"] == ("error", "")
    assert classify(0xFE, 8) == "error" and classify(0xFE12, 16) == "error"
    assert classify(0xFE000001, 32) == "error"


def test_reserved_and_not_available_codes_are_not_values():
    for raw in (0xFB, 0xFC, 0xFD, 0xFF):
        assert "Engine Coolant Temperature" not in decode_pgn(0xFEEE, bytes([raw]) + b"\xff" * 7)
    assert classify(0xFAFF, 16) == "valid" and classify(0xFB00, 16) == "reserved"
    assert classify(0xFF00, 16) == "na"


def test_two_bit_states():
    # CCVS byte 4: cruise active 01, enable 10 (error), brake 11 (n/a), clutch 00
    out = decode_pgn(0xFEF1, frame("FF FF FF 39 FF FF FF FF"))
    assert out["Cruise Control Active"] == ("active", "")
    assert out["Cruise Control Enable Switch"] == ("error", "")
    assert "Brake Switch" not in out
    assert out["Clutch Switch"] == ("released", "")


def test_gear_park_and_range_text():
    out = decode_pgn(0xF005, frame("FB E8 03 7C 44 20 44 20"))
    assert out["Transmission Selected Gear"] == ("park", "")
    assert out["Transmission Actual Gear Ratio"] == (1.0, "")
    assert out["Transmission Current Gear"] == (-1.0, "")           # one reverse gear
    assert out["Transmission Current Range"] == ("D", "")


def test_a_short_frame_decodes_what_it_holds():
    assert decode_pgn(0xFEEE, bytes([0x82])) == {"Engine Coolant Temperature": (90.0, "°C")}


def test_decode_spns_keeps_numbers_and_status():
    rows = {r["spn"]: r for r in decode_spns(0xFEEE, frame("FE 4C FF FF FF FF 49 FF"))}
    assert rows[110]["status"] == "error" and rows[110]["value"] is None
    assert rows[174]["status"] == "ok" and rows[174]["value"] == 36.0
    assert rows[175]["status"] == "not available"


# ── the tables themselves ────────────────────────────────────────────────────

def test_parameter_groups_carry_their_j1939_71_numbers():
    by_acronym = {p.acronym: pgn for pgn, p in PGNS.items()}
    assert by_acronym["ETC1"] == 0xF002 and by_acronym["ETC2"] == 0xF005
    assert by_acronym["ERC1"] == 0xF000 and by_acronym["EBC1"] == 0xF001
    assert by_acronym["EBC2"] == 0xFEBF and by_acronym["ET1"] == 0xFEEE
    assert by_acronym["VEP1"] == 0xFEF7 and by_acronym["CCVS"] == 0xFEF1


def test_no_two_parameters_overlap_in_a_message():
    for pgn, p in PGNS.items():
        taken = set()
        for spec in p.spns:
            bits = set(range(spec.bit, spec.bit + spec.bits))
            assert not bits & taken, f"{p.acronym}: SPN {spec.spn} overlaps"
            assert max(bits) < 64, f"{p.acronym}: SPN {spec.spn} runs past 8 bytes"
            taken |= bits


def test_source_addresses_use_the_preferred_address_table():
    assert sa_name(0x00) == "Engine #1 (0x00)"
    assert sa_name(0x0B) == "Brakes - System Controller (0x0B)"
    assert sa_name(0x17) == "Instrument Cluster #1 (0x17)"
    assert sa_name(0x21) == "Body Controller (0x21)"
    assert sa_name(0x3D) == "Exhaust Emission Controller (0x3D)"
    assert sa_name(0xF9) == "Off Board Diagnostic-Service Tool #1 (0xF9)"
    assert sa_name(0x90) == "SA 0x90"                               # dynamic range


def test_names_for_proprietary_and_unknown_groups():
    assert pgn_name(0xFF21).startswith("PropB")
    assert pgn_name(0xF004).startswith("EEC1")
    assert pgn_name(0xFD41) == "PGN 0xFD41"


def test_the_scan_lists_every_sender_with_its_own_count():
    """Keyed by PGN alone, the scan kept the first sender and dropped the rest."""
    rows = ([{"ID": "18FEF100", "Timestamp": i * 0.1} for i in range(10)]
            + [{"ID": "18FEF121", "Timestamp": i * 0.1} for i in range(4)]
            + [{"ID": "18FECA3D", "Timestamp": i} for i in range(3)]
            + [{"ID": "18FECA0B", "Timestamp": i} for i in range(2)]
            + [{"ID": "0A6", "Timestamp": 0.0}])
    scan = scan_for_j1939(pd.DataFrame(rows))
    got = {(r["pgn"], r["sa"]): r["frame_count"] for r in scan}
    assert got == {(0xFEF1, 0x00): 10, (0xFEF1, 0x21): 4,
                   (0xFECA, 0x3D): 3, (0xFECA, 0x0B): 2}
    assert sum(got.values()) == 19                                  # every 29-bit frame
    assert scan[0]["pgn_name"].startswith("CCVS")
