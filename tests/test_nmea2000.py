"""NMEA 2000 recognition, and the J1939 tables it must not disturb.

A marine bus and a truck bus are the same 29-bit frames. Before this, a
recording off a boat came back as fifty J1939 messages with no PGN names, which
reads as "J1939 with nothing recognised" rather than "this is not J1939".

Every expected value below was read off a real CANedge recording from a vessel
on Lake Erie, and the layouts are cross-checked against each other: the
position fixes the location, and the magnetic variation the decoder reports for
that location is the one a chart gives for it.
"""
import pytest

from canlab.core.j1939 import (
    decode_n2k, decode_pgn, is_nmea2000, parse_j1939_id, scan_for_j1939,
)


def frame(*values: int) -> bytes:
    data = bytes(values)
    return data + b"\x00" * (8 - len(data))


#: Frames lifted verbatim from the recording, so the expected values below are
#: what the boat's instruments actually said rather than what I typed.
REAL = {
    127250: frame(0xD7, 0x84, 0x4D, 0x00, 0x00, 0xEF, 0xF9, 0xFD),
    127251: frame(0xA2, 0xE9, 0xF2, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF),
    129025: frame(0xE6, 0x8D, 0x6D, 0x19, 0x90, 0xEA, 0x97, 0xCF),
    129026: frame(0x21, 0xFC, 0x7C, 0x8E, 0x00, 0x00, 0xFF, 0xFF),
    130306: frame(0x44, 0x4D, 0x00, 0x0F, 0x42, 0xF8, 0xFF, 0xFF),
    130311: frame(0x22, 0xC1, 0x65, 0x72, 0xFF, 0x7F, 0xE1, 0x03),
}


# ── telling the two protocols apart ──────────────────────────────────────────

def test_the_data_page_decides_which_protocol_it_is():
    marine = parse_j1939_id(0x09F11223)       # data page 1
    assert marine["protocol"] == "NMEA 2000"
    assert marine["pgn"] == 127250
    assert marine["pgn_name"] == "Vessel Heading"

    truck = parse_j1939_id(0x0CF00400)        # data page 0
    assert truck["protocol"] == "J1939"
    assert truck["pgn"] == 0xF004
    assert truck["pgn_name"].startswith("EEC1")


def test_the_range_is_what_marks_a_pgn_marine():
    assert not is_nmea2000(0xF004)            # 61444, an engine message
    assert is_nmea2000(127250) and is_nmea2000(130306)
    assert not is_nmea2000(130837)            # one past the top


def test_j1939_decoding_is_unchanged():
    """The data page mask widened to two bits; ordinary J1939 must not move."""
    spns = decode_pgn(0xF004, frame(0, 0, 0, 0x00, 0x20, 0, 0, 0))
    assert spns["Engine Speed"] == (1024.0, "rpm")


# ── the field layouts ────────────────────────────────────────────────────────

def test_heading_deviation_and_variation():
    out = decode_n2k(127250, REAL[127250])
    assert out["Heading"] == (1.9844, "rad")
    assert out["Deviation"] == (0.0, "rad")
    assert out["Variation"][0] == pytest.approx(-0.1553, abs=1e-4)
    # 1.9844 rad is 113.7 degrees. The variation is 8.9 degrees west, which is
    # the published value for the position the next test decodes, from a
    # different message on the same bus. Two layouts agreeing on an external
    # fact is the check; either one alone is only self-consistent.


def test_position_decodes_to_where_the_boat_was():
    out = decode_n2k(129025, REAL[129025])
    assert out["Latitude"][0] == pytest.approx(42.661, abs=1e-3)
    assert out["Longitude"][0] == pytest.approx(-81.2128, abs=1e-3)
    # Lake Erie, off the Ontario shore.


def test_a_signed_field_goes_negative():
    out = decode_n2k(127251, REAL[127251])
    assert out["Rate of Turn"][0] == pytest.approx(-0.000105, abs=1e-6)


def test_wind_speed_and_angle():
    out = decode_n2k(130306, REAL[130306])
    assert out["Wind Speed"] == (0.77, "m/s")
    assert out["Wind Angle"][0] == pytest.approx(1.6911, abs=1e-4)


def test_speed_over_ground_is_zero_because_the_boat_was_moored():
    out = decode_n2k(129026, REAL[129026])
    assert out["Speed Over Ground"] == (0.0, "m/s")


def test_temperature_comes_back_in_celsius_not_kelvin():
    """The wire carries hundredths of a kelvin; 292.85 K is not a temperature
    anyone wants to read off a screen."""
    out = decode_n2k(130311, REAL[130311])
    assert out["Temperature"][0] == pytest.approx(19.70, abs=0.01)


def test_the_unavailable_marker_is_not_reported_as_a_value():
    """All ones means the sender has nothing, not 6553.5 metres per second."""
    out = decode_n2k(130306, frame(0x44, 0xFF, 0xFF, 0x0F, 0x42))
    assert "Wind Speed" not in out
    assert "Wind Angle" in out


def test_a_short_frame_does_not_raise():
    assert decode_n2k(129025, b"\x01\x02") == {}


def test_an_unknown_pgn_decodes_to_nothing_rather_than_guessing():
    assert decode_n2k(130821, frame(1, 2, 3, 4, 5, 6, 7, 8)) == {}


# ── fast packet ──────────────────────────────────────────────────────────────

def test_fast_packet_messages_are_named_but_not_decoded():
    """Reading one frame of a multi-frame message gives confident nonsense.

    PGN 129029 decoded that way dated a recent recording to 2002.
    """
    gnss = parse_j1939_id(0x0DF80523)
    assert gnss["pgn"] == 129029
    assert gnss["pgn_name"] == "GNSS Position Data"
    assert gnss["single_frame"] is False
    assert decode_n2k(129029, frame(*range(8))) == {}

    heading = parse_j1939_id(0x09F11223)
    assert heading["single_frame"] is True


# ── the scan over a whole capture ────────────────────────────────────────────

def test_a_scan_reports_the_protocol_per_message():
    pd = pytest.importorskip("pandas")
    rows = []
    for can_id in ("9F11223", "9FD0223", "0CF00400"):
        rows.append({"ID": can_id, **{f"B{i}": 0 for i in range(8)}})
    hits = {h["id_hex"]: h for h in scan_for_j1939(pd.DataFrame(rows))}
    assert hits["9F11223"]["protocol"] == "NMEA 2000"
    assert hits["9FD0223"]["pgn_name"] == "Wind Data"
    assert hits["0CF00400"]["protocol"] == "J1939"
