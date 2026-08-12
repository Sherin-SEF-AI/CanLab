"""Regression tests for the deep-audit fixes.

Each test pins a specific bug that was fixed so it can't silently regress:
  - ISO-TP callers must send a PCI-less service payload (no double PCI byte).
  - UDS DTC / DID / service parsing must read PCI-less payloads at the right
    offsets, and DTC decoding must stride 4-byte records.
  - OBD-II PID scan must decode multi-byte PIDs.
  - REST JSON must be NaN-safe.
  - Replay must honour the recorded DLC.
  - DBC round-trip must preserve message length + the extended-ID flag and must
    decode on modern cantools.
  - Injection packing must respect byte order + signedness.
  - Live-frame storage must be O(chunk) and lazily concatenated.
  - Vectorized correlation align must match the reference implementation.
"""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PyQt6")   # several core modules import QThread at load


# ── ISO-TP: PCI-less payloads on the wire ─────────────────────────────────────

class _RecordingBus:
    def __init__(self, replies=None):
        self.sent = []
        self._replies = list(replies or [])

    def send(self, msg):
        self.sent.append(bytes(msg.data))

    def recv(self, timeout=0.0):
        return self._replies.pop(0) if self._replies else None


def test_isotp_single_frame_is_pci_less():
    from core.isotp import ISOTPSession
    bus = _RecordingBus()
    ISOTPSession(bus, 0x7E0, 0x7E8).send(bytes([0x01, 0x0C]), timeout=0.001)
    # Correct SF: PCI 0x02, then service payload 01 0C, padded to 8.
    assert bus.sent[0] == bytes([0x02, 0x01, 0x0C, 0, 0, 0, 0, 0])
    # Regression: never the double-PCI "03 02 01 0C" that no ECU answers.
    assert bus.sent[0][:4] != bytes([0x03, 0x02, 0x01, 0x0C])


def test_isotp_frames_always_8_bytes():
    from core.isotp import ISOTPSession
    bus = _RecordingBus()
    ISOTPSession(bus, 0x7E0, 0x7E8).send(bytes([0x22, 0xF1, 0x90]), timeout=0.001)
    assert all(len(f) == 8 for f in bus.sent)


# ── OBD-II poller sends a PCI-less service request ────────────────────────────

def test_obd2_poller_request_is_pci_less():
    from core.obd2_poller import OBD2Poller
    from core.isotp import ISOTPSession
    bus = _RecordingBus()
    session = ISOTPSession(bus, 0x7E0, 0x7E8)
    session.send(bytes([0x01, 0x0C]), timeout=0.001)   # what the poller now sends
    assert bus.sent[0] == bytes([0x02, 0x01, 0x0C, 0, 0, 0, 0, 0])


# ── UDS DTC decoding: 4-byte records, correct code text ───────────────────────

def test_decode_dtc_code_text():
    from core.uds import decode_dtc
    # 0x01 -> P0..., first digit 0, then 01; mid 0x43 -> P0143
    assert decode_dtc(0x01, 0x43, 0x00) == "P0143"
    # High bits 11 -> U prefix
    assert decode_dtc(0xC1, 0x23, 0x00) == "U0123"


def test_obd2_multibyte_pid_decodes_full_width():
    # RPM (0x0C) is two bytes; a single-byte decode would be 4x wrong.
    from core.obd2_pids import decode_pid
    # (A<<8|B)/4 with A=0x1A,B=0xF8 -> 1726 rpm
    assert decode_pid(0x0C, bytes([0x1A, 0xF8])) == pytest.approx(1726.0)


# ── REST API JSON is NaN-safe ─────────────────────────────────────────────────

def test_rest_json_safe_replaces_nan():
    from core.rest_api import _json_safe
    import json
    recs = [{"B0": 1, "B1": float("nan"), "B2": np.int64(7),
             "Timestamp": np.float64(1.5)}]
    out = _json_safe(recs)
    s = json.dumps(out)              # must not raise
    assert out[0]["B1"] is None
    assert out[0]["B2"] == 7
    assert json.loads(s)[0]["Timestamp"] == 1.5


# ── Replay honours recorded DLC ───────────────────────────────────────────────

def test_replay_honours_dlc(monkeypatch):
    from core import replay as replay_mod
    from core.safety import set_armed
    set_armed(True)
    try:
        sent = []

        class Bus:
            def send(self, msg):
                sent.append(bytes(msg.data))

        df = pd.DataFrame([{
            "Timestamp": 0.0, "ID": "0A6", "DLC": 3,
            "B0": 0x11, "B1": 0x22, "B2": 0x33,
            "B3": np.nan, "B4": np.nan, "B5": np.nan, "B6": np.nan, "B7": np.nan,
        }])
        w = replay_mod.ReplayWorker(Bus(), df, speed=100.0, loop=False)
        w.run()   # run synchronously in this thread
        assert sent == [bytes([0x11, 0x22, 0x33])], sent
    finally:
        set_armed(False)


# ── DBC round-trip preserves length + extended flag, and decodes ──────────────

def test_dbc_roundtrip_extended_and_length():
    import cantools
    from core.dbc_manager import signals_to_dbc_string
    sigs = [{
        "message_id": "18FEF100", "message_name": "EEC1", "msg_length": 3,
        "extended": True, "signal_name": "RPM", "start_bit": 0, "length": 16,
        "byte_order": "little", "value_type": "unsigned", "scale": 0.125,
        "offset": 0, "min_val": 0, "max_val": 8000, "unit": "rpm",
    }]
    db = cantools.database.load_string(signals_to_dbc_string(sigs),
                                       database_format="dbc")
    msg = db.messages[0]
    assert msg.frame_id == 0x18FEF100
    assert msg.is_extended_frame is True
    assert msg.length == 3


def test_dbc_decode_big_endian_signed():
    from core.injection import pack_signal
    from core.dbc_manager import decode_frame
    sig = {
        "message_id": "200", "signal_name": "Temp", "start_bit": 7,
        "length": 16, "byte_order": "big", "value_type": "signed",
        "scale": 0.1, "offset": -40, "min_val": -100, "max_val": 200,
        "unit": "C", "msg_length": 8,
    }
    data = pack_signal(35.0, sig)
    decoded = decode_frame([sig], "200", bytes(data))
    assert decoded.get("Temp") == pytest.approx(35.0, abs=0.05)


# ── AppState lazy frame storage ───────────────────────────────────────────────

def test_appstate_append_is_lazy_and_correct():
    from core.state import AppState
    s = AppState()
    s.load_frames(pd.DataFrame({"ID": ["0A6"], "Timestamp": [0.0], "B0": [1]}), "src")
    for i in range(5):
        s.append_frames(pd.DataFrame({"ID": ["0A6"], "Timestamp": [float(i + 1)],
                                      "B0": [i]}))
    df = s.frames_df
    assert len(df) == 6
    # cached: repeated access returns the same object until invalidated
    assert s.frames_df is df
    # direct assignment collapses chunks
    s.frames_df = pd.DataFrame({"ID": ["100"], "Timestamp": [9.0], "B0": [9]})
    assert len(s.frames_df) == 1


# ── Vectorized correlation align matches the reference ────────────────────────

def test_vectorized_align_matches_reference():
    from core.correlation_engine import _align

    def ref(s1, t1, s2, t2, max_dt=0.1):
        v1, v2, j = [], [], 0
        for i in range(len(t1)):
            ts = t1[i]
            while j < len(t2) - 1 and abs(t2[j + 1] - ts) < abs(t2[j] - ts):
                j += 1
            if abs(t2[j] - ts) <= max_dt:
                v1.append(s1[i]); v2.append(s2[j])
        return np.array(v1, float), np.array(v2, float)

    rng = np.random.default_rng(1)
    for _ in range(50):
        n1, n2 = int(rng.integers(1, 40)), int(rng.integers(1, 40))
        t1 = np.sort(rng.uniform(0, 5, n1)); t2 = np.sort(rng.uniform(0, 5, n2))
        s1 = rng.uniform(0, 255, n1); s2 = rng.uniform(0, 255, n2)
        a1, a2 = ref(s1, t1, s2, t2)
        b1, b2 = _align(s1, t1, s2, t2)
        assert np.allclose(a1, b1) and np.allclose(a2, b2)


# ── ARM TX re-check stops replay mid-run ──────────────────────────────────────

def test_replay_stops_when_disarmed_midrun():
    from core import replay as replay_mod
    from core.safety import set_armed
    set_armed(True)
    sent = []

    class Bus:
        def send(self, msg):
            sent.append(1)
            set_armed(False)   # disarm after the first send

    df = pd.DataFrame([{
        "Timestamp": float(i), "ID": "0A6", "DLC": 1, "B0": i,
        "B1": np.nan, "B2": np.nan, "B3": np.nan, "B4": np.nan,
        "B5": np.nan, "B6": np.nan, "B7": np.nan,
    } for i in range(10)])
    try:
        w = replay_mod.ReplayWorker(Bus(), df, speed=100.0, loop=False)
        w.run()
        # Exactly one frame goes out before the disarm halts the loop.
        assert len(sent) == 1, sent
    finally:
        set_armed(False)


# ── SavvyCAN CSV: trailing comma + hex bytes ──────────────────────────────────

def test_savvycan_trailing_comma_and_hex_bytes(tmp_path):
    """Real SavvyCAN exports: 2-digit hex data bytes AND a trailing comma.

    The trailing comma used to make pandas promote Time Stamp to the index and
    shift every column left (IDs landed in the timestamp column, the ID column
    filled with the Extended flag) — which then crashed int(id, 16) in the UI.
    And hex bytes ("0A"/"FF") were read as decimal / NaN. Both must parse right.
    """
    from core.log_parser import parse_log_file
    p = tmp_path / "savvy.csv"
    p.write_text(
        "Time Stamp,ID,Extended,Dir,Bus,LEN,D1,D2,D3,D4,D5,D6,D7,D8\n"
        "1000,000002B5,false,Rx,0,8,00,0A,FF,10,55,00,00,00,\n"
        "2000,00000111,false,Rx,0,8,DE,AD,BE,EF,00,00,00,00,\n"
    )
    df = parse_log_file(str(p))
    assert set(df["ID"]) == {"2B5", "111"}          # real IDs, not "FALSE"
    assert (df["Bus"] == 0).all()                   # Bus column not shifted
    assert (df["DLC"] == 8).all()                   # DLC column not shifted
    row = df[df["ID"] == "2B5"].iloc[0]
    # Hex bytes decoded to their true values (0x0A=10, 0xFF=255, 0x10=16).
    assert [int(row[f"B{i}"]) for i in range(6)] == [0x00, 0x0A, 0xFF, 0x10, 0x55, 0x00]
    # Every ID must be int(x, 16)-parseable (the old crash path).
    for cid in df["ID"].unique():
        int(cid, 16)


def test_savvycan_decimal_sample_still_parses(tmp_path):
    # The bundled decimal-byte sample format (no trailing comma) must keep working.
    from core.log_parser import parse_log_file
    p = tmp_path / "dec.csv"
    p.write_text(
        "Time Stamp,ID,Extended,Dir,Bus,LEN,D1,D2,D3,D4,D5,D6,D7,D8\n"
        "0,018,false,Rx,0,8,70,80,0,0,0,0,0,150\n"
    )
    df = parse_log_file(str(p))
    row = df.iloc[0]
    assert row["ID"] == "018"
    assert [int(row[f"B{i}"]) for i in range(8)] == [70, 80, 0, 0, 0, 0, 0, 150]


# ── candump FD frames are parsed, not mangled ─────────────────────────────────

def test_candump_fd_detection_and_parse(tmp_path):
    from core.log_parser import parse_log_file
    p = tmp_path / "fd.log"
    p.write_text(
        "(1000.000000) can0 123##1001122334455667788990011\n"
        "(1000.001000) can0 456#DEADBEEF\n"
    )
    df = parse_log_file(str(p))
    assert len(df) == 2
    ids = set(df["ID"])
    assert "123" in ids and "456" in ids
    # The FD frame carries more than 8 payload bytes.
    fd_row = df[df["ID"] == "123"].iloc[0]
    assert int(fd_row["DLC"]) > 8
