#!/usr/bin/env python3
"""Record the CanLab feature demo: drive the real app and capture 1080p frames.

Runs headless (Qt offscreen), drives the actual MainWindow through a scripted
tour, and writes one PNG per beat plus a scenes.json describing the narration.
``build.py`` turns those into the narrated, subtitled video.

    QT_QPA_PLATFORM=offscreen python docs/demo/record.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect                      # noqa: E402
from PyQt6.QtWidgets import (QApplication, QMessageBox,  # noqa: E402
                             QTabWidget)

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "build"
FRAMES = OUT / "frames"
SAMPLE = REPO / "canlab" / "sample_data" / "sample_kona_drive.csv"
WIDTH, HEIGHT = 1920, 1080

# Headless: a modal dialog would block the recording forever.
for _name in ("information", "warning", "critical", "question", "about"):
    setattr(QMessageBox, _name, staticmethod(lambda *a, **k: None))

app = QApplication(sys.argv)

from canlab.core import safety                          # noqa: E402
from canlab.core.log_parser import parse_log_file       # noqa: E402
from canlab.core.state import get_state                 # noqa: E402
from canlab.mainwindow import MainWindow                # noqa: E402

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


def wait_until(predicate, timeout: float = 30.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def shot(count: int = 1, gap: float = 0.25) -> None:
    """Capture `count` frames of the window, pumping the event loop between."""
    global _counter
    for i in range(count):
        if i:
            pump(gap)
        else:
            pump(0.1)
        pixmap = window.grab()
        _counter += 1
        path = FRAMES / f"{_counter:05d}.png"
        pixmap.save(str(path))
        _current.append(path.name)


def scene(key: str, title: str, narration: str):
    """Start a new scene; frames captured after this belong to it."""
    global _current
    _current = []
    _scenes.append({"key": key, "title": title,
                    "narration": " ".join(narration.split()),
                    "frames": _current})
    print(f"  scene: {key}", flush=True)


def tab(name: str) -> None:
    """Switch to a top-level tab by its label."""
    for i in range(window.tabs.count()):
        if window.tabs.tabText(i).split()[0] == name.split()[0]:
            window.tabs.setCurrentIndex(i)
            pump(0.3)
            return
    raise KeyError(name)


def subtab(widget, label: str) -> None:
    """Switch an inner QTabWidget to the tab whose label starts with `label`.

    Raises on a miss. This used to return quietly, and a single mistyped name
    ("SEQUENCE" against a page called "TEST SEQUENCE") meant that scene of the
    published walkthrough filmed whichever page happened to be open instead.
    A recorder that silently films the wrong thing is worse than one that
    stops.
    """
    inner = widget.findChild(QTabWidget)
    if inner is None:
        raise KeyError(f"{type(widget).__name__} has no inner tab widget")
    for i in range(inner.count()):
        if inner.tabText(i).upper().startswith(label.upper()):
            inner.setCurrentIndex(i)
            pump(0.3)
            return
    have = [inner.tabText(i) for i in range(inner.count())]
    raise KeyError(f"no sub-tab starting with {label!r} in {have}")


WHEEL_SPEED = {
    "message_id": "0A6", "message_name": "WHL_SPD11", "signal_name": "WhlSpdFL",
    "start_bit": 7, "length": 16, "byte_order": "big", "value_type": "unsigned",
    "scale": 0.03125, "offset": 0.0, "min_val": 0, "max_val": 500,
    "unit": "km/h", "description": "Front-left wheel speed",
}
STEER = {
    "message_id": "018", "message_name": "MDPS12", "signal_name": "SteeringAngle",
    "start_bit": 7, "length": 16, "byte_order": "big", "value_type": "unsigned",
    "scale": 0.1, "offset": -1800.0, "min_val": -600, "max_val": 600,
    "unit": "deg", "description": "Steering wheel angle",
}


# ── the tour ────────────────────────────────────────────────────────────────

def record() -> None:
    # 1 ─────────────────────────────────────────────────────────────────────
    scene("load", "Loading a capture", """
        This is CanLab, a workstation for reverse engineering a CAN bus.
        We start by loading a capture. This one is ten seconds of traffic in
        SavvyCAN format, about six and a half thousand frames. The FRAMES tab
        is the raw view: every frame in time order, with its timestamp,
        arbitration ID, bus, length, the eight data bytes, and the gap since
        the last frame with the same ID. A byte lights up green when it
        changed, so you can see at a glance which parts of a message carry
        something that moves.
    """)
    tab("FRAMES")
    state.load_frames(parse_log_file(str(SAMPLE)), "sample_kona_drive.csv")
    wait_until(lambda: len(state.frames_df) > 6000)
    pump(1.2)
    shot(3, 0.4)

    # 2 ─────────────────────────────────────────────────────────────────────
    scene("ids", "The ID panel and inspector", """
        On the left, every arbitration ID the capture contains, grouped by bus,
        with how often it appears and how many frames there are. Ten IDs here,
        from one hertz up to a hundred. Selecting an ID opens it in the
        inspector on the right: the last ten frames in hex, a per-byte activity
        bar showing which bytes actually change, and minimum, maximum and mean
        for each byte. That is usually the first question you ask of an unknown
        message.
    """)
    state.select_id("0A6")
    pump(1.0)
    shot(2, 0.5)

    # 3 ─────────────────────────────────────────────────────────────────────
    scene("signals", "Byte-role classification", """
        The SIGNALS tab classifies every message for you. It runs offline, with
        no API key and nothing leaving the machine. For each ID it reports the
        frame count, the rate, the entropy of the payload, and a suspected
        role: a sensor carrying a physical value, a status message, a counter,
        or a diagnostic response. These are heuristics that point you at the
        interesting messages first, not identifications to trust blindly.
    """)
    tab("SIGNALS")
    window.signals_tab._run_classify()
    wait_until(lambda: window.signals_tab.table.rowCount() > 0, 60)
    pump(0.8)
    shot(2, 0.5)

    # 4 ─────────────────────────────────────────────────────────────────────
    scene("autore", "Finding counters and checksums", """
        AUTO-RE does the tedious part. It sweeps every message at once, looking
        for rolling counters, bytes that increment each frame and wrap, and for
        checksum bytes, by testing whether a byte is the sum, the exclusive or,
        or the nibble sum of the others. A match only counts if it beats simply
        guessing the byte's most common value, so padding does not read as a
        checksum, and a relation that holds for every byte of a message is
        thrown away rather than reported, because it localises nothing.
        Counters and checksums are not signals, and knowing which bytes they
        occupy removes them from the search.
    """)
    tab("AUTO-RE")
    window.auto_re_tab._run_counter_checksum()
    wait_until(lambda: window.auto_re_tab.ctr_table.rowCount() > 0, 90)
    pump(1.0)
    shot(2, 0.5)

    # 4b ────────────────────────────────────────────────────────────────────
    scene("guesser", "Identifying the algorithm", """
        That sweep tells you which byte. To find out which algorithm, the
        CHECKSUM GUESSER takes one message and one byte and tries all twelve:
        plain and inverted sums, two's complement, nibble sums, CRC-8 in its
        SAE J1850 and AUTOSAR forms, and the manufacturer variants used by
        Hyundai, Toyota, Honda and Subaru. It fits on the first seventy per
        cent of the capture and validates on the rest, and reports both
        numbers, so an algorithm that merely memorised the training frames is
        visible as a high training score with a poor validation score.
    """)
    subtab(window.auto_re_tab, "CHECKSUM GUESSER")
    window.auto_re_tab._refresh_guesser_ids()
    for i in range(window.auto_re_tab.guesser_id_combo.count()):
        if window.auto_re_tab.guesser_id_combo.itemData(i) == "018":
            window.auto_re_tab.guesser_id_combo.setCurrentIndex(i)
            break
    window.auto_re_tab.guesser_byte_spin.setValue(7)
    pump(0.4)
    window.auto_re_tab._run_guesser()
    pump(1.0)
    shot(2, 0.5)

    # 5 ─────────────────────────────────────────────────────────────────────
    scene("entropy", "Where signals begin and end", """
        The same tab measures per-bit entropy across a message to suggest where
        one signal ends and the next begins. Bits that never change are padding;
        bits that change constantly belong together in a value. It is a
        suggestion for the boundaries, which you then confirm by decoding.
    """)
    subtab(window.auto_re_tab, "ENTROPY")
    window.auto_re_tab._run_entropy()
    wait_until(lambda: window.auto_re_tab.entropy_table.rowCount() > 0, 90)
    pump(0.8)
    shot(2, 0.5)

    # 6 ─────────────────────────────────────────────────────────────────────
    scene("dbc", "Defining a signal", """
        Now we define a signal. The DBC BUILDER is where a guess becomes a
        definition. We are describing the front-left wheel speed on message
        0A6: sixteen bits, big-endian, starting at bit seven, scaled by one
        thirty-second of a kilometre per hour. The bit grid underneath shows
        the live payload with the selected field highlighted, and it converts
        between the grid and DBC bit numbering for you, which is the part
        everybody gets wrong by hand.
    """)
    tab("DBC BUILDER")
    state.add_dbc_signal(dict(WHEEL_SPEED))
    pump(0.6)
    window.dbc_tab._on_list_select(0)
    pump(0.8)
    shot(2, 0.5)

    # 7 ─────────────────────────────────────────────────────────────────────
    scene("decode", "Decoding, verified live", """
        Underneath the editor is the live decode preview: real frames from the
        capture, their raw bytes, and the physical value your definition
        produces. Sixty to eighty kilometres per hour here, which is the right
        order of magnitude for a wheel speed, so the scale is plausible. Every
        decode in CanLab runs through cantools, the same library that will read
        the file you export, so what you see here is what you get later.
    """)
    shot(2, 0.6)

    # 8 ─────────────────────────────────────────────────────────────────────
    scene("plot", "Plotting the decoded signal", """
        The PLOT tab draws it over time. Raw bytes and decoded signals can be
        plotted together on a shared time axis, each with its own scale, so you
        can compare a decoded value against the bytes it came from, or against
        a different message entirely. A wheel speed that rises and falls
        smoothly is a good sign the definition is right; a sawtooth would tell
        you the byte order is wrong.
    """)
    state.add_dbc_signal(dict(STEER))
    pump(0.4)
    tab("PLOT")
    pump(0.6)
    window.plot_tab._add_signal("0A6:dbc:WhlSpdFL", "dbc", "0A6", WHEEL_SPEED)
    pump(0.5)
    window.plot_tab._add_signal("0A6:B0", "byte", "0A6", "B0")
    pump(1.0)
    shot(2, 0.6)

    # 9 ─────────────────────────────────────────────────────────────────────
    scene("intelligence", "Correlating messages", """
        INTELLIGENCE looks across messages rather than within one. It measures
        how often each ID repeats, so you can tell a hundred-hertz control
        message from a one-hertz status report. It correlates bytes between
        different IDs with a lag sweep, which is how you find the same physical
        quantity reported by two ECUs. It diffs two captures to show what
        changed when you pressed a button. And it decodes J1939 parameter group
        numbers for heavy vehicles.
    """)
    tab("INTELLIGENCE")
    window.intelligence_tab._compute_periodicity()
    pump(0.8)
    shot(2, 0.5)

    # 10 ────────────────────────────────────────────────────────────────────
    scene("ml", "Machine-learning analysis", """
        ML INTEL goes further per byte. It classifies each byte as a counter, a
        checksum, a boolean flag, a physical value or padding, with a confidence
        and the entropy behind it. It fits a baseline from normal traffic and
        then scores frames for anomalies, which is how you find the one message
        that behaves differently when a fault is present. It also detects
        change points and finds messages with similar behaviour by embedding.
    """)
    tab("ML INTEL")
    if window.ml_intel_tab.id_list.count():
        window.ml_intel_tab.id_list.setCurrentRow(0)
        pump(0.3)
    window.ml_intel_tab._analyze_selected()
    wait_until(lambda: window.ml_intel_tab.roles_table.rowCount() > 0, 60)
    pump(0.8)
    shot(2, 0.5)

    # 11 ────────────────────────────────────────────────────────────────────
    scene("dashboard", "Seeing the whole bus at once", """
        The DASHBOARD is the overview. The heatmap shows how much each byte of
        each message moves across the capture, so a dense column is a message
        worth investigating and a blank one is padding. There is a message
        timeline showing when each ID is active, and gauges you can point at
        any signal you have defined, so a decoded value can be watched as an
        instrument rather than a number.
    """)
    tab("DASHBOARD")
    window.dashboard_tab._render_heatmap()
    wait_until(lambda: True, 1)
    pump(1.5)
    shot(2, 0.5)

    # 12 ────────────────────────────────────────────────────────────────────
    scene("timeline", "Scrubbing through time", """
        TIMELINE is for questions about when. Several signals are stacked on one
        scrubbable axis with a shared playhead, so you can line up the moment a
        flag flips against the moment a value starts moving. It also syncs to a
        video of the dashboard or the road, with an adjustable offset, so you
        can match what the bus did to what the car was doing.
    """)
    tab("TIMELINE")
    pump(0.5)
    for i in range(min(3, window.timeline_tab.sig_list.count())):
        window.timeline_tab.sig_list.item(i).setSelected(True)
    window.timeline_tab._plot_selected()
    pump(1.0)
    shot(2, 0.5)

    # 13 ────────────────────────────────────────────────────────────────────
    scene("codegen", "Generating code", """
        Once the definitions are right, CODE GEN turns them into a working
        program. Pick the signals, the interface and the direction, and it
        writes Python that opens the bus and decodes those signals, or encodes
        and sends them. The generated encoder goes through cantools and applies
        the counter and checksum convention of whichever vehicle profile you
        have selected, so the frames it produces are properly formed.
    """)
    tab("CODE GEN")
    pump(0.4)
    window.codegen_tab._generate()
    pump(0.8)
    shot(2, 0.5)

    # 14 ────────────────────────────────────────────────────────────────────
    scene("exports", "Exporting to other tools", """
        Your work leaves in whatever format the next tool needs. A standard DBC
        that cantools and Vector tools read. An openpilot DBC with the comma
        dot ai checksum and counter annotations. Vector CANdb++ with the cycle
        times measured from your own capture. AUTOSAR ARXML four point three.
        And a Wireshark Lua dissector, so your signals appear by name in a
        packet capture. Each of these is verified in the test suite by loading
        it back with the tool that has to read it.
    """)
    tab("DBC BUILDER")
    pump(0.5)
    shot(2, 0.5)

    # 15 ────────────────────────────────────────────────────────────────────
    scene("diagnostics", "Talking to ECUs", """
        DIAGNOSTICS is the request side. UDS, ISO 14229: read diagnostic
        trouble codes, read ECU identification, and scan which services an ECU
        supports. That scan is read-only by default, because probing a service
        like ECU Reset or Clear Diagnostics on a live bus does exactly what the
        name says. There is OBD-II mode one with automatic PID discovery,
        J1939, a bus health monitor, XCP for reading ECU memory, and DoIP for
        diagnostics over IP.
    """)
    tab("DIAGNOSTICS")
    pump(0.6)
    shot(1)
    subtab(window.diagnostics_tab, "UDS SERVICES")
    shot(1)
    subtab(window.diagnostics_tab, "XCP")
    shot(1)
    subtab(window.diagnostics_tab, "DoIP")
    shot(1)

    # 16 ────────────────────────────────────────────────────────────────────
    scene("security", "Security access", """
        Security access, service 0x27, is the seed and key exchange that unlocks
        a protected session. CanLab requests a seed and tries the common
        algorithms, or your own key function from a script. Brute force is
        available for short keys, rate limited, and it stops the moment the ECU
        reports that the attempt limit is reached, because tripping that counter
        can lock a module until it is power cycled, or permanently.
    """)
    subtab(window.diagnostics_tab, "SECURITY")
    pump(0.5)
    shot(2, 0.5)

    # 17 ────────────────────────────────────────────────────────────────────
    scene("safety", "The transmit gate", """
        Everything so far only listened. Before anything can be sent, there is
        one gate. ARM TX is off by default and nothing leaves the tool while it
        is off: not injection, not replay, not fuzzing, not gateway forwarding,
        and not diagnostic requests either. The status bar says so. Turning it
        on requires confirming a warning that names the risk, and turning it off
        again does not merely block the next frame, it stops anything already
        transmitting.
    """)
    tab("INJECTION")
    safety.set_armed(False)
    pump(0.4)
    shot(1)
    window.injection_tab._refresh_signal_list()
    if window.injection_tab.sig_combo.count():
        window.injection_tab.sig_combo.setCurrentIndex(0)
    window.injection_tab._send_once()
    pump(0.6)
    shot(2, 0.5)

    # 18 ────────────────────────────────────────────────────────────────────
    scene("injection", "Injection, replay and fuzzing", """
        With transmit armed, and only on an isolated bench, the INJECTION tab
        can send. Set a signal to a physical value and send it once, or loop it
        at a chosen period with the counter and checksum kept correct. Replay a
        capture back onto the bus at real speed or faster, with a scrubber.
        Define trigger rules that fire on a byte condition. Sweep an actuator
        between limits with a watchdog that aborts if a message goes silent.
        And run scripted test sequences of inject, wait and assert steps.
    """)
    safety.set_armed(True)
    pump(0.4)
    shot(1)
    subtab(window.injection_tab, "REPLAY")
    shot(1)
    subtab(window.injection_tab, "FUZZ")
    shot(1)
    subtab(window.injection_tab, "TEST SEQUENCE")
    shot(1)
    safety.set_armed(False)

    # 19 ────────────────────────────────────────────────────────────────────
    scene("gateway", "Man in the middle", """
        The GATEWAY tab bridges two CAN channels and puts you in between. Rules
        are applied in order: pass a message through, block it, or modify a byte
        or the arbitration ID as it crosses. That is how you test what an ECU
        does when a message it depends on disappears, or arrives with a
        different value. It needs two hardware channels, and like everything
        else it will not forward a single frame until transmit is armed.
    """)
    tab("GATEWAY")
    pump(0.6)
    shot(2, 0.5)

    # 20 ────────────────────────────────────────────────────────────────────
    scene("obd", "Live OBD-II", """
        The OBD-II tab is the standardised subset any car will answer. It
        discovers which PIDs the vehicle supports, walking the continuation
        windows rather than assuming the first thirty-two, and then polls the
        ones you pick and shows them as live gauges: engine speed, coolant
        temperature, road speed, throttle position.
    """)
    tab("OBD-II")
    pump(0.6)
    shot(2, 0.5)

    # 21 ────────────────────────────────────────────────────────────────────
    scene("ai", "Optional AI assistance", """
        The AI ENGINE is optional and off unless you configure it. It sends one
        message ID's statistics to Anthropic, to Groq, or to a local Ollama
        model that never leaves your machine, and asks for an interpretation.
        What makes it useful is that the offline analysis goes with the
        question: the byte roles, the detected checksum, the message period. The
        model reasons about measured facts rather than raw hex. Its answers are
        still suggestions, and accepting one into the DBC is refused unless it
        actually states a bit position.
    """)
    tab("AI ENGINE")
    state.select_id("0A6")
    pump(0.8)
    shot(2, 0.5)

    # 22 ────────────────────────────────────────────────────────────────────
    scene("live", "Live capture", """
        Connected to a real bus, frames stream in live. One receive thread reads
        the adapter and hands frames to everyone who needs them, so a
        diagnostic scan and the live table never steal responses from each
        other. Frames go into a capped buffer that appends in constant time, so
        a busy bus does not slow the tool down as the log grows: the frame
        counter and the rate keep climbing while the table stays responsive.
    """)
    import can
    channel = "canlab-demo"
    window._can_settings = {"interface": "virtual", "channel": channel,
                            "bitrate": 500000, "fd": False, "data_bitrate": None}
    vehicle = can.interface.Bus(channel=channel, interface="virtual")
    stop = threading.Event()

    def traffic():
        i = 0
        while not stop.is_set():
            vehicle.send(can.Message(arbitration_id=0x1A0 + (i % 6),
                                     data=bytes([(i + k) & 0xFF for k in range(8)]),
                                     is_extended_id=False))
            i += 1
            time.sleep(0.004)

    tab("FRAMES")
    window._connect_can()
    gen = threading.Thread(target=traffic, daemon=True)
    gen.start()
    pump(1.0)
    shot(10, 0.30)          # watch it stream
    stop.set()
    gen.join(timeout=2)
    window._disconnect_can()
    vehicle.shutdown()
    pump(0.5)

    # 23 ────────────────────────────────────────────────────────────────────
    scene("close", "Where the results go", """
        That is the loop: load a capture, let the offline analysis narrow it
        down, define the signals, verify them by decoding real frames, and
        export to whatever comes next. A project file saves the frames, the
        signal definitions and your notes together. A REST API and an MCP
        server expose the same analysis to scripts and to agents. And the one
        rule that does not change is the transmit gate: it is off until you
        turn it on, and you should only turn it on when the bus you are
        connected to is on a bench.
    """)
    tab("DBC BUILDER")
    pump(0.5)
    shot(2, 0.6)


def main() -> int:
    # Clear only the frames. build/ also holds the synthesised narration, and
    # build.py reuses each clip whose words have not changed -- wiping the whole
    # directory here would re-read eleven minutes of speech on every re-record.
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)
    print(f"recording to {OUT}")
    started = time.monotonic()
    record()
    (OUT / "scenes.json").write_text(json.dumps(_scenes, indent=2))
    total = sum(len(s["frames"]) for s in _scenes)
    print(f"captured {total} frames across {len(_scenes)} scenes "
          f"in {time.monotonic() - started:.0f}s")
    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
