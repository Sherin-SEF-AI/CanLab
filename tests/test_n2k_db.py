"""NMEA 2000 beyond the hand-written decoders: the table distilled from canboat.

The first tests are the ones that matter. The hand-written decoders were
checked against a real marine recording; canboat is an independent source
built from years of other people's buses. On the frames below, copied from
that recording, the two must agree on every value they share. The rest pin
the decoder's handling of the codes NMEA 2000 reserves.
"""
import json

import pytest

from canlab.core import n2k_db
from canlab.core.j1939 import decode_n2k, decode_pgn, parse_j1939_id, pgn_name
from canlab.core.multiframe import FAST_PACKET_PGNS, Reassembler
from tests.test_multiframe import GNSS_FRAMES, GNSS_ID
from tests.test_nmea2000 import REAL

#: hand-written name -> canboat name, where they differ
ALIAS = {"Rate of Turn": "Rate", "Course Over Ground": "COG", "Speed Over Ground": "SOG"}


@pytest.mark.parametrize("pgn", sorted(REAL))
def test_canboat_agrees_with_the_hand_written_decoder_on_real_frames(pgn):
    hand, table = decode_n2k(pgn, REAL[pgn]), n2k_db.decode(pgn, REAL[pgn])
    shared = 0
    for name, (value, unit) in hand.items():
        other = table.get(ALIAS.get(name, name))
        if other is None:
            continue
        shared += 1
        assert other[0] == pytest.approx(value, rel=1e-6, abs=1e-9), name
        assert other[1] == unit, name
    assert shared >= 1


def test_the_gnss_fix_agrees_after_reassembly():
    r = Reassembler()
    msgs = []
    for i, f in enumerate(GNSS_FRAMES):
        msgs += r.update(GNSS_ID, f, 58.0 + 0.003 * i)
    (m,) = msgs
    hand, table = decode_n2k(129029, m.data), n2k_db.decode(129029, m.data)
    for name in ("Latitude", "Longitude", "Altitude", "Time", "HDOP", "PDOP"):
        assert table[name][0] == pytest.approx(hand[name][0], rel=1e-9), name
    assert table["Date"][0] == hand["Date"][0] == "2021-03-25"
    assert table["Number of SVs"][0] == hand["Satellites Used"][0]


def test_a_pgn_without_a_hand_written_decoder_decodes_from_the_table():
    out = decode_pgn(127488, bytes([0x00, 0x40, 0x1F, 0xFF, 0xFF, 0x7F, 0xFF, 0xFF]))
    assert out["Speed"] == (2000.0, "rpm")
    assert "Boost Pressure" not in out                 # 0xFFFF: not available
    assert "Tilt/Trim" not in out                      # 0x7F: signed not available
    assert pgn_name(127488) == "Engine Parameters, Rapid Update"


def test_error_and_reserved_codes_are_not_readings():
    out = n2k_db.decode(127488, bytes([0xFD, 0xFE, 0xFF, 0xFD, 0xFF, 0x7E, 0xFF, 0xFF]))
    assert out["Speed"] == ("error", "")               # 0xFFFE
    assert "Boost Pressure" not in out                 # 0xFFFD: above the valid range
    assert out["Tilt/Trim"] == ("error", "")           # 0x7E: signed error
    assert "Instance" not in out                       # 253: reserved


def test_a_fast_packet_is_decoded_only_once_reassembled():
    payload = bytearray(26)
    payload[0] = 0                                     # instance
    payload[1:3] = (3000).to_bytes(2, "little")        # oil pressure, 100 Pa/bit
    payload[3:5] = (3631).to_bytes(2, "little")        # 363.1 K, 0.1 K/bit
    payload[5:7] = b"\xff\xff"                         # temperature: unsigned n/a
    payload[7:9] = (1410).to_bytes(2, "little")        # alternator, 0.01 V
    payload[9:26] = b"\xff" * 17
    payload[9:11] = b"\xff\x7f"                        # fuel rate: signed n/a is 0x7FFF
    assert 127489 in FAST_PACKET_PGNS
    assert decode_pgn(127489, bytes(payload[:8]), reassembled=False) == {}
    out = decode_pgn(127489, bytes(payload), reassembled=True)
    assert out["Oil pressure"] == (300000.0, "Pa")
    assert out["Oil temperature"][0] == pytest.approx(89.95, abs=1e-9)
    assert out["Oil temperature"][1] == "°C"          # kelvin converted
    assert out["Alternator Potential"] == (14.1, "V")
    assert "Fuel Rate" not in out and "Temperature" not in out
    minus_one = bytearray(payload)
    minus_one[9:11] = b"\xff\xff"                      # signed 0xFFFF is -1, a reading
    assert decode_pgn(127489, bytes(minus_one), reassembled=True)["Fuel Rate"] == (-0.1, "L/h")


def test_the_reassembler_handles_a_fast_packet_the_hand_table_does_not_know():
    """127489 was not in the hand-written table, so its frames were left alone."""
    payload = bytes([0, 0xB8, 0x0B, 0x2F, 0x0E] + [0xFF] * 21)          # 26 bytes
    frames = [bytes([0x20, len(payload)]) + payload[:6]]
    rest, counter = payload[6:], 1
    while rest:
        frames.append(bytes([0x20 | counter]) + rest[:7].ljust(7, b"\xff"))
        rest, counter = rest[7:], counter + 1
    r = Reassembler()
    arb = 0x09F20123                                   # PGN 127489 from 0x23
    msgs = []
    for i, f in enumerate(frames):
        msgs += r.update(arb, f, 1.0 + 0.001 * i)
    (m,) = msgs
    assert m.pgn == 127489 and m.data == payload
    assert decode_pgn(m.pgn, m.data, reassembled=True)["Oil pressure"] == (300000.0, "Pa")


def test_a_repeating_group_is_not_decoded_as_one_value():
    """129540 lists one entry per satellite. The table stops before the
    group, so no single satellite's PRN is presented as the PRN."""
    fields = [f[0] for f in n2k_db.definition(129540)["fields"]]
    assert "Sats in View" in fields and "PRN" not in fields and "SNR" not in fields


def test_proprietary_pgns_are_named_not_guessed():
    assert n2k_db.definition(130821) is None
    assert pgn_name(130821) == "Proprietary fast packet (manufacturer defined)"
    assert parse_j1939_id(0x18FF0523)["protocol"] == "J1939"         # PropB, data page 0


def test_the_table_ships_with_its_licence():
    data = json.loads(n2k_db.DATA.read_text())
    assert len(data["pgns"]) >= 200 and data["source"].startswith("canboat")
    notice = (n2k_db.DATA.parent / "CANBOAT-NOTICE.txt").read_text()
    assert "Apache License, Version 2.0" in notice and "Kees Verruijt" in notice
