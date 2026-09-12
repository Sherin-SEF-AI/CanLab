"""Every exporter, against a 29-bit message.

A DBC writes a 29-bit frame id as ``id | 0x80000000``; without that flag the
number is not a valid standard id and cantools refuses the whole file. Only
one of the five exporters had a test that used an extended id, and the
openpilot one was writing the bare number, so any J1939 or 29-bit capture
exported an unloadable file. Real 29-bit logs found it; this pins it.

The openpilot and CANdb++ exporters had no unit coverage at all before this.
"""
import cantools
import pytest

from canlab.core.arxml_export import to_arxml_string
from canlab.core.candbpp_export import to_candbpp_string
from canlab.core.dbc_manager import dbc_frame_id, signals_to_dbc_string
from canlab.core.lua_exporter import signals_to_lua_dissector
from canlab.core.openpilot_export import to_opendbc_string

# A J1939 PGN as the CANedge logs actually carry it, plus an 11-bit message
# in the same file so the exporters have to tell them apart.
EXT_ID = "9F11202"
STD_ID = "1A0"

SIGNALS = [
    {"message_id": EXT_ID, "message_name": "J1939_MSG", "signal_name": "ENGINE_SPEED",
     "start_bit": 24, "length": 16, "byte_order": "little", "value_type": "unsigned",
     "scale": 0.125, "offset": 0.0, "min_val": 0, "max_val": 8031.875,
     "unit": "rpm", "description": "engine speed"},
    {"message_id": EXT_ID, "message_name": "J1939_MSG", "signal_name": "TORQUE",
     "start_bit": 8, "length": 8, "byte_order": "little", "value_type": "signed",
     "scale": 1.0, "offset": -125.0, "min_val": -125, "max_val": 130,
     "unit": "%", "description": ""},
    {"message_id": STD_ID, "message_name": "BODY_MSG", "signal_name": "SPEED",
     "start_bit": 0, "length": 16, "byte_order": "big", "value_type": "unsigned",
     "scale": 0.01, "offset": 0.0, "min_val": 0, "max_val": 655.35,
     "unit": "km/h", "description": ""},
]


def test_dbc_frame_id_sets_the_flag_above_11_bits():
    assert dbc_frame_id({"message_id": EXT_ID}) == 0x9F11202 | 0x80000000
    assert dbc_frame_id({"message_id": STD_ID}) == 0x1A0
    assert dbc_frame_id({"message_id": "7FF"}) == 0x7FF          # the last standard id
    assert dbc_frame_id({"message_id": "800"}) == 0x800 | 0x80000000
    # An explicit flag wins even for a small id, which is legal on CAN.
    assert dbc_frame_id({"message_id": "1A0", "extended": True}) == 0x1A0 | 0x80000000


@pytest.mark.parametrize("name,writer", [
    ("dbc", signals_to_dbc_string),
    ("openpilot", to_opendbc_string),
    ("candbpp", to_candbpp_string),
])
def test_dbc_writers_produce_a_loadable_file_for_29_bit_ids(name, writer, tmp_path):
    path = tmp_path / f"{name}.dbc"
    path.write_text(writer(SIGNALS))
    db = cantools.database.load_file(str(path))       # raises if the id is wrong

    ext = db.get_message_by_frame_id(0x9F11202)
    std = db.get_message_by_frame_id(0x1A0)
    assert ext.is_extended_frame, f"{name}: the 29-bit message lost its flag"
    assert not std.is_extended_frame, f"{name}: an 11-bit message became extended"
    assert {s.name for s in ext.signals} == {"ENGINE_SPEED", "TORQUE"}
    assert {s.name for s in std.signals} == {"SPEED"}

    # The definition has to survive, not merely load.
    decoded = ext.decode(bytes([0x00, 0x9B, 0x00, 0x10, 0x27, 0x00, 0x00, 0x00]))
    assert decoded["ENGINE_SPEED"] == pytest.approx(0x2710 * 0.125)
    assert decoded["TORQUE"] == pytest.approx(0x9B - 125 - 256)     # signed


def test_arxml_carries_the_addressing_mode(tmp_path):
    path = tmp_path / "out.arxml"
    path.write_text(to_arxml_string(SIGNALS))
    db = cantools.database.load_file(str(path))
    ext = db.get_message_by_frame_id(0x9F11202)
    assert ext.is_extended_frame
    assert not db.get_message_by_frame_id(0x1A0).is_extended_frame


def test_lua_dissector_matches_a_29_bit_id():
    text = signals_to_lua_dissector(SIGNALS)
    # The dissector compares against can.id, which carries the bare id; the
    # 0x80000000 flag is a DBC storage convention and must not leak in here.
    assert "can_id == 0x9F11202" in text
    assert "0x89F11202" not in text
    assert "can_id == 0x1A0" in text


def test_no_dbc_writer_emits_a_bare_29_bit_id():
    """The failure mode in one line: a BO_ whose number is neither a valid
    standard id nor flagged extended."""
    for writer in (signals_to_dbc_string, to_opendbc_string, to_candbpp_string):
        for line in writer(SIGNALS).splitlines():
            if not line.startswith("BO_ "):
                continue
            fid = int(line.split()[1])
            assert fid <= 0x7FF or fid & 0x80000000, \
                f"{writer.__name__} wrote an unloadable BO_ id: {line}"
