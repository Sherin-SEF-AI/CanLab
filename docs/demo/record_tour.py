#!/usr/bin/env python3
"""Record the guided tour: one still per beat, plus what to point at.

The other two recorders capture full-window stills and let ``build.py`` play
them as a slideshow. This one also records, for each beat, the exact rectangle
of the control being talked about, taken from the live widget with
``mapTo(window, ...)``. ``compose.py`` uses it to push in on that control, dim
the rest and draw a ring around it.

Taking the geometry from the widget rather than measuring a screenshot means a
control that moves in a later build takes its callout with it, instead of
leaving a label pointing at empty panel.

    QT_QPA_PLATFORM=offscreen python docs/demo/record_tour.py [data-dir]
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

from PyQt6.QtCore import QPoint, QRect, QSettings                 # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QTabWidget  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "build-tour"
FRAMES = OUT / "stills"
DATA = Path(sys.argv[1] if len(sys.argv) > 1
            else Path.home() / "Desktop" / "CanLab-real-data-new")
WIDTH, HEIGHT = 1920, 1080

_cfg = tempfile.mkdtemp(prefix="canlab-tour-")
for _fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
    QSettings.setPath(_fmt, QSettings.Scope.UserScope, _cfg)

for _name in ("information", "warning", "critical", "question", "about"):
    setattr(QMessageBox, _name, staticmethod(lambda *a, **k: None))

app = QApplication(sys.argv[:1])

from canlab.theme import QSS, mono_font                           # noqa: E402

app.setStyleSheet(QSS)
app.setFont(mono_font())

from canlab.core.log_parser import parse_log_file                 # noqa: E402
from canlab.core.state import get_state                           # noqa: E402
from canlab.mainwindow import MainWindow                          # noqa: E402

state = get_state()
window = MainWindow()
window.showNormal()
window.setGeometry(QRect(0, 0, WIDTH, HEIGHT))

_shots: list[dict] = []
_counter = 0


def pump(seconds: float = 0.2) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(predicate, timeout: float = 90.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def rect_of(widget) -> dict | None:
    """The widget's rectangle in window coordinates, or None if not visible."""
    if widget is None or not widget.isVisible():
        return None
    top_left = widget.mapTo(window, QPoint(0, 0))
    size = widget.size()
    if size.width() <= 1 or size.height() <= 1:
        return None
    return {"x": top_left.x(), "y": top_left.y(),
            "w": size.width(), "h": size.height()}


def shot(key: str, narration: str, *, focus=None, caption: str = "",
         seconds: float | None = None) -> None:
    """Capture one beat.

    `focus` is a live widget, or a (x, y, w, h) tuple for a region that is not
    one widget, such as a block of a table.
    """
    global _counter
    pump(0.25)
    _counter += 1
    name = f"{_counter:04d}.png"
    window.grab().save(str(FRAMES / name))
    if isinstance(focus, tuple):
        area = {"x": focus[0], "y": focus[1], "w": focus[2], "h": focus[3]}
    else:
        area = rect_of(focus)
    _shots.append({
        "key": key,
        "still": name,
        "narration": " ".join(narration.split()),
        "caption": caption,
        "focus": area,
        "seconds": seconds,
    })
    marker = "focus" if area else "wide "
    print(f"  {marker}  {key}", flush=True)


def tab(name: str):
    for i in range(window.tabs.count()):
        if window.tabs.tabText(i).split()[0] == name.split()[0]:
            window.tabs.setCurrentIndex(i)
            pump(0.35)
            return window.tabs.widget(i)
    raise KeyError(name)


def subtab(widget, label: str):
    inner = widget.findChild(QTabWidget)
    if inner is None:
        raise KeyError(f"{type(widget).__name__} has no inner tab widget")
    for i in range(inner.count()):
        if inner.tabText(i).upper().startswith(label.upper()):
            inner.setCurrentIndex(i)
            pump(0.35)
            return inner.widget(i)
    have = [inner.tabText(i) for i in range(inner.count())]
    raise KeyError(f"no sub-tab starting with {label!r} in {have}")


def load(name: str):
    df = parse_log_file(str(DATA / name))
    state.load_frames(df, name)
    pump(0.6)
    return df


def main() -> int:
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)
    missing = [n for n in ("canedge_c.MF4", "canedge_nissan.MF4")
               if not (DATA / n).is_file()]
    if missing:
        raise SystemExit(f"missing capture(s) in {DATA}: {missing}")

    bar = window.workspace_bar

    # 1 ── what this is ──────────────────────────────────────────────────────
    df = load("canedge_c.MF4")
    tab("FRAMES")
    shot("open",
         """CanLab reverse-engineers a CAN bus. This is a real recording from a
            CANedge logger, nine thousand six hundred frames across fifty
            arbitration identifiers, and every one of them is a twenty-nine bit
            extended identifier, because this is a heavy vehicle running J1939
            rather than a car.""",
         seconds=9.0)

    shot("workspaces",
         """The work is grouped into five workspaces rather than sixteen tabs in
            a row. Capture is what is on the wire, Explore is what it looks
            like over time, Detect is the automatic analysis, Define is where
            you write down what you worked out, and Bus is everything that
            talks back to the vehicle.""",
         focus=bar, caption="Five workspaces, not sixteen tabs", seconds=11.0)

    # 2 ── the sniffer ───────────────────────────────────────────────────────
    sniffer = tab("SNIFFER")
    window.sniffer_tab._tick()
    shot("sniffer",
         """The frames table is a log, which is the wrong shape for the question
            you actually ask at the bench. The sniffer collapses the bus to one
            row per message: a byte turns green when it rises and red when it
            falls, and bytes that never move stay dim.""",
         focus=(window.sniffer_tab.table.mapTo(window, QPoint(0, 0)).x(),
                window.sniffer_tab.table.mapTo(window, QPoint(0, 0)).y(),
                760, 300),
         caption="One row per message, coloured by what just moved",
         seconds=10.0)

    shot("notch",
         """Notch is why this beats scrolling. It records every bit that is
            moving right now and ignores it from then on, so the counters and
            checksums that never stop go quiet and the next thing that lights
            up is the thing you did.""",
         focus=window.sniffer_tab.btn_notch,
         caption="Notch: ignore everything already moving", seconds=9.5)

    window.sniffer_tab._notch()
    window.sniffer_tab._tick()
    shot("notched",
         """One press masked four hundred and sixty four bits of steady traffic
            on this log.""",
         seconds=4.0)

    # 3 ── detection ─────────────────────────────────────────────────────────
    auto = tab("AUTO-RE")
    window.auto_re_tab._run_counter_checksum()
    wait_until(lambda: window.auto_re_tab.ctr_table.rowCount() > 0, 120)
    shot("detect",
         """The detectors run over the capture with no knowledge of the vehicle
            at all. Rolling counters, checksum bytes and the algorithm that
            reproduces them, bit level flags, enumerated bytes, field
            boundaries from entropy, and multiplexed messages.""",
         focus=window.auto_re_tab.ctr_table,
         caption="Twenty-four counters found, with the algorithm for each",
         seconds=11.0)

    subtab(auto, "ENTROPY")
    window.auto_re_tab._run_entropy()
    wait_until(lambda: window.auto_re_tab.entropy_table.rowCount() > 0, 120)
    shot("entropy",
         """Entropy per bit finds where one field ends and the next begins.
            Forty-two candidate boundaries here, each with a confidence that is
            a match fraction over the frames loaded, not a proof.""",
         focus=window.auto_re_tab.entropy_table,
         caption="Field boundaries, ranked by confidence", seconds=9.0)

    # 4 ── defining a signal ─────────────────────────────────────────────────
    busiest = df["ID"].value_counts().index[0]
    sig = {"message_id": busiest, "message_name": f"PGN_{busiest}",
           "signal_name": "ENGINE_SPEED", "start_bit": 24, "length": 16,
           "byte_order": "little", "value_type": "unsigned",
           "scale": 0.125, "offset": 0.0, "min_val": 0, "max_val": 8031.875,
           "unit": "rpm", "description": "drafted against a real J1939 message"}
    state.add_dbc_signal(sig)
    pump(0.4)
    dbc = tab("DBC")
    window.dbc_tab._on_list_select(0)
    pump(0.4)
    shot("dbc",
         """A signal is a message, a start bit, a length and a scale. The bit
            grid shows exactly which bits it claims, and the preview decodes
            real frames through it as you type, so a definition is checked
            against the bus rather than against your arithmetic.""",
         focus=window.dbc_tab.preview_table,
         caption="Live decode of real frames, as you edit", seconds=11.0)

    shot("bitgrid",
         """Drag across the grid to claim bits. Motorola byte order and
            non-contiguous layouts are handled by the same coordinate map the
            exporter uses, so what you select is what gets written out.""",
         focus=window.dbc_tab.bit_editor,
         caption="Drag to claim bits; Motorola layouts included", seconds=9.0)

    # 5 ── seeing it ─────────────────────────────────────────────────────────
    tab("PLOT")
    window.plot_tab._add_signal(f"{busiest}:dbc:ENGINE_SPEED", "dbc", busiest, sig)
    window.plot_tab._add_signal(f"{busiest}:B3", "byte", busiest, "B3")
    pump(1.0)
    shot("plot",
         """Plotted against time is where a definition is confirmed or refuted.
            A physical quantity moves smoothly; a sawtooth means the byte order
            is wrong. Raw bytes and decoded signals share the axis, each with
            its own scale.""",
         seconds=9.0)

    # 6 ── the transmit gate ─────────────────────────────────────────────────
    tab("INJECTION")
    shot("arm",
         """Everything that can put a frame on a wire is behind one gate. ARM
            TX is off by default and it covers injection, replay, fuzzing,
            gateway forwarding and every diagnostic request. Disarming stops a
            running worker within two seconds.""",
         focus=window._toolbar.widgetForAction(window._act_arm),
         caption="Nothing transmits until this is armed", seconds=11.0)

    shot("inject",
         """The injection page itself is three rows: which signal, what value,
            and how often. Checksum and counter are applied from the vehicle
            profile, so a frame the ECU will accept does not have to be
            assembled by hand.""",
         focus=window.injection_tab.sig_combo,
         caption="Pick a signal, set a value, send once or on a loop",
         seconds=10.0)

    # 7 ── assistants and adapters ───────────────────────────────────────────
    shot("services",
         """Three things can run alongside: a REST API, an MCP server that lets
            an assistant drive the analysis, and the plugin list. None of the
            twenty-five assistant tools can transmit; putting frames on a wire
            stays behind the gate where a person is watching.""",
         focus=window._toolbar.widgetForAction(window._act_mcp),
         caption="REST and MCP: read and analyse, never transmit",
         seconds=11.0)

    shot("adapter",
         """Hardware is picked here. Every python-can backend is available, plus
            GVRET, which CanLab adds itself for the boards SavvyCAN runs on.
            Detect finds what is plugged in, and Test opens the adapter and
            listens for a second without transmitting.""",
         focus=window.adapter_combo,
         caption="Any python-can backend, plus GVRET", seconds=11.0)

    # 8 ── scale ─────────────────────────────────────────────────────────────
    load("canedge_nissan.MF4")
    tab("SNIFFER")
    window.sniffer_tab._clear()
    window.sniffer_tab._tick()
    shot("scale",
         """A second recording: twenty-three minutes from a two-channel logger,
            one hundred and fifty five thousand frames. Both channels keep
            their bus tags, and the sniffer folds the whole thing into sixteen
            rows in half a second.""",
         seconds=9.0)

    shot("close",
         """Load a capture, work out which bytes carry what, write it down, and
            export a DBC other tools can read. Everything shown here ran
            against real recordings from hardware this project did not
            produce.""",
         seconds=8.0)

    (OUT / "shots.json").write_text(json.dumps(_shots, indent=1))
    with_focus = sum(1 for s in _shots if s["focus"])
    print(f"\n{len(_shots)} shots, {with_focus} with a callout, "
          f"{sum(s['seconds'] for s in _shots):.0f}s of narration -> {OUT}")
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
