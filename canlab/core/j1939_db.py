"""J1939 parameter groups, their parameters, and the preferred source addresses.

Layouts follow SAE J1939-71 as it is published: byte and bit positions are
counted from the first data byte and its least significant bit, and multi-byte
values are little-endian. A parameter is written down here only when its
position, resolution and offset are known; a PGN whose layout is not known is
listed by name alone, so the scan can say what a message is without inventing
what it contains.

Every decoded message in the 145,534-frame truck log in the acceptance corpus
is checked for plausibility and against the others it should agree with: the
front axle speed in EBC2 against the wheel-based speed in CCVS, the coolant
temperature of a running diesel, a battery voltage, barometric pressure near
sea level, distances and hours that only grow.

The table this replaces had coolant temperature in the wrong message, ETC1 and
the wheel speeds and ERC1 filed under other PGNs, the battery voltages read
from the current bytes, and every switch decoded as a whole byte. Those are
the reasons for the tests that now pin each layout.
"""
from __future__ import annotations

from typing import NamedTuple


class Spn(NamedTuple):
    """One suspect parameter number, as a bit field in a parameter group."""

    spn: int
    name: str
    bit: int                      # first bit: byte index * 8 + bit within byte
    bits: int
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    states: dict | None = None    # discrete fields: raw value -> label
    ascii: bool = False
    special: dict | None = None   # measured values with named codes (gear 251 = park)


class Pgn(NamedTuple):
    acronym: str
    title: str
    spns: tuple = ()

    @property
    def name(self) -> str:
        return f"{self.acronym} - {self.title}" if self.acronym else self.title


# ── builders, so each line reads like the specification ──────────────────────

def _v(spn, name, byte, nbytes, scale=1.0, offset=0.0, unit="", special=None):
    """A measured value occupying whole bytes, starting at data byte `byte` (1-based)."""
    return Spn(spn, name, (byte - 1) * 8, nbytes * 8, scale, offset, unit, special=special)


def _s(spn, name, byte, bit, states=None, bits=2):
    """A discrete field at data byte `byte` (1-based), starting at bit `bit` (1-based)."""
    return Spn(spn, name, (byte - 1) * 8 + (bit - 1), bits, states=states or SWITCH)


def _a(spn, name, byte, nbytes):
    return Spn(spn, name, (byte - 1) * 8, nbytes * 8, ascii=True)


#: The two-bit state every J1939 switch uses. 10 is an error and 11 is "not
#: available"; the decoder handles those for every discrete field.
SWITCH = {0: "off", 1: "on"}
ACTIVE = {0: "not active", 1: "active"}

TORQUE_MODE = {
    0: "low idle governor / no request", 1: "accelerator pedal / operator selection",
    2: "cruise control", 3: "PTO governor", 4: "road speed governor",
    5: "ASR control", 6: "transmission control", 7: "ABS control",
    8: "torque limiting", 9: "high speed governor", 10: "braking system",
    11: "remote accelerator",
}
OVERRIDE_MODE = {0: "override disabled", 1: "speed control", 2: "torque control",
                 3: "speed/torque limit control"}
CRUISE_STATES = {0: "off/disabled", 1: "hold", 2: "accelerate", 3: "decelerate",
                 4: "resume", 5: "set", 6: "accelerator override"}

#: A transmission gear: negative is reverse, 0 is neutral, 251 is park.
GEAR_CODES = {251: "park"}

PCT = "%"
RPM = "rpm"
KPA = "kPa"
DEGC = "°C"
KMH = "km/h"


PGNS: dict[int, Pgn] = {
    0x0000: Pgn("TSC1", "Torque/Speed Control 1", (
        _s(695, "Engine Override Control Mode", 1, 1, OVERRIDE_MODE),
        _s(696, "Engine Requested Speed Control Conditions", 1, 3,
           {0: "transient optimized, driveline disengaged",
            1: "stability optimized, driveline disengaged",
            2: "stability optimized, driveline engaged, condition 1",
            3: "stability optimized, driveline engaged, condition 2"}),
        _s(897, "Override Control Mode Priority", 1, 5,
           {0: "highest", 1: "high", 2: "medium", 3: "low"}),
        _v(898, "Engine Requested Speed/Speed Limit", 2, 2, 0.125, 0, RPM),
        _v(518, "Engine Requested Torque/Torque Limit", 4, 1, 1, -125, PCT),
    )),
    0xF000: Pgn("ERC1", "Electronic Retarder Controller 1", (
        _s(900, "Retarder Torque Mode", 1, 1, TORQUE_MODE, bits=4),
        _s(571, "Retarder Enable - Brake Assist Switch", 1, 5),
        _s(572, "Retarder Enable - Shift Assist Switch", 1, 7),
        _v(520, "Actual Retarder - Percent Torque", 2, 1, 1, -125, PCT),
        _v(1085, "Intended Retarder Percent Torque", 3, 1, 1, -125, PCT),
        _v(1480, "Source Address of Controlling Device for Retarder Control", 5, 1),
        _v(1715, "Drivers Demand Retarder - Percent Torque", 6, 1, 1, -125, PCT),
        _v(1716, "Retarder Selection, Non-engine", 7, 1, 0.4, 0, PCT),
        _v(1717, "Actual Maximum Available Retarder - Percent Torque", 8, 1, 1, -125, PCT),
    )),
    0xF001: Pgn("EBC1", "Electronic Brake Controller 1", (
        _s(561, "ASR Engine Control Active", 1, 1, ACTIVE),
        _s(562, "ASR Brake Control Active", 1, 3, ACTIVE),
        _s(563, "Anti-Lock Braking (ABS) Active", 1, 5, ACTIVE),
        _s(1121, "EBS Brake Switch", 1, 7),
        _v(521, "Brake Pedal Position", 2, 1, 0.4, 0, PCT),
        _s(575, "ABS Off-road Switch", 3, 1),
        _s(576, "ASR Off-road Switch", 3, 3),
        _s(577, "ASR Hill Holder Switch", 3, 5),
        _s(1238, "Traction Control Override Switch", 3, 7),
        _s(972, "Accelerator Interlock Switch", 4, 1),
        _s(971, "Engine Derate Switch", 4, 3),
        _s(970, "Engine Auxiliary Shutdown Switch", 4, 5),
        _s(969, "Remote Accelerator Enable Switch", 4, 7),
        _v(973, "Engine Retarder Selection", 5, 1, 0.4, 0, PCT),
        _s(1243, "ABS Fully Operational", 6, 1, {0: "not fully operational", 1: "fully operational"}),
        _s(1439, "EBS Red Warning Signal", 6, 3),
        _s(1438, "ABS/EBS Amber Warning Signal", 6, 5),
        _s(1793, "ATC/ASR Information Signal", 6, 7),
        _v(1481, "Source Address of Controlling Device for Brake Control", 7, 1),
    )),
    0xF002: Pgn("ETC1", "Electronic Transmission Controller 1", (
        _s(560, "Transmission Driveline Engaged", 1, 1, {0: "disengaged", 1: "engaged"}),
        _s(573, "Transmission Torque Converter Lockup Engaged", 1, 3,
           {0: "disengaged", 1: "engaged"}),
        _s(574, "Transmission Shift In Process", 1, 5,
           {0: "not in process", 1: "in process"}),
        _v(191, "Transmission Output Shaft Speed", 2, 2, 0.125, 0, RPM),
        _v(522, "Percent Clutch Slip", 4, 1, 0.4, 0, PCT),
        _s(606, "Engine Momentary Overspeed Enable", 5, 1,
           {0: "disabled", 1: "enabled"}),
        _s(607, "Progressive Shift Disable", 5, 3, {0: "not disabled", 1: "disabled"}),
        _v(161, "Transmission Input Shaft Speed", 6, 2, 0.125, 0, RPM),
        _v(1482, "Source Address of Controlling Device for Transmission Control", 8, 1),
    )),
    0xF003: Pgn("EEC2", "Electronic Engine Controller 2", (
        _s(558, "Accelerator Pedal 1 Low Idle Switch", 1, 1,
           {0: "not in low idle", 1: "in low idle"}),
        _s(559, "Accelerator Pedal Kickdown Switch", 1, 3,
           {0: "kickdown passive", 1: "kickdown active"}),
        _v(91, "Accelerator Pedal Position 1", 2, 1, 0.4, 0, PCT),
        _v(92, "Engine Percent Load At Current Speed", 3, 1, 1, 0, PCT),
        _v(974, "Remote Accelerator Pedal Position", 4, 1, 0.4, 0, PCT),
        _v(29, "Accelerator Pedal Position 2", 5, 1, 0.4, 0, PCT),
    )),
    0xF004: Pgn("EEC1", "Electronic Engine Controller 1", (
        _s(899, "Engine Torque Mode", 1, 1, TORQUE_MODE, bits=4),
        _v(512, "Driver's Demand Engine - Percent Torque", 2, 1, 1, -125, PCT),
        _v(513, "Actual Engine - Percent Torque", 3, 1, 1, -125, PCT),
        _v(190, "Engine Speed", 4, 2, 0.125, 0, RPM),
        _v(1483, "Source Address of Controlling Device for Engine Control", 6, 1),
        _v(2432, "Engine Demand - Percent Torque", 8, 1, 1, -125, PCT),
    )),
    0xF005: Pgn("ETC2", "Electronic Transmission Controller 2", (
        _v(524, "Transmission Selected Gear", 1, 1, 1, -125, special=GEAR_CODES),
        _v(526, "Transmission Actual Gear Ratio", 2, 2, 0.001),
        _v(523, "Transmission Current Gear", 4, 1, 1, -125, special=GEAR_CODES),
        _a(162, "Transmission Requested Range", 5, 2),
        _a(163, "Transmission Current Range", 7, 2),
    )),
    0xFEBF: Pgn("EBC2", "Wheel Speed Information", (
        _v(904, "Front Axle Speed", 1, 2, 1 / 256, 0, KMH),
        _v(905, "Relative Speed; Front Axle, Left Wheel", 3, 1, 1 / 16, -7.8125, KMH),
        _v(906, "Relative Speed; Front Axle, Right Wheel", 4, 1, 1 / 16, -7.8125, KMH),
        _v(907, "Relative Speed; Rear Axle #1, Left Wheel", 5, 1, 1 / 16, -7.8125, KMH),
        _v(908, "Relative Speed; Rear Axle #1, Right Wheel", 6, 1, 1 / 16, -7.8125, KMH),
        _v(909, "Relative Speed; Rear Axle #2, Left Wheel", 7, 1, 1 / 16, -7.8125, KMH),
        _v(910, "Relative Speed; Rear Axle #2, Right Wheel", 8, 1, 1 / 16, -7.8125, KMH),
    )),
    0xFEC1: Pgn("HRVD", "High Resolution Vehicle Distance", (
        _v(917, "High Resolution Total Vehicle Distance", 1, 4, 0.005, 0, "km"),
        _v(918, "High Resolution Trip Distance", 5, 4, 0.005, 0, "km"),
    )),
    0xFEDF: Pgn("EEC3", "Electronic Engine Controller 3", (
        _v(514, "Nominal Friction - Percent Torque", 1, 1, 1, -125, PCT),
        _v(515, "Engine's Desired Operating Speed", 2, 2, 0.125, 0, RPM),
        _v(519, "Engine's Desired Operating Speed Asymmetry Adjustment", 4, 1),
    )),
    0xFEE0: Pgn("VD", "Vehicle Distance", (
        _v(244, "Trip Distance", 1, 4, 0.125, 0, "km"),
        _v(245, "Total Vehicle Distance", 5, 4, 0.125, 0, "km"),
    )),
    0xFEE5: Pgn("HOURS", "Engine Hours, Revolutions", (
        _v(247, "Engine Total Hours of Operation", 1, 4, 0.05, 0, "h"),
        _v(249, "Engine Total Revolutions", 5, 4, 1000, 0, "r"),
    )),
    0xFEE6: Pgn("TD", "Time/Date", (
        _v(959, "Seconds", 1, 1, 0.25, 0, "s"),
        _v(960, "Minutes", 2, 1, 1, 0, "min"),
        _v(961, "Hours", 3, 1, 1, 0, "h"),
        _v(963, "Month", 4, 1, 1, 0, ""),
        _v(962, "Day", 5, 1, 0.25, 0, "d"),
        _v(964, "Year", 6, 1, 1, 1985, ""),
        _v(1601, "Local Minute Offset", 7, 1, 1, -125, "min"),
        _v(1602, "Local Hour Offset", 8, 1, 1, -125, "h"),
    )),
    0xFEE7: Pgn("VH", "Vehicle Hours", (
        _v(246, "Total Vehicle Hours", 1, 4, 0.05, 0, "h"),
        _v(248, "Total Power Takeoff Hours", 5, 4, 0.05, 0, "h"),
    )),
    0xFEE9: Pgn("LFC", "Fuel Consumption (Liquid)", (
        _v(182, "Engine Trip Fuel", 1, 4, 0.5, 0, "L"),
        _v(250, "Engine Total Fuel Used", 5, 4, 0.5, 0, "L"),
    )),
    0xFEEE: Pgn("ET1", "Engine Temperature 1", (
        _v(110, "Engine Coolant Temperature", 1, 1, 1, -40, DEGC),
        _v(174, "Engine Fuel Temperature 1", 2, 1, 1, -40, DEGC),
        _v(175, "Engine Oil Temperature 1", 3, 2, 0.03125, -273, DEGC),
        _v(176, "Engine Turbocharger Oil Temperature", 5, 2, 0.03125, -273, DEGC),
        _v(52, "Engine Intercooler Temperature", 7, 1, 1, -40, DEGC),
        _v(1134, "Engine Intercooler Thermostat Opening", 8, 1, 0.4, 0, PCT),
    )),
    0xFEEF: Pgn("EFL/P1", "Engine Fluid Level/Pressure 1", (
        _v(94, "Engine Fuel Delivery Pressure", 1, 1, 4, 0, KPA),
        _v(22, "Engine Extended Crankcase Blow-by Pressure", 2, 1, 0.05, 0, KPA),
        _v(98, "Engine Oil Level", 3, 1, 0.4, 0, PCT),
        _v(100, "Engine Oil Pressure", 4, 1, 4, 0, KPA),
        _v(101, "Engine Crankcase Pressure", 5, 2, 1 / 128, -250, KPA),
        _v(109, "Engine Coolant Pressure", 7, 1, 2, 0, KPA),
        _v(111, "Engine Coolant Level", 8, 1, 0.4, 0, PCT),
    )),
    0xFEF1: Pgn("CCVS", "Cruise Control/Vehicle Speed", (
        _s(69, "Two Speed Axle Switch", 1, 1, {0: "low speed range", 1: "high speed range"}),
        _s(70, "Parking Brake Switch", 1, 3, {0: "not set", 1: "set"}),
        _s(1633, "Cruise Control Pause Switch", 1, 5),
        _v(84, "Wheel-Based Vehicle Speed", 2, 2, 1 / 256, 0, KMH),
        _s(595, "Cruise Control Active", 4, 1, {0: "off", 1: "active"}),
        _s(596, "Cruise Control Enable Switch", 4, 3, {0: "disabled", 1: "enabled"}),
        _s(597, "Brake Switch", 4, 5, {0: "released", 1: "depressed"}),
        _s(598, "Clutch Switch", 4, 7, {0: "released", 1: "depressed"}),
        _s(599, "Cruise Control Set Switch", 5, 1),
        _s(600, "Cruise Control Coast (Decelerate) Switch", 5, 3),
        _s(601, "Cruise Control Resume Switch", 5, 5),
        _s(602, "Cruise Control Accelerate Switch", 5, 7),
        _v(86, "Cruise Control Set Speed", 6, 1, 1, 0, KMH),
        _s(527, "Cruise Control States", 7, 6, CRUISE_STATES, bits=3),
        _s(968, "Engine Idle Increment Switch", 8, 1),
        _s(967, "Engine Idle Decrement Switch", 8, 3),
        _s(966, "Engine Test Mode Switch", 8, 5),
        _s(1237, "Engine Shutdown Override Switch", 8, 7),
    )),
    0xFEF2: Pgn("LFE", "Fuel Economy (Liquid)", (
        _v(183, "Engine Fuel Rate", 1, 2, 0.05, 0, "L/h"),
        _v(184, "Engine Instantaneous Fuel Economy", 3, 2, 1 / 512, 0, "km/L"),
        _v(185, "Engine Average Fuel Economy", 5, 2, 1 / 512, 0, "km/L"),
        _v(51, "Engine Throttle Valve 1 Position", 7, 1, 0.4, 0, PCT),
        _v(3673, "Engine Throttle Valve 2 Position", 8, 1, 0.4, 0, PCT),
    )),
    0xFEF5: Pgn("AMB", "Ambient Conditions", (
        _v(108, "Barometric Pressure", 1, 1, 0.5, 0, KPA),
        _v(170, "Cab Interior Temperature", 2, 2, 0.03125, -273, DEGC),
        _v(171, "Ambient Air Temperature", 4, 2, 0.03125, -273, DEGC),
        _v(172, "Engine Air Inlet Temperature", 6, 1, 1, -40, DEGC),
        _v(79, "Road Surface Temperature", 7, 2, 0.03125, -273, DEGC),
    )),
    0xFEF6: Pgn("IC1", "Inlet/Exhaust Conditions 1", (
        _v(81, "Engine Diesel Particulate Filter Inlet Pressure", 1, 1, 0.5, 0, KPA),
        _v(102, "Engine Intake Manifold #1 Pressure", 2, 1, 2, 0, KPA),
        _v(105, "Engine Intake Manifold 1 Temperature", 3, 1, 1, -40, DEGC),
        _v(106, "Engine Air Inlet Pressure", 4, 1, 2, 0, KPA),
        _v(107, "Engine Air Filter 1 Differential Pressure", 5, 1, 0.05, 0, KPA),
        _v(173, "Engine Exhaust Gas Temperature", 6, 2, 0.03125, -273, DEGC),
        _v(112, "Engine Coolant Filter Differential Pressure", 8, 1, 0.5, 0, KPA),
    )),
    0xFEF7: Pgn("VEP1", "Vehicle Electrical Power 1", (
        _v(114, "Net Battery Current", 1, 1, 1, -125, "A"),
        _v(115, "Alternator Current", 2, 1, 1, 0, "A"),
        _v(167, "Charging System Potential (Voltage)", 3, 2, 0.05, 0, "V"),
        _v(168, "Battery Potential / Power Input 1", 5, 2, 0.05, 0, "V"),
        _v(158, "Keyswitch Battery Potential", 7, 2, 0.05, 0, "V"),
    )),
    0xFEFC: Pgn("DD", "Dash Display", (
        _v(80, "Washer Fluid Level", 1, 1, 0.4, 0, PCT),
        _v(96, "Fuel Level 1", 2, 1, 0.4, 0, PCT),
        _v(95, "Engine Fuel Filter Differential Pressure", 3, 1, 2, 0, KPA),
        _v(99, "Engine Oil Filter Differential Pressure", 4, 1, 0.5, 0, KPA),
        _v(169, "Cargo Ambient Temperature", 5, 2, 0.03125, -273, DEGC),
        _v(38, "Fuel Level 2", 7, 1, 0.4, 0, PCT),
    )),
    0xFEF3: Pgn("VP1", "Vehicle Position 1", (
        _v(584, "Latitude", 1, 4, 1e-7, -210, "deg"),
        _v(585, "Longitude", 5, 4, 1e-7, -210, "deg"),
    )),
    0xFEF0: Pgn("PTO", "Power Takeoff Information", (
        _v(90, "Power Takeoff Oil Temperature", 1, 1, 1, -40, DEGC),
        _v(186, "Power Takeoff Speed", 2, 2, 0.125, 0, RPM),
        _v(187, "Power Takeoff Set Speed", 4, 2, 0.125, 0, RPM),
    )),
    0xFE6C: Pgn("TCO1", "Tachograph", (
        _v(1624, "Tachograph Vehicle Speed", 7, 2, 1 / 256, 0, KMH),
    )),

    # Named, not decoded: their layouts are either variable (identification
    # strings, diagnostic lists), carried by the transport protocol, or not
    # documented well enough here to decode without guessing.
    0xE000: Pgn("CM1", "Cab Message 1"),
    0xE800: Pgn("ACKM", "Acknowledgment"),
    0xEA00: Pgn("RQST", "Request"),
    0xEB00: Pgn("TP.DT", "Transport Protocol Data Transfer"),
    0xEC00: Pgn("TP.CM", "Transport Protocol Connection Management"),
    0xEE00: Pgn("AC", "Address Claimed"),
    0xEF00: Pgn("PropA", "Proprietary A"),
    0xF00A: Pgn("EGF1", "Engine Gas Flow Rate"),
    0xF00E: Pgn("AT1IG1", "Aftertreatment 1 Intake Gas 1"),
    0xF00F: Pgn("AT1OG1", "Aftertreatment 1 Outlet Gas 1"),
    0xFE4F: Pgn("VDC1", "Vehicle Dynamic Stability Control 1"),
    0xFEBD: Pgn("FD", "Fan Drive"),
    0xFEC3: Pgn("ETC5", "Electronic Transmission Controller 5"),
    0xFECA: Pgn("DM1", "Active Diagnostic Trouble Codes"),
    0xFECB: Pgn("DM2", "Previously Active Diagnostic Trouble Codes"),
    0xFECC: Pgn("DM3", "Diagnostic Data Clear/Reset of Previously Active DTCs"),
    0xFEDA: Pgn("SOFT", "Software Identification"),
    0xFEDB: Pgn("EFL/P2", "Engine Fluid Level/Pressure 2"),
    0xFEDC: Pgn("IO", "Idle Operation"),
    0xFEDD: Pgn("TC", "Turbocharger"),
    0xFEE1: Pgn("RC", "Retarder Configuration"),
    0xFEE3: Pgn("EC1", "Engine Configuration 1"),
    0xFEE4: Pgn("SHUTDN", "Shutdown"),
    0xFEEB: Pgn("CI", "Component Identification"),
    0xFEEC: Pgn("VI", "Vehicle Identification"),
    0xFEFA: Pgn("B", "Brakes"),
    0xFEFF: Pgn("WFI", "Water in Fuel Indicator"),
    0xFE92: Pgn("EI", "Engine Information"),
    0xFE69: Pgn("ET3", "Engine Temperature 3"),
    0xFD7C: Pgn("DPFC1", "Diesel Particulate Filter Control 1"),
}


def proprietary_name(pgn: int) -> str | None:
    """Proprietary B covers a whole range; its contents are the manufacturer's."""
    if 0xFF00 <= pgn <= 0xFFFF:
        return "PropB - Proprietary B (manufacturer defined)"
    if pgn == 0x1EF00:
        return "PropA2 - Proprietary A2"
    return None


#: SAE J1939 preferred source addresses (the industry-group-independent
#: table). Addresses 128 to 247 are assigned dynamically or by industry group
#: and have no fixed meaning, so they are reported by number.
SOURCE_ADDRESSES: dict[int, str] = {
    0: "Engine #1", 1: "Engine #2", 2: "Turbocharger", 3: "Transmission #1",
    4: "Transmission #2", 5: "Shift Console - Primary", 6: "Shift Console - Secondary",
    7: "Power TakeOff - (Main or Rear)", 8: "Axle - Steering", 9: "Axle - Drive #1",
    10: "Axle - Drive #2", 11: "Brakes - System Controller", 12: "Brakes - Steer Axle",
    13: "Brakes - Drive Axle #1", 14: "Brakes - Drive Axle #2", 15: "Retarder - Engine",
    16: "Retarder - Driveline", 17: "Cruise Control", 18: "Fuel System",
    19: "Steering Controller", 20: "Suspension - Steer Axle",
    21: "Suspension - Drive Axle #1", 22: "Suspension - Drive Axle #2",
    23: "Instrument Cluster #1", 24: "Trip Recorder",
    25: "Passenger-Operator Climate Control #1",
    26: "Alternator/Electrical Charging System", 27: "Aerodynamic Control",
    28: "Vehicle Navigation", 29: "Vehicle Security", 30: "Electrical System",
    31: "Starter System", 32: "Tractor-Trailer Bridge #1", 33: "Body Controller",
    34: "Auxiliary Valve Control or Engine Air System Valve Control",
    35: "Hitch Control", 36: "Power TakeOff (Front or Secondary)",
    37: "Off Vehicle Gateway", 38: "Virtual Terminal (in cab)",
    39: "Management Computer #1", 40: "Cab Display #1",
    41: "Retarder, Exhaust, Engine #1", 42: "Headway Controller",
    43: "On-Board Diagnostic Unit", 44: "Retarder, Exhaust, Engine #2",
    45: "Endurance Braking System", 46: "Hydraulic Pump Controller",
    47: "Suspension - System Controller #1", 48: "Pneumatic - System Controller",
    49: "Cab Controller - Primary", 50: "Cab Controller - Secondary",
    51: "Tire Pressure Controller", 52: "Ignition Control Module #1",
    53: "Ignition Control Module #2", 54: "Seat Control #1",
    55: "Lighting - Operator Controls", 56: "Rear Axle Steering Controller #1",
    57: "Water Pump Controller", 58: "Passenger-Operator Climate Control #2",
    59: "Transmission Display - Primary", 60: "Transmission Display - Secondary",
    61: "Exhaust Emission Controller", 62: "Vehicle Dynamic Stability Controller",
    63: "Oil Sensor", 64: "Suspension - System Controller #2",
    65: "Information System Controller #1", 66: "Ramp Control",
    67: "Clutch/Converter Unit", 68: "Auxiliary Heater #1", 69: "Auxiliary Heater #2",
    70: "Engine Valve Controller", 71: "Chassis Controller #1",
    72: "Chassis Controller #2", 73: "Propulsion Battery Charger",
    74: "Communications Unit, Cellular", 75: "Communications Unit, Satellite",
    76: "Communications Unit, Radio", 77: "Steering Column Unit",
    78: "Fan Drive Controller", 79: "Seat Control #2", 80: "Parking Brake Controller",
    81: "Aftertreatment #1 System Gas Intake", 82: "Aftertreatment #1 System Gas Outlet",
    83: "Safety Restraint System", 84: "Cab Climate Control",
    85: "Aftertreatment #2 System Gas Intake", 86: "Aftertreatment #2 System Gas Outlet",
    248: "File Server / Printer", 249: "Off Board Diagnostic-Service Tool #1",
    250: "Off Board Diagnostic-Service Tool #2", 251: "On-Board Data Logger",
    252: "Reserved for Experimental Use", 253: "Reserved for OEM",
    254: "Null Address", 255: "Global",
}


# ── decoding one field ───────────────────────────────────────────────────────

NOT_AVAILABLE = object()
ERROR = "error"


def extract(data: bytes, bit: int, bits: int) -> int | None:
    """Little-endian bit field; None when the frame is too short."""
    if bit + bits > len(data) * 8:
        return None
    value = int.from_bytes(bytes(data), "little")
    return (value >> bit) & ((1 << bits) - 1)


def classify(raw: int, bits: int) -> str:
    """"valid", "error", "reserved" or "na", by the J1939-71 ranges.

    Whole bytes, words and double words reserve their top codes: for one byte,
    251 to 253 are reserved, 254 is an error and 255 is not available, and the
    wider ranges follow the same pattern in their top byte. A two-bit field
    uses 10 for error and 11 for not available; wider discrete fields use their
    two highest codes the same way.
    """
    if bits in (8, 16, 32):
        top = raw >> (bits - 8)
        if top <= 0xFA:
            return "valid"
        if top == 0xFE:
            return "error"
        if top == 0xFF:
            return "na"
        return "reserved"
    full = (1 << bits) - 1
    if raw == full:
        return "na"
    if bits >= 2 and raw == full - 1:
        return "error"
    return "valid"


def decode_spn(spec: Spn, data: bytes):
    """The value, a state label, ERROR, or NOT_AVAILABLE for one parameter."""
    raw = extract(data, spec.bit, spec.bits)
    if raw is None:
        return NOT_AVAILABLE
    if spec.ascii:
        text = bytes(data)[spec.bit // 8:(spec.bit + spec.bits) // 8]
        if all(b == 0xFF for b in text) or all(b == 0 for b in text):
            return NOT_AVAILABLE
        return text.decode("ascii", "replace").strip("\x00 ")
    if spec.special and raw in spec.special:
        return spec.special[raw]
    if spec.states is not None and raw in spec.states:
        return spec.states[raw]
    kind = classify(raw, spec.bits)
    if kind == "na" or kind == "reserved":
        return NOT_AVAILABLE
    if kind == "error":
        return ERROR
    if spec.states is not None:
        return f"state {raw}"
    return raw * spec.scale + spec.offset
