"""The capture kit: segments, marks, a session on a fake bus, and the project.

Nothing here opens hardware. The bus is a recording double or python-can's
in-process virtual backend, and the kit must never send on either.
"""
import sys
import threading
import time
import types

import pandas as pd
import pytest

from canlab.core.capture_kit import (
    CaptureOptions, CaptureSession, MarkFile, build_project, gpio_marker,
    parse_mark_line, parse_pin_map, stdin_marker,
)
from canlab.core.capture_writer import SAVVYCAN_HEADER, SegmentWriter, iter_segments
from canlab.core.log_parser import parse_log_file, parse_savvycan_csv
from tests.doubles import FakeMsg, RecordingBus

SAMPLE = "canlab/sample_data/sample_kona_drive.csv"


def rows(n, t0=0.0, dt=0.01, arb=0x1A0):
    return [(t0 + i * dt, arb + (i % 3), False, 0, 8, bytes([i & 0xFF] * 8)) for i in range(n)]


# ── segments ─────────────────────────────────────────────────────────────────

def test_segments_rotate_by_count_and_every_one_parses(tmp_path):
    w = SegmentWriter(tmp_path, "seg", max_frames=100, max_seconds=0)
    assert w.write_rows(rows(250)) == 250
    paths = w.close()
    assert [p.name for p in paths] == ["seg-0001.csv", "seg-0002.csv", "seg-0003.csv"]
    counts = [len(parse_savvycan_csv(str(p))) for p in paths]
    assert counts == [100, 100, 50] and w.frames == 250
    joined = pd.concat(list(iter_segments(paths)), ignore_index=True)
    assert len(joined) == 250 and joined["ID"].nunique() == 3
    assert joined["Timestamp"].iloc[-1] == pytest.approx(2.49)


def test_segments_rotate_by_time(tmp_path):
    w = SegmentWriter(tmp_path, max_frames=10**9, max_seconds=1.0)
    w.write_rows(rows(300, dt=0.01))            # 0.00 .. 2.99 s
    paths = w.close()
    assert len(paths) == 3
    assert all(p.read_text().startswith(SAVVYCAN_HEADER) for p in paths)


def test_an_empty_last_segment_is_not_left_behind(tmp_path):
    w = SegmentWriter(tmp_path)
    w.write_rows(rows(5))
    w.rotate()                                   # opens an empty second file
    assert len(w.close()) == 1 and len(list(tmp_path.glob("*.csv"))) == 1


def test_convert_writer_and_segment_writer_agree_byte_for_byte(tmp_path):
    from canlab.cli import write_savvycan_csv
    df = parse_log_file(SAMPLE)
    write_savvycan_csv(df, tmp_path / "convert.csv")
    tuples = [(float(r.Timestamp), int(r.ID, 16), bool(r.Extended), r.Bus, int(r.DLC),
               bytes(int(getattr(r, f"B{i}")) for i in range(int(r.DLC))))
              for r in df.itertuples()]
    w = SegmentWriter(tmp_path / "seg", max_frames=10**9, max_seconds=0)
    w.write_rows(tuples)
    (seg,) = w.close()
    assert seg.read_bytes() == (tmp_path / "convert.csv").read_bytes()


# ── marks ────────────────────────────────────────────────────────────────────

def test_marks_survive_reload_and_a_point_becomes_an_interval(tmp_path):
    m = MarkFile(tmp_path / "marks.json")
    assert m.begin("brake", 10.0) == "begin" and m.open_labels() == ["brake"]
    assert m.end("brake", 12.0) == "end" and m.open_labels() == []
    assert m.point("horn", 15.0) == "point"
    again = MarkFile(tmp_path / "marks.json")
    items = again.annotations.items
    assert [(a.label, a.start, a.end) for a in items] == [("brake", 10.0, 12.0), ("horn", 14.5, 15.5)]
    assert again.end("nothing", 1.0) == "none"


def test_toggle_opens_then_closes_and_stop_closes_what_is_open(tmp_path):
    m = MarkFile(tmp_path / "marks.json")
    assert m.toggle("brake", 1.0) == "begin"
    assert m.toggle("brake", 2.0) == "end"
    assert m.apply("horn", "toggle", 3.0) == "begin"
    assert m.close_all(4.0) == 1 and m.annotations.items[-1].end == 4.0
    with pytest.raises(ValueError):
        m.apply("x", "explode", 1.0)


def test_parse_mark_line_and_pin_map():
    assert parse_mark_line("brake") == ("brake", "toggle")
    assert parse_mark_line("brake on") == ("brake", "begin")
    assert parse_mark_line("brake off") == ("brake", "end")
    assert parse_mark_line("horn point") == ("horn", "point")
    assert parse_mark_line("   ") is None
    assert parse_pin_map("17=brake, 27=horn") == {17: "brake", 27: "horn"}
    with pytest.raises(ValueError):
        parse_pin_map("17=brake,17=horn")
    with pytest.raises(ValueError):
        parse_pin_map("brake")


# ── a session ────────────────────────────────────────────────────────────────

def _feed(bus, n, arb=0x1A0):
    t0 = time.time()
    for i in range(n):
        bus.feed(FakeMsg(arb + (i % 2), bytes([i & 0xFF, 1, 2, 3, 4, 5, 6, 7]),
                         timestamp=t0 + i * 0.001))


def test_a_full_session_on_a_recording_bus_becomes_a_project(tmp_path, qcore):
    bus = RecordingBus()
    _feed(bus, 300)
    opts = CaptureOptions(out_dir=tmp_path / "run", prefix="drive", max_frames=120)
    session = CaptureSession(bus, opts, name="fake", bitrate=500_000)
    stop = threading.Event()

    def lines():
        time.sleep(0.2)
        yield "brake\n"
        time.sleep(0.2)
        yield "brake\n"
        time.sleep(0.2)
        yield "q\n"

    threading.Thread(target=stdin_marker, args=(session, lines(), stop), daemon=True).start()
    summary = session.run(stop, duration_s=10.0)
    assert stop.is_set(), "q did not stop the run"
    assert summary["frames"] == 300 and summary["marks"] == 1 and summary["ids"] == 2
    assert len(summary["segments"]) == 3                    # 120 + 120 + 60
    assert (tmp_path / "run" / "marks.json").is_file()
    assert bus.sent == []                                   # observed, never answered
    assert bus.shutdown_calls == 1                          # the hub closes the bus once

    project = tmp_path / "run" / "drive.canlab.zip"
    n = build_project(session.writer.segments, session.marks.annotations, project,
                      meta={"adapter": "fake"})
    assert n == 300
    from canlab.core.project import load_project
    from canlab.core.state import AppState
    state = AppState()
    load_project(state, str(project))
    assert len(state.frames_df) == 300 and state.frames_df["ID"].nunique() == 2
    (mark,) = state.annotations.items
    assert mark.label == "brake" and mark.closed and mark.end > mark.start
    assert state.frames_df["Timestamp"].min() <= mark.start <= state.frames_df["Timestamp"].max() + 1


def test_an_empty_run_still_writes_marks_and_no_segments(tmp_path):
    session = CaptureSession(RecordingBus(), CaptureOptions(out_dir=tmp_path / "empty"))
    summary = session.run(threading.Event(), duration_s=0.3)
    assert summary["frames"] == 0 and summary["segments"] == []
    assert (tmp_path / "empty" / "marks.json").read_text().strip() == "[]"


def test_http_state_view_shows_the_tail_and_marks_land(tmp_path):
    bus = RecordingBus()
    _feed(bus, 50)
    session = CaptureSession(bus, CaptureOptions(out_dir=tmp_path / "v", keep_tail=1000))
    session.start()
    deadline = time.time() + 5
    while time.time() < deadline and session.hub.rx_count < 50:
        time.sleep(0.01)
    session.step()
    state = session.state_getter()
    assert state.is_connected and len(state.frames_df) == 50 and state.dbc_signals == []
    assert session.mark("brake", "begin", at=100.0) == "begin"
    assert session.mark("brake", "end", at=101.0) == "end"
    assert session.mark("horn") == "begin"                  # toggle on the wall clock
    summary = session.stop()
    assert summary["marks"] == 2 and not state.is_connected
    assert MarkFile(tmp_path / "v" / "marks.json").open_labels() == []


def test_gpio_is_wired_when_gpiozero_exists_and_skipped_when_absent(tmp_path, monkeypatch):
    session = CaptureSession(RecordingBus(), CaptureOptions(out_dir=tmp_path / "g"))

    class Button:
        made = []

        def __init__(self, pin):
            self.pin = pin
            self.when_pressed = None
            self.when_released = None
            Button.made.append(self)

    monkeypatch.setitem(sys.modules, "gpiozero", types.SimpleNamespace(Button=Button))
    notes = gpio_marker(session, {17: "brake"})
    assert notes == ["GPIO 17 marks 'brake' while pressed"]
    (button,) = Button.made
    button.when_pressed()
    assert session.marks.open_labels() == ["brake"]
    button.when_released()
    assert session.marks.open_labels() == [] and len(session.marks) == 1

    monkeypatch.setitem(sys.modules, "gpiozero", None)      # import raises
    notes = gpio_marker(session, {27: "horn"})
    assert len(notes) == 1 and "unavailable" in notes[0] and "[27]" in notes[0]
    assert gpio_marker(session, {}) == []


def test_a_python_can_virtual_bus_run(tmp_path):
    can = pytest.importorskip("can")
    channel = f"kit-{time.time_ns()}"
    reader = can.Bus(interface="virtual", channel=channel)
    sender = can.Bus(interface="virtual", channel=channel)
    session = CaptureSession(reader, CaptureOptions(out_dir=tmp_path / "virt"),
                             name="virtual", bitrate=500_000)

    def talk():
        time.sleep(0.2)
        for i in range(50):
            sender.send(can.Message(arbitration_id=0x123, data=[i, 0, 0, 0], is_extended_id=False))
            time.sleep(0.002)

    threading.Thread(target=talk, daemon=True).start()
    try:
        summary = session.run(threading.Event(), duration_s=1.5)
    finally:
        reader.shutdown()
        sender.shutdown()
    assert summary["frames"] == 50 and summary["ids"] == 1
    (seg,) = summary["segments"]
    df = parse_savvycan_csv(seg)
    assert len(df) == 50 and df["ID"].iloc[0] == "123" and int(df["DLC"].iloc[0]) == 4
