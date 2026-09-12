#!/usr/bin/env python3
"""Phase 2: exports, code generation, integrations and the transmit gate,
all against the real 180-ID vehicle capture."""
import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DATA = sys.argv[1]
OUT = f"{DATA}/exports"
os.makedirs(OUT, exist_ok=True)

from PyQt6.QtCore import QRect                                    # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox             # noqa: E402

for _n in ("information", "warning", "critical", "question", "about"):
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: None))
app = QApplication(sys.argv)

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
pump(1.0)
print(f"\nloaded {len(state.frames_df)} real frames, "
      f"{state.frames_df['ID'].nunique()} IDs")

# A signal on a byte that actually moves in this capture.
busiest = state.frames_df["ID"].value_counts().index[0]
frames = state.get_frames_for_id(busiest)
moving = max(range(8), key=lambda i: frames[f"B{i}"].nunique())
SIG = {"message_id": busiest, "message_name": f"MSG_{busiest}",
       "signal_name": "RealSignal", "start_bit": moving * 8, "length": 8,
       "byte_order": "little", "value_type": "unsigned", "scale": 1.0,
       "offset": 0.0, "min_val": 0, "max_val": 255, "unit": "", "description": ""}
state.add_dbc_signal(dict(SIG))
pump(0.5)
print(f"defined a signal on the byte that moves most: {busiest} B{moving} "
      f"({frames[f'B{moving}'].nunique()} distinct values)")


# ── Decode against real frames ────────────────────────────────────────────────
print("\n=== DECODING REAL FRAMES ===")


def decode_real():
    from canlab.core.dbc_manager import decode_frame
    got = []
    for _, row in frames.head(50).iterrows():
        data = bytes(int(row[f"B{i}"]) if row[f"B{i}"] == row[f"B{i}"] else 0
                     for i in range(8))
        out = decode_frame([SIG], busiest, data)
        if "RealSignal" in out:
            got.append(out["RealSignal"])
    return len(got) == 50, f"decoded 50/50 real frames, values {min(got)}..{max(got)}"


check("decode real frames through cantools", decode_real)


# ── Exports, each loaded back by the tool that must read it ───────────────────
print("\n=== EXPORTS, ROUND-TRIPPED ===")


def dbc_roundtrip():
    import cantools
    from canlab.core.dbc_manager import signals_to_dbc_string
    text = signals_to_dbc_string(state.dbc_signals)
    open(f"{OUT}/real.dbc", "w").write(text)
    db = cantools.database.load_string(text, database_format="dbc")
    m = db.messages[0]
    return m.name.endswith(busiest), \
        f"cantools loaded it: {m.name}, {len(m.signals)} signal(s), {m.length} bytes"


check("DBC export loads in cantools", dbc_roundtrip)


def lua_export():
    from canlab.core.lua_exporter import signals_to_lua_dissector
    text = signals_to_lua_dissector(state.dbc_signals)
    open(f"{OUT}/real.lua", "w").write(text)
    # Comments explain why bit32 is avoided, so strip them before checking.
    code = "\n".join(ln for ln in text.splitlines()
                     if not ln.strip().startswith("--"))
    body = code[code.index("local function get_bit"):]
    bad = [t for t in ("bit32", "<<", ">>", "&", "|") if t in body]
    return "Field.new" in code and not bad, \
        f"{len(text)} chars; no Lua 5.3-only constructs ({bad or 'clean'})"


check("Wireshark Lua export", lua_export)


def arxml_export():
    import cantools
    from canlab.core.arxml_export import to_arxml_string
    open(f"{OUT}/real.arxml", "w").write(to_arxml_string(state.dbc_signals))
    db = cantools.database.load_file(f"{OUT}/real.arxml")
    return len(db.messages) >= 1, \
        f"cantools loaded the ARXML: {len(db.messages)} message(s)"


check("ARXML export loads in cantools", arxml_export)


def openpilot_export():
    import cantools
    from canlab.core.openpilot_export import to_opendbc_string
    open(f"{OUT}/real_op.dbc", "w").write(to_opendbc_string(state.dbc_signals))
    db = cantools.database.load_file(f"{OUT}/real_op.dbc")
    return len(db.messages) >= 1, f"openpilot DBC parses: {len(db.messages)} message(s)"


check("openpilot DBC export", openpilot_export)


def candbpp():
    from canlab.core.candbpp_export import to_candbpp_string
    open(f"{OUT}/real_candbpp.dbc", "w").write(to_candbpp_string(state.dbc_signals))
    text = open(f"{OUT}/real_candbpp.dbc").read()
    return "BA_DEF_" in text, f"{len(text)} chars, attribute blocks present"


check("Vector CANdb++ export", candbpp)


def timeseries():
    from canlab.core.timeseries_export import export_timeseries
    rows = export_timeseries(state.frames_df, state.dbc_signals,
                             f"{OUT}/real_series.csv")
    return rows > 0, f"{rows} decoded rows written to CSV"


check("decoded time-series export", timeseries)


def codegen():
    window.codegen_tab._generate()
    pump(0.6)
    text = window.codegen_tab.code_edit.toPlainText()
    compiled = False
    try:
        compile(text, "generated.py", "exec")
        compiled = True
    except SyntaxError as exc:
        return False, f"generated Python does not compile: {exc}"
    return compiled and len(text) > 200, \
        f"{len(text)} chars of Python, compiles cleanly"


check("CODE GEN output is valid Python", codegen)


# ── The transmit gate, with real arbitration IDs ──────────────────────────────
print("\n=== TRANSMIT GATE (real IDs, virtual bus) ===")


def gate():
    import threading

    import can
    channel = "realtest-gate"
    window._can_settings = {"interface": "virtual", "channel": channel,
                            "bitrate": 500000, "fd": False, "data_bitrate": None}
    watcher = can.interface.Bus(channel=channel, interface="virtual")
    seen, stop = [], threading.Event()

    def watch():
        while not stop.is_set():
            m = watcher.recv(timeout=0.1)
            if m is not None:
                seen.append(m)

    threading.Thread(target=watch, daemon=True).start()
    window._connect_can()
    pump(0.5)

    safety.set_armed(False)
    window.injection_tab._refresh_signal_list()
    if window.injection_tab.sig_combo.count():
        window.injection_tab.sig_combo.setCurrentIndex(0)
    window.injection_tab._send_once()
    window.diagnostics_tab._clear_dtc()
    pump(0.8)
    while_disarmed = len(seen)

    safety.set_armed(True)
    window.injection_tab._send_once()
    pump(0.8)
    while_armed = len(seen) - while_disarmed

    safety.set_armed(False)
    stop.set()
    pump(0.3)
    window._disconnect_can()
    watcher.shutdown()
    return while_disarmed == 0 and while_armed >= 1, \
        f"disarmed sent {while_disarmed} frames, armed sent {while_armed}"


check("nothing transmits while disarmed", gate)


# ── Integrations, serving the real capture ────────────────────────────────────
print("\n=== INTEGRATIONS ===")


def rest():
    import json as _json

    from fastapi.testclient import TestClient

    from canlab.core.rest_api import _build_app
    client = TestClient(_build_app(lambda: state, "tok"))
    unauth = client.get("/frames")
    r = client.get("/frames?n=25", headers={"X-API-Token": "tok"})
    body = r.json()
    _json.dumps(body)                      # must be valid JSON, NaN and all
    return (unauth.status_code == 401 and r.status_code == 200
            and len(body) == 25), \
        f"no token -> {unauth.status_code}, with token -> {len(body)} real frames"


check("REST serves real frames and rejects no token", rest)


def mcp():
    from canlab.core.mcp_tools import CanLabTools
    t = CanLabTools()
    t.load_log(f"{DATA}/GVRET_Log.csv")
    ids = t.list_ids(limit=500)
    stats = t.byte_stats(busiest)
    report = t.run_all_detectors()
    draft = t.draft_dbc()
    hit = t.search(busiest)["results"]
    doc = t.fetch(f"id:{busiest}")
    return (len(ids) == 180 and bool(stats["bytes"]) and report["totals"]["counters"] > 0
            and draft["signals"] > 0 and hit and "Byte statistics" in doc["text"]), \
        (f"load_log + list_ids returned {len(ids)} IDs; run_all_detectors found "
         f"{report['totals']}; draft_dbc wrote {draft['signals']} signals; "
         f"search/fetch answer for {busiest}")


check("MCP tools drive the real capture", mcp)


print("\n" + "=" * 66)
passed = sum(1 for _, ok, _ in RESULTS if ok)
print(f"  phase 2: {passed}/{len(RESULTS)} passed")
json.dump(RESULTS, open(f"{DATA}/phase2.json", "w"), indent=1)
window.close()
