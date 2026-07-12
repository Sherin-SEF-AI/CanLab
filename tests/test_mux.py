"""Tests for multiplexer detection (#5) and DBC mux emission."""
import numpy as np
import pandas as pd
import pytest

from core.mux_detector import detect_multiplexer


def _muxed_frames(n=400):
    """Byte 0 selects the mode: mode 0 varies bytes 1-2, mode 1 varies bytes 3-4."""
    rng = np.random.default_rng(0)
    rows = []
    for i in range(n):
        sel = i % 2
        b = [0] * 8
        b[0] = sel
        if sel == 0:
            b[1] = int(rng.integers(0, 256)); b[2] = int(rng.integers(0, 256))
        else:
            b[3] = int(rng.integers(0, 256)); b[4] = int(rng.integers(0, 256))
        rows.append({"Timestamp": i * 0.01, "ID": "300", "Bus": 0, "DLC": 8,
                     **{f"B{k}": b[k] for k in range(8)}})
    return pd.DataFrame(rows)


def test_detects_multiplexor_byte():
    res = detect_multiplexer(_muxed_frames())
    assert res is not None
    assert res["mux_byte"] == 0
    assert set(res["modes"].keys()) == {0, 1}
    # mode 0 should activate bytes 1,2; mode 1 bytes 3,4
    assert set(res["modes"][0]) == {1, 2}
    assert set(res["modes"][1]) == {3, 4}


def test_non_muxed_returns_none():
    # Every byte varies independently — no clean selector.
    rng = np.random.default_rng(1)
    rows = [{"Timestamp": i * 0.01, "ID": "301", "Bus": 0, "DLC": 8,
             **{f"B{k}": int(rng.integers(0, 256)) for k in range(8)}}
            for i in range(300)]
    assert detect_multiplexer(pd.DataFrame(rows)) is None


def test_dbc_emits_mux_tokens():
    pytest.importorskip("cantools")
    from core.dbc_manager import signals_to_dbc_string
    sigs = [
        {"message_id": "300", "message_name": "MUXED", "msg_length": 8,
         "signal_name": "Mode", "start_bit": 0, "length": 8, "mux_role": "M"},
        {"message_id": "300", "message_name": "MUXED", "msg_length": 8,
         "signal_name": "SigA", "start_bit": 8, "length": 16, "mux_value": 0},
        {"message_id": "300", "message_name": "MUXED", "msg_length": 8,
         "signal_name": "SigB", "start_bit": 24, "length": 16, "mux_value": 1},
    ]
    dbc = signals_to_dbc_string(sigs)
    assert " SG_ Mode M :" in dbc
    assert " SG_ SigA m0 :" in dbc
    assert " SG_ SigB m1 :" in dbc
    import cantools
    db = cantools.database.load_string(dbc, database_format="dbc")   # must parse
    assert db.get_message_by_name("MUXED") is not None
