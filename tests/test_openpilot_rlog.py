"""openpilot logs: built here with the vendored cereal schema, then read back.

A real log is checked in the acceptance run (a public 2021 drive from
openpilot's CI). These pin the reader's behaviour on logs small enough to
write by hand: compression, openpilot's own transmissions kept apart from the
car's traffic, the GPS as a reference, and the names openpilot gives its files.
"""
import bz2

import pytest

capnp = pytest.importorskip("capnp")

from canlab.core import openpilot_parser as op  # noqa: E402
from canlab.core.log_parser import is_openpilot_log, parse_log_file  # noqa: E402


def _log(tmp_path, name="rlog.bz2", compress=True):
    log = op.schema()
    msgs = []
    t0 = 20_000_000_000_000                         # logMonoTime, ns
    for i in range(10):
        ev = log.Event.new_message(logMonoTime=t0 + i * 10_000_000)
        frames = ev.init("can", 3)
        frames[0].address, frames[0].src = 0x0B4, 0
        frames[0].dat = bytes([0, 0, 0, 0, 0, 0x0A, i, 0])
        frames[1].address, frames[1].src = 0x18FEF100, 1
        frames[1].dat = bytes([0xC3, 0, 0x40, 0, 0, 0, 0, 0x30])
        frames[2].address, frames[2].src = 0x2E4, 0x80           # openpilot's own send
        frames[2].dat = bytes(8)
        msgs.append(ev)
    gps = log.Event.new_message(logMonoTime=t0 + 50_000_000)
    g = gps.init("gpsLocationExternal")
    g.speed, g.latitude, g.longitude, g.altitude = 25.5, 32.71, -117.15, -10.0
    msgs.append(gps)
    raw = b"".join(m.to_bytes() for m in msgs)
    path = tmp_path / name
    path.write_bytes(bz2.compress(raw) if compress else raw)
    return path


def test_a_compressed_rlog_reads_as_frames(tmp_path):
    df = op.parse_rlog(str(_log(tmp_path)))
    assert len(df) == 20 and df.attrs["sent_frames"] == 10
    assert sorted(df["ID"].unique()) == ["0B4", "18FEF100"]
    assert sorted(df["Bus"].unique()) == [0, 1]
    assert df["Timestamp"].iloc[0] == 0.0
    assert df["Timestamp"].max() == pytest.approx(0.09)
    ext = df.drop_duplicates("ID").set_index("ID")["Extended"]
    assert bool(ext["18FEF100"]) and not bool(ext["0B4"])


def test_openpilots_own_transmissions_are_kept_apart(tmp_path):
    df = op.parse_rlog(str(_log(tmp_path)), include_sent=True)
    assert len(df) == 30 and 0x80 in set(df["Bus"])


def test_an_uncompressed_log_reads_the_same(tmp_path):
    assert len(op.parse_rlog(str(_log(tmp_path, "rlog", compress=False)))) == 20


def test_the_gps_is_a_reference_on_the_same_clock(tmp_path):
    path = _log(tmp_path)
    speed, lat, lon, alt = op.gps_reference(str(path))
    assert speed.unit == "m/s" and speed.values.tolist() == [25.5]
    assert speed.ts.tolist() == pytest.approx([0.05])
    assert lat.values[0] == pytest.approx(32.71) and alt.unit == "m"


def test_openpilots_file_names_route_to_this_reader(tmp_path):
    for name in ("rlog", "qlog", "rlog.bz2", "qlog.zst", "a.rlog", "b.qlog.bz2"):
        assert is_openpilot_log(name), name
    for name in ("capture.csv", "data.bz2", "trace.log"):
        assert not is_openpilot_log(name), name
    assert len(parse_log_file(str(_log(tmp_path, "rlog.bz2")))) == 20


def test_a_zstd_log_without_zstandard_says_what_to_install(tmp_path, monkeypatch):
    path = tmp_path / "rlog.zst"
    path.write_bytes(b"\x28\xb5\x2f\xfd" + bytes(20))
    import builtins
    real_import = builtins.__import__

    def no_zstd(name, *a, **k):
        if name == "zstandard":
            raise ImportError("no zstandard")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_zstd)
    with pytest.raises(RuntimeError, match="zstandard"):
        op.parse_rlog(str(path))


def test_without_pycapnp_the_error_names_the_extra(monkeypatch, tmp_path):
    monkeypatch.setattr(op, "_CAPNP_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="canlab\\[openpilot\\]"):
        op.parse_rlog(str(tmp_path / "rlog"))


def test_the_schema_ships_with_its_licence():
    assert (op.SCHEMA_DIR / "log.capnp").is_file() and (op.SCHEMA_DIR / "car.capnp").is_file()
    notice = (op.SCHEMA_DIR / "NOTICE.txt").read_text()
    assert "Comma.ai" in notice and "MIT License" in notice
