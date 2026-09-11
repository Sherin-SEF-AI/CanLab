"""Parsers for the formats people actually load: SavvyCAN CSV, candump, pcap, the bundled sample."""
import math
import struct
from pathlib import Path

import numpy as np
import pytest

from canlab.core.log_parser import (
    parse_candump_log, parse_log_file, parse_pcap, parse_savvycan_csv,
    CAN_EFF_FLAG, CAN_ERR_FLAG, CAN_RTR_FLAG,
)

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = Path(__file__).resolve().parents[1] / "canlab" / "sample_data" / "sample_kona_drive.csv"


def _nan(v):
    return isinstance(v, float) and math.isnan(v)


def test_savvycan_real_format_hex_bom_crlf():
    df = parse_savvycan_csv(str(FIXTURES / "savvycan_real.csv"))
    assert len(df) == 4
    assert set(df["ID"]) == {"386", "1A0", "18FEF100"}      # digit-only hex id is NOT decimal
    r = df[df["ID"] == "386"].iloc[0]
    assert r["B0"] == 255 and r["B1"] == 0x10 and r["B7"] == 0x7F   # hex bytes, FF is not NaN
    assert r["Timestamp"] == pytest.approx(0.001)                    # microseconds -> seconds
    short = df[df["ID"] == "1A0"].iloc[0]
    assert int(short["DLC"]) == 3 and int(short["Bus"]) == 1
    assert _nan(short["B3"]) and _nan(short["B7"])
    ext = df[df["ID"] == "18FEF100"].iloc[0]
    assert bool(ext["Extended"]) is True and ext["B7"] == 0x88
    assert not df[df["ID"] == "386"]["Extended"].any()
    d = df[df["ID"] == "386"]["Delta"].tolist()
    assert d[0] == 0.0 and d[1] == pytest.approx(0.001)
    assert list(df.columns[:5]) == ["Timestamp", "ID", "Bus", "DLC", "Extended"]


def test_savvycan_seconds_timestamps(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("Time Stamp,ID,Extended,Dir,Bus,LEN,D1,D2,D3,D4,D5,D6,D7,D8\n"
                 "12.5,0A6,false,Rx,0,2,0A,0B,,,,,,\n"
                 "12.75,0A6,false,Rx,0,2,0A,0C,,,,,,\n")
    df = parse_log_file(str(p))
    assert df["Timestamp"].tolist() == [12.5, 12.75]
    assert df["ID"].tolist() == ["0A6", "0A6"] and df["Delta"].tolist()[1] == pytest.approx(0.25)


def test_candump_classic_fd_error_remote():
    df = parse_candump_log(str(FIXTURES / "candump_mixed.log"))
    assert len(df) == 5                                   # error + remote frames skipped
    assert "20000004" not in set(df["ID"]) and "7DF" not in set(df["ID"])
    fd = df[df["ID"] == "123"].iloc[0]
    assert int(fd["DLC"]) == 12 and fd["B8"] == 8 and fd["B11"] == 0x0B and int(fd["Bus"]) == 1
    classic = df[df["ID"] == "386"].iloc[0]
    assert int(classic["DLC"]) == 4 and classic["B0"] == 255 and _nan(classic["B4"]) and _nan(classic["B8"])
    ext = df[df["ID"] == "18FEF100"].iloc[0]
    assert bool(ext["Extended"]) is True
    last = df[df["ID"] == "1A0"].iloc[-1]
    assert int(last["Bus"]) == 2 and last["Delta"] == pytest.approx(0.06)
    assert parse_log_file(str(FIXTURES / "candump_mixed.log")).shape == df.shape


def _write_pcap(path, frames, fmt=">I"):
    import dpkt
    with open(path, "wb") as f:
        w = dpkt.pcap.Writer(f, linktype=227)
        t = 1.0
        for word, data, fd in frames:
            hdr = struct.pack(fmt, word) + bytes([len(data), 0x04 if fd else 0, 0, 0])
            payload = data + bytes(max(0, (64 if fd else 8) - len(data)))
            w.writepkt(hdr + payload, ts=t)
            t += 0.01


def test_pcap_socketcan_big_endian_with_fd_and_flags(tmp_path):
    pytest.importorskip("dpkt")
    frames = [
        (0x1A0, bytes(range(8)), False),
        (0x123, bytes(range(12)), True),
        (CAN_ERR_FLAG | 0x4, bytes(8), False),           # error frame
        (CAN_RTR_FLAG | 0x7DF, bytes(0), False),          # remote frame
        (CAN_EFF_FLAG | 0x18FEF100, b"\x11" * 8, False),  # 29-bit
    ]
    p = tmp_path / "cap.pcap"
    _write_pcap(p, frames)
    df = parse_pcap(str(p))
    assert set(df["ID"]) == {"1A0", "123", "18FEF100"}
    fd = df[df["ID"] == "123"].iloc[0]
    assert int(fd["DLC"]) == 12 and fd["B11"] == 11
    assert _nan(df[df["ID"] == "1A0"].iloc[0]["B8"])
    assert bool(df[df["ID"] == "18FEF100"].iloc[0]["Extended"])
    assert parse_log_file(str(p)).shape == df.shape


def test_pcap_old_little_endian_capture_is_detected(tmp_path):
    pytest.importorskip("dpkt")
    frames = [(0x2B0, bytes(range(8)), False), (0x1A0, bytes(range(8)), False),
              (CAN_EFF_FLAG | 0x18FEF100, b"\x22" * 8, False)]
    p = tmp_path / "old.pcap"
    _write_pcap(p, frames, fmt="<I")
    df = parse_pcap(str(p))
    assert set(df["ID"]) == {"2B0", "1A0", "18FEF100"}


def test_pcap_rejects_non_can_linktype(tmp_path):
    dpkt = pytest.importorskip("dpkt")
    p = tmp_path / "eth.pcap"
    with open(p, "wb") as f:
        w = dpkt.pcap.Writer(f, linktype=1)
        w.writepkt(bytes(20), ts=1.0)
    with pytest.raises(ValueError, match="link type"):
        parse_log_file(str(p))


def test_bundled_sample_is_genuine_savvycan_and_parses():
    text = SAMPLE.read_text().splitlines()
    assert text[0].startswith("Time Stamp,ID,Extended")
    assert text[1].split(",")[1] == "00000018"              # 8-digit hex id as SavvyCAN writes it
    df = parse_log_file(str(SAMPLE))
    assert len(df) == 6610
    assert {"018", "0A6", "260", "544"} <= set(df["ID"])
    steer = df[df["ID"] == "018"]
    checks = ((steer[[f"B{i}" for i in range(7)]].sum(axis=1) % 256) == steer["B7"])
    assert checks.all()                                     # generator's sum checksum survives hex parsing
    assert df["Bus"].dtype.kind == "i" and df["DLC"].dtype.kind == "i"
    assert not np.isnan(df["Delta"]).any()
