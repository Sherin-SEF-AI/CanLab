"""Three things SavvyCAN has that CanLab did not: the sniffer view, the
capture splitter, and the GVRET hardware protocol.

The GVRET codec is tested against byte strings built to the wire format in
SavvyCAN's connections/gvretserial.cpp, so it is checked without a board
attached. No hardware was involved.
"""
import numpy as np
import pandas as pd
import pytest

from canlab.core import gvret
from canlab.core.capture_split import (
    SplitResult, _expand_ids, split, split_by_bus, split_by_frames, split_by_ids,
    split_by_percent, split_by_time,
)
from canlab.core.sniffer import FELL, ROSE, UNCHANGED, Sniffer


# ── sniffer ──────────────────────────────────────────────────────────────────

def test_sniffer_collapses_to_one_row_per_id():
    s = Sniffer()
    for i in range(5):
        s.update("100", [i, 0x40, 0xFF], ts=1.0 + i * 0.1)
        s.update("200", [0, 0, 0], ts=1.0 + i * 0.1)
    rows = s.snapshot(now=1.5)
    assert [r.can_id for r in rows] == ["100", "200"]      # numerically ordered
    assert rows[0].count == 5 and rows[0].data == [4, 0x40, 0xFF]
    assert rows[0].hex() == "04 40 FF"
    assert rows[0].rate_hz == pytest.approx(10, abs=1)


def test_direction_is_up_or_down_not_merely_different():
    s = Sniffer()
    s.update("100", [0x10, 0x10, 0x10], ts=1.0)
    s.update("100", [0x20, 0x05, 0x10], ts=1.1)
    row = s.snapshot(now=1.2)[0]
    assert row.direction[:3] == [ROSE, FELL, UNCHANGED]
    assert row.changes[:3] == [1, 1, 0]
    # The colour fades, so a byte that moved a while ago reads as steady again.
    assert s.snapshot(now=1.1 + 2.0)[0].direction[0] == UNCHANGED


def test_first_frame_of_an_id_is_not_a_change():
    s = Sniffer()
    s.update("100", [0xFF, 0x00], ts=1.0)
    row = s.snapshot(now=1.0)[0]
    assert row.direction == [UNCHANGED, UNCHANGED] and row.changes == [0, 0]


def test_notch_masks_the_bits_that_were_already_moving():
    """The point of the whole feature: silence the counter, see the button."""
    s = Sniffer()
    # B0 is a 4-bit counter that never stops; B1 is a button, still at rest.
    # A full cycle first, so every bit the counter uses has been seen moving.
    for i in range(16):
        s.update("100", [i % 16, 0x00], ts=1.0 + i * 0.1)
    assert s.snapshot(now=2.5)[0].changes[0] > 0

    added = s.notch()
    assert added == 4 and s.notched_bits() == 4    # exactly the counter nibble

    for i in range(16, 32):
        s.update("100", [i % 16, 0x00], ts=1.0 + i * 0.1)
    row = s.snapshot(now=4.2)[0]
    assert row.direction[0] == UNCHANGED, "notched counter bits still colouring"

    s.update("100", [0, 0x80], ts=5.0)          # the button, on a fresh bit
    row = s.snapshot(now=5.0)[0]
    assert row.direction[1] == ROSE, "a bit outside the notch must still show"

    # A bit above the notched nibble in the same byte still reports.
    s.update("100", [0x10, 0x80], ts=5.1)
    assert s.snapshot(now=5.1)[0].direction[0] == ROSE

    s.unnotch()
    assert s.notched_bits() == 0
    s.update("100", [0x11, 0x80], ts=5.2)
    assert s.snapshot(now=5.2)[0].direction[0] == ROSE


def test_mute_notched_blanks_those_bits_in_the_payload():
    s = Sniffer()
    s.update("100", [0x0F, 0x00], ts=1.0)
    s.update("100", [0x00, 0x00], ts=1.1)       # low nibble moved
    s.notch()
    s.update("100", [0x0F, 0x00], ts=1.2)
    assert s.snapshot(now=1.2)[0].data[0] == 0x0F
    s.mute_notched = True
    assert s.snapshot(now=1.2)[0].data[0] == 0x00


def test_ids_expire_unless_told_not_to():
    s = Sniffer(expiry_s=5.0)          # live by default, so silence ages out
    s.update("100", [0], ts=1.0)
    s.update("200", [0], ts=100.0)
    assert [r.can_id for r in s.snapshot(now=100.0)] == ["200"]
    assert s.expired(now=100.0) == ["100"]
    s.never_expire = True
    assert len(s.snapshot(now=100.0)) == 2
    s.clear()
    assert len(s) == 0 and s.snapshot(now=100.0) == []


def test_a_loaded_log_never_expires_because_it_has_no_now():
    """The capture clock is not wall time. Expiring against wall time emptied
    the whole view the moment a file was opened."""
    s = Sniffer(live=False)
    s.update("100", [1], ts=12.5)
    s.update("200", [2], ts=13.0)
    assert s.clock() == 13.0
    rows = s.snapshot()                # no explicit now: the capture's own end
    assert [r.can_id for r in rows] == ["100", "200"]
    assert rows[0].age(s.clock()) == pytest.approx(0.5)


def test_bit_view_is_msb_first():
    s = Sniffer()
    s.update("100", [0b10000001], ts=1.0)
    assert s.snapshot(now=1.0)[0].bits()[0] == [1, 0, 0, 0, 0, 0, 0, 1]


def test_sniffer_eats_the_canonical_dataframe_and_hub_rows():
    df = pd.DataFrame({
        "Timestamp": [1.0, 1.1, 1.2], "ID": ["1A0", "1A0", "2B0"],
        "Bus": [0, 0, 1], "DLC": [3, 3, 2],
        "B0": [1.0, 2.0, 9.0], "B1": [0.0, 0.0, 9.0], "B2": [5.0, 5.0, np.nan],
        **{f"B{i}": [np.nan] * 3 for i in range(3, 8)},
    })
    s = Sniffer()
    assert s.update_dataframe(df) == 3
    rows = {r.can_id: r for r in s.snapshot(now=1.2)}
    assert rows["1A0"].data == [2, 0, 5] and rows["1A0"].direction[0] == ROSE
    assert rows["2B0"].data == [9, 9] and rows["2B0"].bus == 1

    s2 = Sniffer()
    assert s2.update_frame_rows([(1.0, 0x1A0, False, 0, 3, b"\x01\x02\x03")]) == 1
    assert s2.snapshot(now=1.0)[0].can_id == "1A0"
    assert s.update_dataframe(pd.DataFrame()) == 0


# ── capture splitter ─────────────────────────────────────────────────────────

@pytest.fixture
def capture():
    n = 100
    return pd.DataFrame({
        "Timestamp": np.linspace(10.0, 19.9, n),
        "ID": [["100", "1A0", "7E8"][i % 3] for i in range(n)],
        "Bus": [i % 2 for i in range(n)],
        "DLC": [8] * n,
        **{f"B{b}": [float(i % 256) for i in range(n)] for b in range(8)},
    })


def test_split_by_time_is_relative_to_the_first_frame(capture):
    r = split_by_time(capture, 1.0, 2.0)
    assert r.n_kept + r.n_dropped == len(capture)
    assert r.kept["Timestamp"].min() >= 11.0 and r.kept["Timestamp"].max() <= 12.0
    assert list(r.kept.index) == list(range(r.n_kept)), "index must restart"
    absolute = split_by_time(capture, 11.0, 12.0, relative=False)
    assert absolute.n_kept == r.n_kept
    # Reversed bounds are a typo, not an empty result.
    assert split_by_time(capture, 2.0, 1.0).n_kept == r.n_kept
    assert "1 s to 2 s" in r.description


def test_split_by_frames_and_percent(capture):
    r = split_by_frames(capture, 10, 19)
    assert r.n_kept == 10 and r.n_dropped == 90
    assert split_by_frames(capture, 90, 999).n_kept == 10      # clamped to the end
    half = split_by_percent(capture, 0, 50)
    assert half.n_kept == 50
    assert split_by_percent(capture, 50, 100).n_kept == 50


def test_split_by_ids_accepts_ranges_and_bare_ids(capture):
    r = split_by_ids(capture, "100, 7E8")
    assert set(r.kept["ID"].unique()) == {"100", "7E8"}
    assert set(split_by_ids(capture, "0x100-0x1A0").kept["ID"].unique()) == {"100", "1A0"}
    assert split_by_ids(capture, ["100"]).n_kept == len(capture[capture["ID"] == "100"])
    assert split_by_ids(capture, "").n_kept == 0
    assert _expand_ids("1A0-100") == _expand_ids("100-1A0")      # order does not matter
    assert _expand_ids("nonsense, 100") == {"100"}


def test_split_by_bus_and_invert(capture):
    r = split_by_bus(capture, 1)
    assert set(r.kept["Bus"].unique()) == {1} and r.n_kept == 50
    inv = split_by_bus(capture, 1, invert=True)
    assert set(inv.kept["Bus"].unique()) == {0}
    assert "everything except" in inv.description


def test_split_dispatch_and_summary(capture):
    r = split(capture, "frames", first=0, last=9)
    assert r.n_kept == 10 and "10 of 100 frames (10.0%)" in r.summary()
    with pytest.raises(ValueError):
        split(capture, "nope")
    empty = split(pd.DataFrame(), "frames", first=0, last=5)
    assert isinstance(empty, SplitResult) and empty.n_kept == 0


def test_a_trimmed_capture_is_still_a_capture(capture):
    """Trimming has to produce something the rest of the application accepts,
    since Replace the loaded capture feeds it straight back into the store."""
    from canlab.core.frame_store import FrameStore
    from canlab.core.sniffer import Sniffer
    kept = split_by_frames(capture, 0, 19).kept
    store = FrameStore()
    store.load_dataframe(kept)
    back = store.materialize()
    assert len(back) == 20
    assert set(back["ID"]) == set(kept["ID"])
    assert {"Timestamp", "ID", "Bus", "DLC", "B0"} <= set(back.columns)
    assert Sniffer().update_dataframe(back) == 20


# ── GVRET ────────────────────────────────────────────────────────────────────

def _rx_frame(us: int, ident: int, data: bytes, *, bus: int = 0, extended: bool = False):
    """A frame exactly as a GVRET board sends one."""
    ident_word = ident | (gvret.EXTENDED_FLAG if extended else 0)
    return (bytes([gvret.SOF, gvret.CMD_FRAME])
            + us.to_bytes(4, "little") + ident_word.to_bytes(4, "little")
            + bytes([(len(data) & 0x0F) | ((bus & 0x0F) << 4)]) + data)


def test_codec_decodes_a_frame():
    c = gvret.GvretCodec()
    frames = c.feed(_rx_frame(1_500_000, 0x1A0, b"\x01\x02\x03"))
    assert len(frames) == 1
    f = frames[0]
    assert f.arbitration_id == 0x1A0 and not f.is_extended_id
    assert f.data == b"\x01\x02\x03" and f.dlc == 3 and f.channel == 0
    assert f.timestamp == pytest.approx(1.5)


def test_codec_handles_extended_ids_and_bus_index():
    c = gvret.GvretCodec()
    f = c.feed(_rx_frame(0, 0x18DAF110, b"\xAA", bus=1, extended=True))[0]
    assert f.arbitration_id == 0x18DAF110 and f.is_extended_id and f.channel == 1


def test_codec_reassembles_across_arbitrary_chunk_boundaries():
    """Serial reads split wherever they like; the parser must not care."""
    stream = b"".join(_rx_frame(i * 1000, 0x100 + i, bytes([i]) * (i % 8 + 1))
                      for i in range(20))
    for size in (1, 2, 3, 5, 7, 64):
        c = gvret.GvretCodec()
        got = []
        for i in range(0, len(stream), size):
            got += c.feed(stream[i:i + size])
        assert len(got) == 20, f"chunk size {size}"
        assert [f.arbitration_id for f in got] == [0x100 + i for i in range(20)]
        assert c.desyncs == 0


def test_codec_skips_the_handshake_replies_without_losing_sync():
    c = gvret.GvretCodec()
    stream = (bytes([gvret.SOF, gvret.CMD_GET_NUM_BUSES, 2])
              + bytes([gvret.SOF, gvret.CMD_GET_CANBUS_PARAMS]) + bytes(10)
              + bytes([gvret.SOF, gvret.CMD_GET_DEVICE_INFO])
              + (618).to_bytes(2, "little") + bytes([0, 0, 0, 1])
              + bytes([gvret.SOF, gvret.CMD_VALIDATE])
              + bytes([gvret.SOF, gvret.CMD_TIME_SYNC]) + bytes(4)
              + _rx_frame(7, 0x2C4, b"\xDE\xAD"))
    frames = c.feed(stream)
    assert [f.arbitration_id for f in frames] == [0x2C4]
    assert c.device_info == {"buses": 2, "build": 618, "single_wire": 1}
    assert c.desyncs == 0


def test_codec_resynchronises_after_noise():
    c = gvret.GvretCodec()
    frames = c.feed(b"garbage text from the boot loader\r\n"
                    + _rx_frame(0, 0x123, b"\x01"))
    assert [f.arbitration_id for f in frames] == [0x123]
    assert c.desyncs > 0


def test_encode_frame_matches_the_reference_layout():
    out = gvret.encode_frame(0x1A0, b"\x11\x22", bus=1)
    assert out == bytes([0xF1, 0x00, 0xA0, 0x01, 0x00, 0x00, 0x01, 0x02, 0x11, 0x22, 0x00])
    ext = gvret.encode_frame(0x18DAF110, b"", extended=True)
    assert ext[2:6] == (0x18DAF110 | 0x80000000).to_bytes(4, "little")
    assert ext[7] == 0 and ext[-1] == 0
    assert len(gvret.encode_frame(0x1, b"\xFF" * 20)) == 2 + 4 + 2 + 8 + 1   # trimmed to 8


def test_encode_setup_carries_the_flag_bits():
    out = gvret.encode_setup(500_000)
    assert out[:2] == bytes([0xF1, 0x05])
    w0 = int.from_bytes(out[2:6], "little")
    assert w0 & 0x0FFFFFFF == 500_000
    assert w0 & gvret.BAUD_CONFIGURED and w0 & gvret.BAUD_ENABLED
    assert not w0 & gvret.BAUD_LISTEN_ONLY
    w1 = int.from_bytes(out[6:10], "little")
    assert not w1 & gvret.BAUD_ENABLED, "second bus must not come up uninvited"
    assert gvret.baud_word(250_000, listen_only=True) & gvret.BAUD_LISTEN_ONLY
    assert gvret.encode_setup(500_000, 250_000, enable1=True)[6:10] == \
        gvret.baud_word(250_000).to_bytes(4, "little")


def test_handshake_enters_binary_mode_first():
    hs = gvret.encode_handshake()
    assert hs.startswith(b"\xE7\xE7")
    for cmd in (gvret.CMD_GET_NUM_BUSES, gvret.CMD_GET_DEVICE_INFO, gvret.CMD_VALIDATE):
        assert bytes([gvret.SOF, cmd]) in hs


def test_gvret_is_a_python_can_interface():
    import can.util
    from can.interfaces import BACKENDS
    from canlab.core import adapters
    assert gvret.register()
    assert "gvret" in BACKENDS and "gvret" in can.util.VALID_INTERFACES
    assert "gvret" in adapters.INTERFACES
    kw = adapters.Adapter("m2", "gvret", "/dev/ttyACM0", 500_000,
                          extra={"tty_baudrate": 1_000_000, "bus_index": 0}).bus_kwargs()
    assert kw == {"interface": "gvret", "channel": "/dev/ttyACM0", "bitrate": 500_000,
                  "tty_baudrate": 1_000_000, "bus_index": 0}


class _FakeSerial:
    """A board on the other end of the wire."""

    def __init__(self, script: bytes = b""):
        self.rx = bytearray(script)
        self.written = bytearray()
        self.closed = False

    def read(self, n=1):
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def write(self, data):
        self.written.extend(data)
        return len(data)

    def close(self):
        self.closed = True


def test_bus_receives_sends_and_shuts_down(monkeypatch):
    import can
    script = _rx_frame(1_000_000, 0x1A0, b"\x01\x02") + _rx_frame(1_010_000, 0x2B0, b"\x03")
    fake = _FakeSerial(script)
    monkeypatch.setattr(gvret, "_open_stream", lambda *a, **k: fake)
    bus = gvret.GvretBus("/dev/fake", bitrate=250_000)
    try:
        assert fake.written.startswith(b"\xE7\xE7")            # handshake
        assert bytes([0xF1, 0x05]) in bytes(fake.written)      # then setup
        first = bus.recv(timeout=1.0)
        second = bus.recv(timeout=1.0)
        assert first.arbitration_id == 0x1A0 and bytes(first.data) == b"\x01\x02"
        assert second.arbitration_id == 0x2B0
        # Board microseconds become host time, keeping the 10 ms gap.
        assert second.timestamp - first.timestamp == pytest.approx(0.01, abs=1e-4)
        assert bus.recv(timeout=0.05) is None

        fake.written.clear()
        bus.send(can.Message(arbitration_id=0x123, data=b"\xAA", is_extended_id=False))
        assert bytes(fake.written) == gvret.encode_frame(0x123, b"\xAA")
    finally:
        bus.shutdown()
    assert fake.closed


def test_listen_only_bus_refuses_to_transmit(monkeypatch):
    import can
    fake = _FakeSerial()
    monkeypatch.setattr(gvret, "_open_stream", lambda *a, **k: fake)
    bus = gvret.GvretBus("/dev/fake", bitrate=500_000, listen_only=True)
    try:
        w0 = int.from_bytes(bytes(fake.written).split(bytes([0xF1, 0x05]))[1][:4], "little")
        assert w0 & gvret.BAUD_LISTEN_ONLY
        with pytest.raises(RuntimeError, match="listen-only"):
            bus.send(can.Message(arbitration_id=1, data=b""))
    finally:
        bus.shutdown()


def test_probe_reports_a_missing_serial_port_helpfully():
    from canlab.core.adapters import Adapter, probe_adapter
    r = probe_adapter(Adapter("m2", "gvret", "/dev/canlab-no-such-port"), listen_s=0.1)
    assert not r["ok"] and "no-such-port" in r["error"].lower()


# ── the UI pieces ────────────────────────────────────────────────────────────

def test_sniffer_tab_shows_the_loaded_capture(qcore):
    pytest.importorskip("PyQt6")
    from canlab.core.log_parser import parse_log_file
    from canlab.core.state import get_state
    from canlab.tabs.sniffer_tab import SnifferTab
    state = get_state()
    state.load_frames(parse_log_file("canlab/sample_data/sample_kona_drive.csv"), "sample")
    tab = SnifferTab()
    try:
        tab.show()                       # the tick does nothing while hidden
        qcore.processEvents()
        tab._tick()
        assert tab.table.rowCount() > 0
        ids = {tab.table.item(r, 0).text() for r in range(tab.table.rowCount())}
        assert ids <= set(state.get_unique_ids())
        assert tab.table.item(0, 4).text() != ""          # a byte is rendered

        before = tab._sniffer.notched_bits()
        tab._notch()
        assert tab._sniffer.notched_bits() >= before
        tab._unnotch()
        assert tab._sniffer.notched_bits() == 0

        tab.chk_bits.setChecked(True)
        tab._tick()
        assert len(tab.table.item(0, 4).text()) in (0, 8)  # eight bits, or empty
        tab.chk_bits.setChecked(False)

        tab.btn_pause.setChecked(True)
        rows_paused = tab.table.rowCount()
        tab._tick()
        assert tab.table.rowCount() == rows_paused
        tab.btn_pause.setChecked(False)

        tab._clear()
        assert tab.table.rowCount() == 0
    finally:
        tab.cleanup()
        tab.deleteLater()


def test_trim_dialog_previews_and_returns_the_subset(qcore, capture):
    pytest.importorskip("PyQt6")
    from canlab.ui.trim_dialog import TrimDialog
    dlg = TrimDialog(capture)
    try:
        assert "100 frames" in dlg.lbl_preview.text() or "of 100 frames" in dlg.lbl_preview.text()
        dlg.mode_combo.setCurrentIndex(1)                  # frame numbers
        dlg.f_from.setValue(0); dlg.f_to.setValue(9)
        assert "10 of 100 frames" in dlg.lbl_preview.text()
        dlg.mode_combo.setCurrentIndex(3)                  # IDs
        dlg.id_edit.setText("100")
        assert dlg._result.n_kept == len(capture[capture["ID"] == "100"])
        dlg.chk_invert.setChecked(True)
        assert dlg._result.n_kept == len(capture) - len(capture[capture["ID"] == "100"])
        # An empty selection cannot be accepted.
        dlg.chk_invert.setChecked(False)
        dlg.id_edit.setText("nonsense")
        from PyQt6.QtWidgets import QDialogButtonBox
        assert not dlg.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    finally:
        dlg.deleteLater()
