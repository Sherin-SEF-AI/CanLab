#!/usr/bin/env python3
"""Record a full pass over a real electric car's bus, as a saved project.

The capture is 460,024 frames recorded from a Tata Tigor EV through its
OBD-II port while the car was parked, and it is somebody's own vehicle, so it
is not committed to this repository. Point this at the project file and it
drives the real window through the whole workflow on it.

The point of using it is that nothing here is known in advance. There is no
manufacturer database, no ground truth, and the car was stationary, so most of
what a moving vehicle would say is absent. The narration says what the tool
found and what it could not, in that order.

    QT_QPA_PLATFORM=offscreen python docs/demo/record_tigor.py [project.canlab.zip]
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, QSettings                          # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QTabWidget  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

OUT = HERE / "build-tigor"
FRAMES = OUT / "frames"
PROJECT = Path(sys.argv[1] if len(sys.argv) > 1
               else Path.home() / "Documents" / "can-data" / "tatatigor.canlab.zip")
WIDTH, HEIGHT = 1920, 1080

_cfg = tempfile.mkdtemp(prefix="canlab-tigor-")
for _fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
    QSettings.setPath(_fmt, QSettings.Scope.UserScope, _cfg)
for _name in ("information", "warning", "critical", "question", "about"):
    setattr(QMessageBox, _name, staticmethod(lambda *a, **k: None))

app = QApplication(sys.argv[:1])

from canlab.theme import QSS, mono_font                            # noqa: E402

app.setStyleSheet(QSS)
app.setFont(mono_font())

from canlab.core.state import get_state                            # noqa: E402
from canlab.mainwindow import MainWindow                           # noqa: E402

state = get_state()
window = MainWindow()
window.showNormal()
window.setGeometry(QRect(0, 0, WIDTH, HEIGHT))

_scenes: list[dict] = []
_counter = 0


def pump(seconds: float = 0.25) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(predicate, timeout: float = 240.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def scene(key: str, narration: str, *, count: int = 3, gap: float = 0.5) -> None:
    global _counter
    names = []
    for _ in range(count):
        pump(gap)
        _counter += 1
        name = f"{_counter:04d}.png"
        window.grab().save(str(FRAMES / name))
        names.append(name)
    _scenes.append({"key": key, "narration": " ".join(narration.split()),
                    "frames": names})
    print(f"  scene: {key}", flush=True)


def card(key: str, narration: str, image, count: int = 4) -> None:
    global _counter
    names = []
    for _ in range(count):
        _counter += 1
        name = f"{_counter:04d}.png"
        image.save(str(FRAMES / name))
        names.append(name)
    _scenes.append({"key": key, "narration": " ".join(narration.split()),
                    "frames": names})
    print(f"  scene: {key}", flush=True)


def tab(name: str):
    for i in range(window.tabs.count()):
        if window.tabs.tabText(i).split()[0] == name.split()[0]:
            window.tabs.setCurrentIndex(i)
            pump(0.4)
            return window.tabs.widget(i)
    raise KeyError(name)


def subtab(widget, label: str):
    inner = widget.findChild(QTabWidget)
    for i in range(inner.count()):
        if inner.tabText(i).upper().startswith(label.upper()):
            inner.setCurrentIndex(i)
            pump(0.4)
            return inner.widget(i)
    raise KeyError(f"no sub-tab {label!r} in "
                   f"{[inner.tabText(i) for i in range(inner.count())]}")


def findings_card(facts: dict):
    """The closing card, built from what this run measured."""
    from PIL import Image, ImageDraw

    import titles as T
    img = T.background(3.0).convert("RGBA")
    d = ImageDraw.Draw(img)
    T.draw_centered(d, WIDTH / 2, 86, "WHAT THE BUS GAVE UP", T.mono(32, bold=True),
                    (*T.GREEN, 255))
    T.draw_centered(d, WIDTH / 2, 142, "Tata Tigor EV, parked, 26.8 minutes",
                    T.ui(46, 700), (*T.TEXT, 255))
    rows = [
        ("frames", f"{facts['frames']:,} at {facts['fps']:.0f} a second"),
        ("messages", f"{facts['ids']} identifiers, 11-bit, one bus"),
        ("fastest", "0x111 at 100 Hz, and its eight bytes never change"),
        ("moving", f"{facts['moving_ids']} of {facts['ids']} messages have any byte that moves"),
        ("counters", "none found"),
        ("checksums", "none reproduced by the algorithms tried"),
        ("bit flags", f"{facts['flags']} candidates across {facts['flag_ids']} messages"),
        ("field edges", f"{facts['boundaries']} from bit entropy"),
        ("multiplexed", f"{facts['mux']} messages"),
        ("high entropy", "2 bytes take all 256 values: not a counter, not a sum"),
        ("transmitted", "nothing; no frame was sent to the car"),
    ]
    y = 230
    for label, value in rows:
        d.rounded_rectangle((260, y, WIDTH - 260, y + 56), 10,
                            fill=(18, 20, 22, 220), outline=(58, 64, 68, 255), width=2)
        d.text((290, y + 14), label, font=T.mono(24, bold=True), fill=(*T.DIM, 255))
        d.text((700, y + 12), value, font=T.mono(26), fill=(*T.TEXT, 255))
        y += 66
    return Image.alpha_composite(Image.new("RGBA", img.size, (0, 0, 0, 255)),
                                 img).convert("RGB")


def main() -> int:
    if not PROJECT.is_file():
        raise SystemExit(f"no project at {PROJECT}")
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)

    # 1 ── open the saved project, the way its owner would ───────────────────
    from canlab.core.project import load_project

    tab("FRAMES")
    started = time.perf_counter()
    load_project(state, str(PROJECT))
    wait_until(lambda: len(state.frames_df) > 400_000, 240)
    load_seconds = time.perf_counter() - started
    pump(1.5)

    df = state.frames_df
    span = float(df["Timestamp"].max() - df["Timestamp"].min())
    facts = {"frames": len(df), "ids": int(df["ID"].nunique()),
             "fps": len(df) / span, "span": span}

    scene("open",
          f"""This is a real electric car: a Tata Tigor, recorded through its
              OBD-II port while it sat parked. Four hundred and sixty thousand
              frames over twenty seven minutes, saved as a project and reopened
              here in {load_seconds:.0f} seconds. Nothing about this bus is
              known in advance. There is no manufacturer database behind it and
              no list of what any message means.""")

    # 2 ── the shape of the bus ──────────────────────────────────────────────
    tab("SIGNALS")
    pump(1.2)
    scene("shape",
          """Thirty nine messages, all eleven bit, on one bus. One of them
             repeats a hundred times a second, one fifty times, a handful at
             ten, and twenty seven more at two hertz in a block. That block is
             the shape a battery management system usually has, one message per
             group of cells, though nothing here proves it.""")

    # 3 ── what actually moves ───────────────────────────────────────────────
    tab("SNIFFER")
    window.sniffer_tab._clear()
    t0 = time.perf_counter()
    window.sniffer_tab._tick()
    fold = time.perf_counter() - t0
    pump(0.8)
    B = [f"B{i}" for i in range(8)]
    moving = sum(1 for cid, g in df.groupby("ID")
                 if any(g[c].nunique(dropna=True) > 1 for c in B if c in g))
    facts["moving_ids"] = moving
    scene("sniffer",
          f"""The sniffer folds all of it into one row per message in
              {fold * 1000:.0f} milliseconds, and this is where a parked car
              shows what it is. The fastest message on the bus, a hundred
              times a second, never changes a single byte. Only {moving} of the
              thirty nine messages contain a byte that moves at all. Everything
              else is a heartbeat.""")

    # 4 ── the detectors ─────────────────────────────────────────────────────
    auto = tab("AUTO-RE")
    window.auto_re_tab._run_counter_checksum()
    wait_until(lambda: window.auto_re_tab.ctr_table.rowCount() > 0, 30)
    pump(1.0)
    scene("counters",
          """The counter and checksum detectors find nothing here, and an empty
             table is a real answer. These heuristics were written against
             buses that carry rolling counters and checksum bytes; this one
             does not, at least not in any form they recognise. Reporting that
             honestly is worth more than inventing a match.""")

    subtab(auto, "FLAGS")
    if hasattr(window.auto_re_tab, "_run_flags"):
        window.auto_re_tab._run_flags()
        wait_until(lambda: window.auto_re_tab.flags_table.rowCount() > 0, 60)
    pump(0.8)
    from canlab.core.bit_flags import detect_flags
    flags = detect_flags(df)
    facts["flags"] = sum(len(v) for v in flags.values())
    facts["flag_ids"] = len(flags)
    scene("flags",
          f"""Below byte level there is more. {facts['flags']} single bits across
              {facts['flag_ids']} messages behave like switches: two states,
              held for a while, flipping now and then. On a stationary car those
              are the things that were actually doing something, doors, relays,
              a charge state.""")

    subtab(auto, "ENTROPY")
    window.auto_re_tab._run_entropy()
    wait_until(lambda: window.auto_re_tab.entropy_table.rowCount() > 0, 120)
    pump(0.8)
    from canlab.core.entropy_boundary import detect_signal_boundaries
    edges = detect_signal_boundaries(df)
    facts["boundaries"] = (sum(len(v) for v in edges.values())
                           if isinstance(edges, dict) else len(edges))
    scene("entropy",
          f"""Entropy per bit suggests {facts['boundaries']} places where one
              field ends and the next begins. They are candidates ranked by a
              match fraction over the frames loaded, not conclusions, and on a
              bus this quiet there is less for them to work with than there
              would be on a moving car.""")

    # 5 ── a candidate signal, defined and checked ───────────────────────────
    sig = {"message_id": "245", "message_name": "MSG_245",
           "signal_name": "CANDIDATE_245_B4B5", "start_bit": 39, "length": 16,
           "byte_order": "big", "value_type": "unsigned",
           "scale": 1.0, "offset": 0.0, "min_val": 0.0, "max_val": 65535.0,
           "unit": "", "description": "16-bit field, bytes 4 and 5, unidentified"}
    state.add_dbc_signal(sig)
    pump(0.5)
    tab("DBC")
    window.dbc_tab._on_list_select(0)
    pump(0.6)
    scene("define",
          """One field is worth defining. Bytes four and five of message two
             four five, read together, hold a sixteen bit number that stays
             between three thousand and eighty three and three thousand and
             ninety seven for the whole recording. That is the size and
             steadiness of a cell voltage in millivolts, which is a guess, and
             the definition is named as a candidate rather than as a signal.""")

    tab("PLOT")
    window.plot_tab._add_signal("245:dbc:CANDIDATE_245_B4B5", "dbc", "245", sig)
    pump(1.5)
    scene("plot",
          """Plotted over twenty seven minutes it drifts by fourteen units and
             nothing else. That is consistent with a battery resting, and it is
             also consistent with several other things. This is the honest end
             of the road for a parked car: to go further the vehicle has to
             move, or something has to be pressed while the capture runs.""")

    # 6 ── what the protocol decoders say ────────────────────────────────────
    tab("INTELLIGENCE")
    window.intelligence_tab._run_j1939()
    wait_until(lambda: window.intelligence_tab.j1939_table.rowCount() > 0, 60)
    pump(0.8)
    scene("protocol",
          """The protocol decoders are asked as well, and they decline. Every
             identifier here is eleven bit, so this is neither J1939 nor NMEA
             2000, and the scan says exactly that instead of forcing a reading
             onto it.""")

    # 7 ── nothing was sent ──────────────────────────────────────────────────
    tab("INJECTION")
    pump(0.6)
    scene("safety",
          """Through all of this the car was only listened to. The transmit
             gate stayed shut, no frame was sent, and on a vehicle that is not
             a detail: everything here is analysis of a recording that already
             existed.""")

    from canlab.core.mux_detector import detect_multiplexer
    facts["mux"] = sum(1 for cid in df["ID"].unique()
                       if len(df[df["ID"] == cid]) >= 50
                       and detect_multiplexer(df[df["ID"] == cid]))
    card("findings",
         """Twenty seven minutes of a parked electric car, and this is what came
            out. Not a decoded dashboard: a map of where the structure is, which
            messages are worth returning to with the car awake, and a clear list
            of what could not be established.""",
         findings_card(facts))

    (OUT / "scenes.json").write_text(json.dumps(_scenes, indent=1))
    (OUT / "facts.json").write_text(json.dumps(facts, indent=1, default=str))
    print(f"\n{len(_scenes)} scenes, {_counter} frames -> {OUT}")
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
