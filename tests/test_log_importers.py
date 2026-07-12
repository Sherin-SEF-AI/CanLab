"""Round-trip tests for the BLF / ASC / MDF4 capture importers.

Uses python-can's writers to emit tiny capture files with known IDs (including
a low ID 0x0A6 that the historic lstrip("0x") bug corrupted, and an extended
ID), then parses them back and asserts the canonical schema round-trips.
"""
import pytest

can = pytest.importorskip("can")

from core.log_parser import parse_asc, parse_blf, parse_log_file


# (arbitration_id, data, is_extended_id)
FRAMES = [
    (0x0A6, b"\x01\x02\x03\x04", False),          # low ID -> must canonicalize to "0A6"
    (0x244, b"\xDE\xAD\xBE\xEF\x00\x11\x22\x33", False),
    (0x7E8, b"\x03\x41\x00", False),              # short frame -> NaN tail bytes
    (0x18FEF100, b"\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF", True),  # 29-bit extended
]

EXPECTED_IDS = ["0A6", "244", "7E8", "18FEF100"]


def _write_messages(writer_cls, path):
    writer = writer_cls(str(path))
    t = 1000.0
    for arb, data, ext in FRAMES:
        writer.on_message_received(
            can.Message(
                timestamp=t,
                arbitration_id=arb,
                data=data,
                is_extended_id=ext,
                channel=0,
            )
        )
        t += 0.01
    writer.stop()


def _assert_roundtrip(df):
    assert len(df) == len(FRAMES)

    # IDs are canonical strings.
    assert set(df["ID"]) == set(EXPECTED_IDS)
    assert "0A6" in df["ID"].values          # low ID not corrupted to "A6"
    assert "18FEF100" in df["ID"].values     # extended ID keeps full width

    # Required schema columns present.
    for col in ["Timestamp", "ID", "Bus", "DLC", "Delta"] + [f"B{i}" for i in range(8)]:
        assert col in df.columns

    # Payload round-trips for the 0x0A6 frame.
    row = df[df["ID"] == "0A6"].iloc[0]
    assert int(row["DLC"]) == 4
    assert [int(row[f"B{i}"]) for i in range(4)] == [1, 2, 3, 4]

    # Short 0x7E8 frame has NaN tail bytes beyond its 3-byte payload.
    short = df[df["ID"] == "7E8"].iloc[0]
    assert int(short["DLC"]) == 3
    for i in range(3, 8):
        assert short[f"B{i}"] != short[f"B{i}"]  # NaN

    # Extended flag reported for the 29-bit ID.
    ext = df[df["ID"] == "18FEF100"].iloc[0]
    assert bool(ext["Extended"]) is True


def test_blf_roundtrip(tmp_path):
    path = tmp_path / "capture.blf"
    _write_messages(can.BLFWriter, path)

    df = parse_blf(str(path))
    _assert_roundtrip(df)

    # Dispatch by suffix works too.
    _assert_roundtrip(parse_log_file(str(path)))


def test_asc_roundtrip(tmp_path):
    path = tmp_path / "capture.asc"
    _write_messages(can.ASCWriter, path)

    df = parse_asc(str(path))
    _assert_roundtrip(df)

    _assert_roundtrip(parse_log_file(str(path)))


def test_mdf_importer(tmp_path):
    """MDF4 importer round-trips when asammdf is available; skipped otherwise."""
    pytest.importorskip("asammdf")
    from asammdf import MDF  # noqa: F401
    from core.log_parser import parse_mdf

    # Build an MDF4 capture from the same frames using python-can's writer.
    mf4 = pytest.importorskip("can.io.mf4", reason="python-can MF4 support unavailable")
    path = tmp_path / "capture.mf4"
    writer = can.MF4Writer(str(path))
    t = 1000.0
    for arb, data, ext in FRAMES:
        writer.on_message_received(
            can.Message(timestamp=t, arbitration_id=arb, data=data,
                        is_extended_id=ext, channel=0)
        )
        t += 0.01
    writer.stop()

    df = parse_mdf(str(path))
    assert set(df["ID"]) == set(EXPECTED_IDS)
    assert "0A6" in df["ID"].values
