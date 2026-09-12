"""DBC core: signal dicts -> DBC text -> cantools -> decode/encode, round trips."""
import cantools
import pandas as pd
import pytest

from canlab.core.dbc_manager import (
    build_database, decode_frame, decode_series, encode_frame, load_dbc,
    signals_to_dbc_string, validate_signals, dbc_identifier, norm_signal,
)


def sig(**kw):
    base = {"message_id": "1A0", "message_name": "MSG", "signal_name": "Speed",
            "start_bit": 0, "length": 16, "byte_order": "little",
            "value_type": "unsigned", "scale": 0.1, "offset": 0.0,
            "min_val": 0, "max_val": 6553.5, "unit": "km/h", "description": ""}
    base.update(kw)
    return base


def test_little_endian_encode_decode_roundtrip():
    s = sig()
    payload = encode_frame([s], "1A0", {"Speed": 123.4})
    assert payload[:2] == bytes([0xD2, 0x04])          # raw 1234 little-endian
    assert decode_frame([s], "1A0", payload)["Speed"] == pytest.approx(123.4)


def test_big_endian_non_byte_aligned_matches_cantools():
    s = sig(start_bit=4, length=12, byte_order="big", value_type="signed",
            scale=0.25, offset=-10)
    db = cantools.database.load_string(signals_to_dbc_string([s]), database_format="dbc")
    msg = db.get_message_by_frame_id(0x1A0)
    assert msg.signals[0].byte_order == "big_endian" and msg.signals[0].is_signed
    raw = bytes([0x0A, 0xBC, 0, 0, 0, 0, 0, 0])
    assert decode_frame([s], "1A0", raw)["Speed"] == db.decode_message(0x1A0, raw)["Speed"]
    for value in (-10.0, 0.0, 100.25, -300.5):
        assert decode_frame([s], "1A0", encode_frame([s], "1A0", {"Speed": value}))["Speed"] == value


def test_signed_negative_value():
    s = sig(start_bit=8, length=8, value_type="signed", scale=1, offset=0)
    payload = encode_frame([s], "1A0", {"Speed": -50})
    assert payload[1] == (-50) & 0xFF
    assert decode_frame([s], "1A0", payload)["Speed"] == -50


def test_extended_id_gets_dbc_flag_and_round_trips(tmp_path):
    s = sig(message_id="18FEF100", message_name="EEC1")
    text = signals_to_dbc_string([s])
    assert f"BO_ {0x18FEF100 | 0x80000000} EEC1:" in text
    db = build_database([s])
    assert db.get_message_by_name("EEC1").is_extended_frame
    payload = encode_frame([s], "18FEF100", {"Speed": 5.0})
    assert decode_frame([s], "18FEF100", payload)["Speed"] == pytest.approx(5.0)
    path = tmp_path / "x.dbc"
    path.write_text(text)
    back = load_dbc(str(path))
    assert back[0]["message_id"] == "18FEF100" and back[0]["extended"] is True


def test_mux_tokens_and_value_table_round_trip(tmp_path):
    sigs = [
        sig(message_id="300", message_name="MUXED", signal_name="Mode", length=8, mux_role="M",
            scale=1, value_table={0: "OFF", 1: "ON"}),
        sig(message_id="300", message_name="MUXED", signal_name="SigA", start_bit=8, mux_value=0, scale=1),
        sig(message_id="300", message_name="MUXED", signal_name="SigB", start_bit=24, mux_value=1, scale=1),
    ]
    text = signals_to_dbc_string(sigs)
    assert " SG_ Mode M :" in text and " SG_ SigA m0 :" in text and " SG_ SigB m1 :" in text
    assert 'VAL_ 768 Mode 0 "OFF" 1 "ON" ;' in text
    path = tmp_path / "mux.dbc"
    path.write_text(text)
    back = {d["signal_name"]: d for d in load_dbc(str(path))}
    assert back["Mode"]["mux_role"] == "M" and back["Mode"]["value_table"] == {0: "OFF", 1: "ON"}
    assert back["SigA"]["mux_value"] == 0 and back["SigB"]["mux_value"] == 1
    decoded = decode_frame(sigs, "300", bytes([1, 0, 0, 0x34, 0x12, 0, 0, 0]))
    assert decoded["Mode"] == 1 and decoded["SigB"] == 0x1234 and "SigA" not in decoded


def test_names_and_units_are_sanitised_but_still_load():
    s = sig(message_name="Msg A", signal_name="1 speed (raw)", unit='k"m/h', description='say "hi"')
    db = build_database([s])
    msg = db.messages[0]
    assert msg.name == "Msg_A" and msg.signals[0].name == "_1_speed__raw_"
    assert msg.signals[0].unit == 'k"m/h' and msg.signals[0].comment == 'say "hi"'
    assert dbc_identifier("1 speed (raw)") == "_1_speed__raw_"


def test_duplicate_signal_names_in_one_message_are_disambiguated():
    sigs = [sig(signal_name="X", start_bit=0, length=8), sig(signal_name="X", start_bit=8, length=8)]
    names = [s.name for s in build_database(sigs).messages[0].signals]
    assert names == ["X", "X_2"]


def test_case_insensitive_byte_order_and_value_type():
    n = norm_signal(sig(byte_order="Motorola", value_type="Signed"))
    assert n["byte_order"] == "big" and n["value_type"] == "signed"
    text = signals_to_dbc_string([sig(value_type="Unsigned")])
    assert "@1+" in text


def test_load_export_load_preserves_fields(tmp_path):
    sigs = [sig(msg_length=8), sig(signal_name="Temp", start_bit=16, length=8,
                                   value_type="signed", scale=1, offset=-40, unit="degC")]
    p1 = tmp_path / "a.dbc"
    p1.write_text(signals_to_dbc_string(sigs))
    loaded = load_dbc(str(p1))
    p2 = tmp_path / "b.dbc"
    p2.write_text(signals_to_dbc_string(loaded))
    again = load_dbc(str(p2))
    keys = ["message_id", "signal_name", "start_bit", "length", "byte_order",
            "value_type", "scale", "offset", "unit", "msg_length", "extended"]
    assert [{k: d[k] for k in keys} for d in loaded] == [{k: d[k] for k in keys} for d in again]


def test_validate_reports_overflow_and_bad_length():
    assert validate_signals([sig()]) == []
    errs = validate_signals([sig(start_bit=60, length=8)])
    assert errs and "exceed" in errs[0]
    errs = validate_signals([sig(start_bit=62, length=10, byte_order="big")])
    assert errs and "exceed" in errs[0]
    assert validate_signals([sig(length=0)])
    # a bigger FD message makes room
    assert validate_signals([sig(start_bit=60, length=8, msg_length=16)]) == []


def test_decode_series_and_unknown_id():
    s = sig()
    frames = pd.DataFrame({"Timestamp": [0.0, 0.01, 0.02], "ID": ["1A0"] * 3,
                           "B0": [0x0A, 0x14, 0x1E], "B1": [0, 0, 0],
                           **{f"B{i}": [0, 0, 0] for i in range(2, 8)}})
    out = decode_series([s], "1A0", frames)
    assert list(out.columns) == ["Timestamp", "Speed"]
    assert out["Speed"].tolist() == pytest.approx([1.0, 2.0, 3.0])
    assert decode_frame([s], "7FF", b"\x00" * 8) == {}
    assert decode_series([s], "7FF", frames).empty
