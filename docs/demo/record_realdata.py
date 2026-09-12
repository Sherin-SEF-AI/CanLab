#!/usr/bin/env python3
"""Record the real-data validation run: drive the app over other people's logs.

The feature walkthrough in ``record.py`` uses the bundled sample, which this
project generated. This one uses real device recordings the application has
never seen, in native MDF4 from CANedge hardware, including a 145,000-frame
J1939 log that is 29-bit throughout and a 23-minute two-channel recording.

Runs headless at 1080p and writes one PNG per beat plus scenes.json, which
``build_realdata.py`` turns into the narrated, subtitled video.

    QT_QPA_PLATFORM=offscreen python docs/demo/record_realdata.py [data-dir]
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

from PyQt6.QtCore import QRect, QSettings                    # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QTabWidget   # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "build-realdata"
FRAMES = OUT / "frames"
DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-1000/newdata")
WIDTH, HEIGHT = 1920, 1080

# Never touch the real user configuration while recording.
_cfg = tempfile.mkdtemp(prefix="canlab-record-")
for _fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
    QSettings.setPath(_fmt, QSettings.Scope.UserScope, _cfg)

# Headless: a modal dialog would block the recording forever.
for _name in ("information", "warning", "critical", "question", "about"):
    setattr(QMessageBox, _name, staticmethod(lambda *a, **k: None))

app = QApplication(sys.argv[:1])

from canlab.theme import QSS, mono_font                      # noqa: E402

app.setStyleSheet(QSS)
app.setFont(mono_font())

from canlab.core.log_parser import parse_log_file            # noqa: E402
from canlab.core.state import get_state                      # noqa: E402
from canlab.mainwindow import MainWindow                     # noqa: E402

state = get_state()
window = MainWindow()
window.showNormal()
window.setGeometry(QRect(0, 0, WIDTH, HEIGHT))

_counter = 0
_scenes: list[dict] = []
_current: list[str] = []


def pump(seconds: float = 0.15) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(predicate, timeout: float = 60.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def shot(count: int = 1, gap: float = 0.25) -> None:
    global _counter
    for i in range(count):
        pump(gap if i else 0.1)
        _counter += 1
        path = FRAMES / f"{_counter:05d}.png"
        window.grab().save(str(path))
        _current.append(path.name)


def scene(key: str, title: str, narration: str):
    global _current
    _current = []
    _scenes.append({"key": key, "title": title,
                    "narration": " ".join(narration.split()), "frames": _current})
    print(f"  scene: {key}", flush=True)


def tab(name: str) -> None:
    for i in range(window.tabs.count()):
        if window.tabs.tabText(i).split()[0] == name.split()[0]:
            window.tabs.setCurrentIndex(i)
            pump(0.3)
            return
    raise KeyError(name)


def subtab(widget, label: str) -> None:
    inner = widget.findChild(QTabWidget)
    if inner is None:
        return
    for i in range(inner.count()):
        if inner.tabText(i).upper().startswith(label.upper()):
            inner.setCurrentIndex(i)
            pump(0.3)
            return


def load(name: str):
    df = parse_log_file(str(DATA / name))
    state.load_frames(df, name)
    pump(0.6)
    return df


# ── the run ──────────────────────────────────────────────────────────────────

def main() -> int:
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)
    missing = [n for n in ("canedge_c.MF4", "canedge_nissan.MF4", "canedge_big.MF4")
               if not (DATA / n).is_file()]
    if missing:
        raise SystemExit(f"missing capture(s) in {DATA}: {missing}")

    # 1 ──────────────────────────────────────────────────────────────────────
    scene("open", "A real log the application has never seen",
          """This is not the bundled sample. It is a recording from a CANedge
             logger, in native MDF four, published by CSS Electronics. Nine
             thousand six hundred frames across fifty arbitration identifiers,
             and every single one is a twenty-nine bit extended identifier,
             because this is a J1939 vehicle rather than a passenger car. Until
             now the MDF four reader had only ever been given files this
             project wrote itself.""")
    df = load("canedge_c.MF4")
    tab("FRAMES")
    shot(4, 0.4)

    # 2 ──────────────────────────────────────────────────────────────────────
    scene("sniffer", "The sniffer, and the notch",
          """The sniffer collapses the bus to one row per message instead of one
             row per frame. A byte turns green when it rises and red when it
             falls, and fades back after a second. Bytes that have never moved
             stay dim, so what is alive on this bus is visible at a glance.""")
    tab("SNIFFER")
    window.sniffer_tab._tick()
    shot(4, 0.4)

    scene("notch", "Silencing what is already moving",
          """Notch is the reason to use this view. It records every bit that is
             currently in motion and ignores it from then on. On this log that
             masks four hundred and sixty four bits of counters and checksums
             in one press, and the display goes quiet. From that point the only
             thing that lights up is something you caused.""")
    window.sniffer_tab._notch()
    window.sniffer_tab._tick()
    shot(3, 0.4)

    # 3 ──────────────────────────────────────────────────────────────────────
    scene("detect", "Every detector on unfamiliar traffic",
          """The offline detectors run over the real log with no prior
             knowledge of the vehicle: rolling counters, checksum bytes and the
             algorithm that reproduces them, bit level flags, enumerated bytes,
             field boundaries from bit entropy, and multiplexed messages. On
             this capture that is twenty four counters, six flags, fourteen
             enumerations, forty two field boundaries and four multiplexed
             messages.""")
    tab("AUTO-RE")
    window.auto_re_tab._run_counter_checksum()
    wait_until(lambda: window.auto_re_tab.ctr_table.rowCount() > 0, 120)
    shot(3, 0.4)

    scene("flags", "Bit-level flags and enumerated bytes",
          """The same sweep goes below byte level: single bit switches and small
             packed fields, and bytes that behave as enumerations rather than
             measurements. On a real J1939 log that is six flags and fourteen
             enumerated bytes, each with the states actually observed.""")
    subtab(window.auto_re_tab, "FLAGS")
    if hasattr(window.auto_re_tab, "_run_flags"):
        window.auto_re_tab._run_flags()
        wait_until(lambda: window.auto_re_tab.flags_table.rowCount() > 0, 120)
    shot(3, 0.4)

    scene("entropy", "Field boundaries from bit entropy",
          """Entropy per bit finds where one field ends and the next begins,
             without being told anything about the vehicle. Forty two candidate
             field boundaries on this capture, each with a confidence that is a
             match fraction over the frames loaded, not a proof.""")
    subtab(window.auto_re_tab, "ENTROPY")
    window.auto_re_tab._run_entropy()
    wait_until(lambda: window.auto_re_tab.entropy_table.rowCount() > 0, 120)
    shot(3, 0.4)

    # 4 ──────────────────────────────────────────────────────────────────────
    scene("signals", "Per-message structure",
          """The signals view gives one row per message with its rate, payload
             entropy and a suspected type. On a J1939 bus the identifiers carry
             a parameter group number and a source address, so the shape of the
             traffic is already meaningful before a single signal is
             defined.""")
    tab("SIGNALS")
    shot(3, 0.4)

    # 5 ──────────────────────────────────────────────────────────────────────
    scene("dbc", "A signal on a real twenty-nine bit message",
          """A signal defined on the busiest real message decodes real frames
             through cantools, with the bit grid showing exactly which bits it
             claims. Because the identifier is twenty-nine bit, the exported
             file has to set the extended flag in its frame identifier.
             Exporting this capture is what found a genuine bug: the openpilot
             writer was emitting the bare number, which cantools refuses. Every
             writer is now checked against an extended identifier.""")
    busiest = df["ID"].value_counts().index[0]
    sig_def = {
        "message_id": busiest, "message_name": f"PGN_{busiest}",
        "signal_name": "ENGINE_SPEED", "start_bit": 24, "length": 16,
        "byte_order": "little", "value_type": "unsigned",
        "scale": 0.125, "offset": 0.0, "min_val": 0, "max_val": 8031.875,
        "unit": "rpm", "description": "drafted against a real J1939 message"}
    state.add_dbc_signal(sig_def)
    pump(0.4)
    tab("DBC")
    shot(3, 0.4)

    scene("plot", "Confirming it against the frames",
          """Plotted against time, the decoded signal is what confirms or
             refutes the definition. A physical quantity moves smoothly; a
             sawtooth means the byte order is wrong. This is the step that
             turns a candidate into a signal.""")
    tab("PLOT")
    window.plot_tab._add_signal(f"{busiest}:dbc:ENGINE_SPEED", "dbc", busiest, sig_def)
    window.plot_tab._add_signal(f"{busiest}:B3", "byte", busiest, "B3")
    pump(1.0)
    shot(4, 0.4)

    # 6 ──────────────────────────────────────────────────────────────────────
    scene("formats", "Five formats, one truth",
          """The same real log is written out as SavvyCAN C S V, Vector B L F,
             Vector A S C, candump and socket C A N pcap, then read back by
             every parser. All five have to return the same nine thousand six
             hundred frames, the same fifty identifiers, the same payload bytes
             and the same extended flag. They do.""")
    tab("FRAMES")
    shot(2, 0.4)

    # 7 ──────────────────────────────────────────────────────────────────────
    scene("multibus", "Twenty-three minutes, two channels",
          """A second real recording: twenty-three minutes from a two channel
             logger, one hundred and fifty five thousand frames. Both channels
             keep their bus tags through the reader, which the single bus sample
             could never test. The sniffer folds the whole thing into sixteen
             rows in half a second.""")
    load("canedge_nissan.MF4")
    tab("SNIFFER")
    window.sniffer_tab._clear()
    window.sniffer_tab._tick()
    shot(4, 0.4)

    # 8 ──────────────────────────────────────────────────────────────────────
    scene("j1939", "A hundred and forty five thousand frames",
          """The largest of the real logs: one hundred and forty five thousand
             frames, one hundred and forty two identifiers, extended from end to
             end. It loads, it parses, and every arbitration identifier above
             seven hundred and ninety one is correctly reported as extended
             rather than quietly truncated.""")
    load("canedge_big.MF4")
    tab("FRAMES")
    shot(3, 0.4)

    # 9 ──────────────────────────────────────────────────────────────────────
    scene("safety", "Nothing was transmitted",
          """Through all of this the transmit gate stayed shut. No bus was
             opened, nothing was sent, and none of the twenty five tools the
             assistant interface exposes is able to transmit at all. Ninety
             checks over real data, fifty four of them new, and four hundred and
             forty eight unit tests. Two real bugs found and fixed: the
             openpilot writer on extended identifiers, and the sniffer ageing a
             loaded log against wall clock time.""")
    tab("INJECTION")
    shot(3, 0.4)

    scene("close", "What this run was for",
          """Synthetic data agrees with whatever the code assumes. Real data
             from somebody else's hardware does not, which is the whole point of
             running it.""")
    tab("FRAMES")
    shot(2, 0.4)

    (OUT / "scenes.json").write_text(json.dumps(_scenes, indent=1))
    total = sum(len(s["frames"]) for s in _scenes)
    print(f"\n{len(_scenes)} scenes, {total} frames at {WIDTH}x{HEIGHT} -> {OUT}")
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
