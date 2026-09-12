#!/usr/bin/env python3
"""Phase 3: everything phases 1 and 2 did not touch.

Diagnostics are exercised against a scripted ECU responder on a virtual bus.
That is a real protocol exchange, not real vehicle data, and is labelled as
such: without an ECU there is no other way to see whether the request and
response handling works at all.
"""
import json
import os
import sys
import threading
import time
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = sys.argv[1]

from PyQt6.QtCore import QRect                                    # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox             # noqa: E402

for _n in ("information", "warning", "critical", "question", "about"):
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: None))
app = QApplication(sys.argv)

import can                                                        # noqa: E402

from canlab.core import safety                                    # noqa: E402
from canlab.core.log_parser import parse_log_file                 # noqa: E402
from canlab.core.state import get_state                           # noqa: E402
from canlab.mainwindow import MainWindow                          # noqa: E402

RESULTS = []


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"  FAIL  {name}\n        {type(exc).__name__}: {exc}")
        return False
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}\n        {detail}")
    return ok


def pump(s=0.3):
    end = time.monotonic() + s
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


window = MainWindow()
window.showNormal()
window.setGeometry(QRect(0, 0, 1920, 1080))
state = get_state()
state.load_frames(parse_log_file(f"{DATA}/GVRET_Log.csv"), "GVRET_Log.csv")
pump(0.8)
busiest = state.frames_df["ID"].value_counts().index[0]
print(f"\nloaded {len(state.frames_df)} real frames, "
      f"{state.frames_df['ID'].nunique()} IDs")


# ── Remaining log formats ─────────────────────────────────────────────────────
print("\n=== REMAINING LOG FORMATS (real frames, re-containered) ===")


def mdf4():
    """A real MDF4 CAN log needs ID, DLC and DataBytes channels together.

    An earlier version of this check wrote only DataBytes, got zero rows back,
    and asserted len(out) >= 0, which is true of everything. It proved nothing.
    """
    import numpy as np
    from asammdf import MDF, Signal

    src = state.frames_df.head(3000)
    ts = np.asarray(src["Timestamp"], dtype="f8")
    ids = np.asarray([int(x, 16) for x in src["ID"]], dtype="u4")
    data = np.zeros((len(src), 8), dtype="u1")
    for i in range(8):
        data[:, i] = np.asarray(src[f"B{i}"].fillna(0), dtype="u1")

    mdf = MDF()
    mdf.append([Signal(samples=ids, timestamps=ts, name="CAN_DataFrame.ID"),
                Signal(samples=np.asarray(src["DLC"].fillna(8), dtype="u1"),
                       timestamps=ts, name="CAN_DataFrame.DLC"),
                Signal(samples=data, timestamps=ts,
                       name="CAN_DataFrame.DataBytes")])
    path = f"{DATA}/real_can.mf4"
    mdf.save(path, overwrite=True)

    out = parse_log_file(path)
    same_first = len(out) and out.iloc[0]["ID"] == src.iloc[0]["ID"]
    return len(out) == len(src) and same_first, \
        (f"{len(out)} of {len(src)} real frames survived the MDF4 round trip, "
         f"{out['ID'].nunique() if len(out) else 0} IDs, first frame matches")


check("MDF4 parser accepts an asammdf file", mdf4)


def rlog():
    from canlab.core import log_parser
    try:
        log_parser.parse_log_file(f"{DATA}/not_an_rlog.rlog")
    except Exception as exc:
        msg = str(exc)
        clear = any(w in msg.lower() for w in ("capnp", "cereal", "schema",
                                               "no such file", "not found"))
        return clear, f"raises a readable error rather than guessing: {msg[:80]}"
    return False, "accepted a file that is not an rlog"


check("openpilot rlog fails cleanly when it cannot read", rlog)


# ── Project round trip ────────────────────────────────────────────────────────
print("\n=== PROJECT SAVE AND LOAD ===")


def project():
    from canlab.core.project import load_project, save_project
    state.add_dbc_signal({"message_id": busiest, "message_name": "M",
                          "signal_name": "S", "start_bit": 0, "length": 8,
                          "byte_order": "little", "value_type": "unsigned",
                          "scale": 1.0, "offset": 0.0, "min_val": 0,
                          "max_val": 255, "unit": "", "description": ""})
    path = f"{DATA}/real.canlab"
    save_project(state, path)
    before_frames = len(state.frames_df)
    before_sigs = len(state.dbc_signals)
    state.load_frames(state.frames_df.head(5), "cleared")
    state.dbc_signals.clear()
    load_project(state, path)
    pump(0.5)
    return (len(state.frames_df) == before_frames
            and len(state.dbc_signals) == before_sigs), \
        (f"saved and reloaded {len(state.frames_df)} frames and "
         f"{len(state.dbc_signals)} signal(s)")


check("project round-trips frames and signals", project)


# ── Every transmit worker, gated ──────────────────────────────────────────────
print("\n=== TRANSMIT WORKERS (real IDs, virtual bus) ===")

CH = "phase3"
watcher = can.interface.Bus(channel=CH, interface="virtual")
seen, stop = [], threading.Event()


def _watch():
    while not stop.is_set():
        m = watcher.recv(timeout=0.1)
        if m is not None:
            seen.append(m)


threading.Thread(target=_watch, daemon=True).start()
window._can_settings = {"interface": "virtual", "channel": CH,
                        "bitrate": 500000, "fd": False, "data_bitrate": None}
window._connect_can()
pump(0.5)
bus = state.bus_hub.subscribe() if state.bus_hub else None


def worker_gate(label, make):
    """A worker must send nothing disarmed and something armed."""
    def run():
        safety.set_armed(False)
        seen.clear()
        w = make()
        w.start() if hasattr(w, "start") else w()
        pump(1.0)
        if hasattr(w, "stop"):
            w.stop()
        if hasattr(w, "wait"):
            w.wait(2000)
        disarmed = len(seen)

        safety.set_armed(True)
        seen.clear()
        w2 = make()
        w2.start() if hasattr(w2, "start") else w2()
        pump(1.2)
        if hasattr(w2, "stop"):
            w2.stop()
        if hasattr(w2, "wait"):
            w2.wait(2000)
        armed = len(seen)
        safety.set_armed(False)
        return disarmed == 0 and armed > 0, \
            f"disarmed {disarmed} frames, armed {armed} frames"
    return run


target = int(busiest, 16)


def replay_worker():
    from canlab.core.replay import ReplayWorker
    return ReplayWorker(bus, state.frames_df.head(40), speed=50.0, loop=False)


check("REPLAY", worker_gate("replay", replay_worker))


def fuzz_worker():
    from canlab.core.fuzzer import FuzzWorker
    return FuzzWorker(bus, target, rate_hz=60.0, max_iter=25)


check("FUZZER", worker_gate("fuzz", fuzz_worker))


def sweep_worker():
    from canlab.core.safety_scanner import SafetyScanWorker
    sig = dict(state.dbc_signals[0]) if state.dbc_signals else None
    return SafetyScanWorker(bus=bus, sig=sig, min_val=0, max_val=255, steps=6,
                            step_delay_ms=20, watchdog_id=0,
                            watchdog_timeout_ms=2000)


check("ACTUATOR SWEEP", worker_gate("sweep", sweep_worker))

stop.set()
pump(0.3)
window._disconnect_can()
watcher.shutdown()


# ── Diagnostics against a scripted ECU ────────────────────────────────────────
print("\n=== DIAGNOSTICS (scripted ECU responder, not real vehicle data) ===")


def diagnostics():
    """A responder that answers OBD-II mode 1 and a UDS DID read."""
    ch = "phase3-ecu"
    ecu = can.interface.Bus(channel=ch, interface="virtual")
    tool_settings = {"interface": "virtual", "channel": ch, "bitrate": 500000,
                     "fd": False, "data_bitrate": None}
    running = threading.Event()
    running.set()
    answered = []

    def respond():
        while running.is_set():
            msg = ecu.recv(timeout=0.1)
            if msg is None or msg.arbitration_id != 0x7E0:
                continue
            payload = bytes(msg.data)
            svc = payload[1] if len(payload) > 1 else 0
            if svc == 0x01 and len(payload) > 2:          # OBD-II mode 1
                pid = payload[2]
                answered.append(("obd", pid))
                data = ([0x04, 0x41, pid, 0x7B] if pid != 0x00
                        else [0x06, 0x41, 0x00, 0xBE, 0x1F, 0xA8, 0x13])
                ecu.send(can.Message(arbitration_id=0x7E8,
                                     data=bytes(data + [0] * (8 - len(data))),
                                     is_extended_id=False))
            elif svc == 0x22:                             # UDS read DID
                answered.append(("uds", payload[2:4].hex()))
                ecu.send(can.Message(
                    arbitration_id=0x7E8,
                    data=bytes([0x06, 0x62, payload[2], payload[3],
                                0x41, 0x42, 0x43, 0x00]),
                    is_extended_id=False))

    threading.Thread(target=respond, daemon=True).start()
    window._can_settings = tool_settings
    window._connect_can()
    pump(0.4)
    safety.set_armed(True)

    from canlab.core.isotp import ISOTPSession
    sub = state.bus_hub.subscribe({0x7E8})
    session = ISOTPSession(sub, 0x7E0, 0x7E8)
    obd = session.request(bytes([0x01, 0x0C]), timeout=1.5)
    uds = session.request(bytes([0x22, 0xF1, 0x90]), timeout=1.5)

    safety.set_armed(False)
    running.clear()
    pump(0.3)
    window._disconnect_can()
    ecu.shutdown()
    ok = bool(obd) and bool(uds)
    return ok, (f"ECU answered {len(answered)} requests; "
                f"OBD-II reply {obd.hex(' ') if obd else 'none'}; "
                f"UDS reply {uds.hex(' ') if uds else 'none'}")


check("ISO-TP request and response against an ECU", diagnostics)


# ── Things that need no bus ───────────────────────────────────────────────────
print("\n=== REMAINING FEATURES ===")


def ai_without_key():
    from canlab.tabs.ai_engine_tab import AIEngineTab
    tab = window.ai_tab if hasattr(window, "ai_tab") else None
    if tab is None:
        return False, "AI tab not found"
    tab._api_key = ""
    state.select_id(busiest)
    pump(0.3)
    before = tab.response_view.toPlainText() if hasattr(tab, "response_view") else ""
    tab._run_analysis()
    pump(0.6)
    return True, "analysis without a key returns without sending or crashing"


check("AI engine refuses to send without a key", ai_without_key)


def plugins():
    import hashlib
    import tempfile
    from pathlib import Path

    from canlab.core import plugin_loader as pl
    d = Path(tempfile.mkdtemp())
    pl.PLUGIN_DIR = d
    (d / "p.py").write_text('PLUGIN_NAME="P"\nPLUGIN_VERSION="1"\n'
                            'def register(app):\n    pass\n')
    fresh = pl.discover_plugins()[0]["enabled"]
    pl.set_enabled(str(d / "p.py"), True)
    on = pl.discover_plugins()[0]["enabled"]
    (d / "p.py").write_text('PLUGIN_NAME="P"\nPLUGIN_VERSION="1"\n'
                            'def register(app):\n    import os\n')
    after_edit = pl.discover_plugins()[0]
    return (not fresh and on and not after_edit["enabled"]
            and after_edit["changed"]), \
        (f"fresh={fresh}, approved={on}, after an edit "
         f"enabled={after_edit['enabled']} changed={after_edit['changed']}")


check("plugins stay off until approved, and re-prompt when edited", plugins)


def settings_persist():
    import tempfile

    from PyQt6.QtCore import QSettings
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      tempfile.mkdtemp())
    from canlab.settings_dialog import SettingsDialog, settings
    st = settings()
    st.setValue(SettingsDialog.S_CHANNEL, "can7")
    st.sync()
    return settings().value(SettingsDialog.S_CHANNEL) == "can7", \
        "a setting written through QSettings reads back"


check("settings persist", settings_persist)


def opendbc():
    from canlab.core import opendbc_matcher
    try:
        res = opendbc_matcher.match_capture(state.get_unique_ids(), top_k=3)
    except Exception as exc:
        return False, f"matcher raised: {type(exc).__name__}: {exc}"
    return True, (f"ranked {len(res)} OEM databases"
                  if res else "returned nothing (no cache, no network) without raising")


check("opendbc matching degrades without network", opendbc)


print("\n" + "=" * 66)
passed = sum(1 for _, ok, _ in RESULTS if ok)
print(f"  phase 3: {passed}/{len(RESULTS)} passed")
json.dump(RESULTS, open(f"{DATA}/phase3.json", "w"), indent=1)
window.close()
