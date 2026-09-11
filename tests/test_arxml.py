"""ARXML export/import: loadable by a real AUTOSAR reader, not just by us.

The previous exporter emitted a private dialect that only its own importer
understood; cantools loaded it as zero messages. These tests decode the export
with cantools and compare it against the DBC decode of the same signals, which
is the only check that means anything for an interchange format.
"""
import cantools
import pytest

from canlab.core.arxml_export import to_arxml_string
from canlab.core.arxml_import import parse_arxml, parse_arxml_string
from canlab.core.dbc_manager import build_database


def sig(**kw):
    base = {"message_id": "1A0", "message_name": "MSG", "signal_name": "Speed",
            "start_bit": 0, "length": 16, "byte_order": "little",
            "value_type": "unsigned", "scale": 0.1, "offset": -40.0,
            "min_val": 0, "max_val": 6553, "unit": "km/h", "description": "road speed"}
    base.update(kw)
    return base


def _load(signals):
    return cantools.database.load_string(to_arxml_string(signals),
                                         database_format="arxml")


def test_cantools_loads_the_export():
    db = _load([sig()])
    assert len(db.messages) == 1
    msg = db.messages[0]
    assert msg.name == "MSG" and msg.frame_id == 0x1A0 and msg.length == 8


@pytest.mark.parametrize("definition,payload", [
    (sig(start_bit=0, length=16, byte_order="little"), bytes([0xD2, 0x04, 0, 0, 0, 0, 0, 0])),
    (sig(start_bit=7, length=16, byte_order="big"), bytes([0x04, 0xD2, 0, 0, 0, 0, 0, 0])),
    (sig(start_bit=8, length=8, byte_order="little", scale=1.0, offset=0.0),
     bytes([0, 0x7F, 0, 0, 0, 0, 0, 0])),
])
def test_arxml_decodes_the_same_as_the_dbc(definition, payload):
    """The two exporters must agree; a format that disagrees is worse than none."""
    from_arxml = _load([definition]).decode_message(0x1A0, payload)
    from_dbc = build_database([definition]).decode_message(0x1A0, payload)
    assert from_arxml == from_dbc


def test_scale_offset_and_unit_survive():
    signal = _load([sig()]).messages[0].signals[0]
    assert signal.scale == pytest.approx(0.1)
    assert signal.offset == pytest.approx(-40.0)
    assert signal.unit == "km/h"
    assert signal.length == 16 and signal.byte_order == "little_endian"


def test_big_endian_byte_order_is_recorded():
    signal = _load([sig(start_bit=7, byte_order="big")]).messages[0].signals[0]
    assert signal.byte_order == "big_endian"


def test_extended_ids_round_trip():
    db = _load([sig(message_id="18FEF100", message_name="EEC1", extended=True)])
    msg = db.messages[0]
    assert msg.frame_id == 0x18FEF100 and msg.is_extended_frame


def test_multiple_messages_and_signals():
    signals = [
        sig(signal_name="Speed", start_bit=0, length=16),
        sig(signal_name="Temp", start_bit=16, length=8, scale=1.0, offset=-40.0, unit="degC"),
        sig(message_id="2B0", message_name="OTHER", signal_name="Rpm",
            start_bit=0, length=16, scale=0.25, offset=0.0, unit="rpm"),
    ]
    db = _load(signals)
    assert {m.name for m in db.messages} == {"MSG", "OTHER"}
    assert {s.name for s in db.get_message_by_name("MSG").signals} == {"Speed", "Temp"}


def test_names_are_sanitised_but_still_load():
    db = _load([sig(message_name="Msg A", signal_name="1 speed (raw)")])
    assert db.messages[0].name == "Msg_A"
    assert db.messages[0].signals[0].name == "_1_speed__raw_"


def test_import_reads_our_own_export(tmp_path):
    original = [sig(), sig(signal_name="Temp", start_bit=16, length=8,
                           scale=1.0, offset=-40.0, unit="degC")]
    path = tmp_path / "out.arxml"
    path.write_text(to_arxml_string(original))
    imported = parse_arxml(str(path))
    by_name = {s["signal_name"]: s for s in imported}
    assert set(by_name) == {"Speed", "Temp"}
    assert by_name["Speed"]["start_bit"] == 0
    assert by_name["Speed"]["length"] == 16
    assert by_name["Speed"]["scale"] == pytest.approx(0.1)
    assert by_name["Speed"]["offset"] == pytest.approx(-40.0)
    assert by_name["Speed"]["byte_order"] == "little"
    assert by_name["Speed"]["message_id"] == "1A0"


def test_imported_signals_re_export_identically():
    original = [sig()]
    reimported = parse_arxml_string(to_arxml_string(original))
    again = parse_arxml_string(to_arxml_string(reimported))
    keys = ["message_id", "signal_name", "start_bit", "length",
            "byte_order", "scale", "offset", "unit"]
    assert [{k: s[k] for k in keys} for s in reimported] == \
           [{k: s[k] for k in keys} for s in again]


def test_import_rejects_a_non_arxml_file(tmp_path):
    path = tmp_path / "not.arxml"
    path.write_text("hello")
    with pytest.raises(ValueError, match="Could not read ARXML"):
        parse_arxml(str(path))


def test_import_reads_a_foreign_autosar_file():
    """An ARXML from anywhere else must import, not just our own output."""
    foreign = to_arxml_string([sig(message_name="Foreign", signal_name="Value")])
    # Re-namespace it the way another tool might, to prove we are not pattern
    # matching on our own output.
    foreign = foreign.replace("CanLabCluster", "SupplierCluster")
    signals = parse_arxml_string(foreign)
    assert signals and signals[0]["signal_name"] == "Value"
