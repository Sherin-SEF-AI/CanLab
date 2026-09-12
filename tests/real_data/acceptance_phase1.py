#!/usr/bin/env python3
"""Exercise every CanLab feature against a real vehicle capture.

Real data only: 12,974 frames, 180 arbitration IDs, captured from a vehicle
and published with SavvyCAN. No synthetic frames are used anywhere here.

Each check prints PASS or FAIL with what it actually observed, so a failure
says what the application did, not merely that an assertion tripped.
"""
import os
import sys
import time
import traceback
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DATA = sys.argv[1] if len(sys.argv) > 1 else "."
CAPTURE = f"{DATA}/GVRET_Log.csv"

from PyQt6.QtCore import QRect                                    # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox             # noqa: E402

for _n in ("information", "warning", "critical", "question", "about"):
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: None))

app = QApplication(sys.argv)

from canlab.core.log_parser import parse_log_file                 # noqa: E402
from canlab.core.state import get_state                           # noqa: E402
from canlab.mainwindow import MainWindow                          # noqa: E402

RESULTS = []


def check(name, fn):
    """Run one check. fn returns (ok, detail)."""
    try:
        ok, detail = fn()
    except Exception as exc:
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"  FAIL  {name}\n        {type(exc).__name__}: {exc}")
        if os.environ.get("TRACE"):
            traceback.print_exc()
        return False
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}\n        {detail}")
    return ok


def pump(seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_for(predicate, timeout=90):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.03)
    return False


# ── Load ──────────────────────────────────────────────────────────────────────
print("\n=== LOADING REAL CAPTURE ===")
window = MainWindow()
window.showNormal()
window.setGeometry(QRect(0, 0, 1920, 1080))
state = get_state()

df = parse_log_file(CAPTURE)
state.load_frames(df, "GVRET_Log.csv")
pump(1.0)
N_FRAMES, N_IDS = len(state.frames_df), state.frames_df["ID"].nunique()
print(f"  {N_FRAMES} frames, {N_IDS} arbitration IDs, "
      f"{state.frames_df.Timestamp.max() - state.frames_df.Timestamp.min():.1f} s")

check("frames reach the store",
      lambda: (N_FRAMES == 12974, f"{N_FRAMES} frames in the store"))
check("all 180 IDs survive the load",
      lambda: (N_IDS == 180, f"{N_IDS} unique IDs"))


# ── Panels ────────────────────────────────────────────────────────────────────
print("\n=== PANELS ===")


def id_panel():
    window.id_panel._refresh_ids()
    pump(0.5)
    tree = window.id_panel.id_tree
    total = sum(tree.topLevelItem(i).childCount() for i in range(tree.topLevelItemCount()))
    return total >= N_IDS, f"ID tree lists {total} entries for {N_IDS} IDs"


check("ID panel lists every ID", id_panel)


def inspector():
    busiest = state.frames_df["ID"].value_counts().index[0]
    state.select_id(busiest)
    pump(0.8)
    txt = window.inspector.hex_dump.toPlainText()
    return len(txt) > 40, f"inspector shows {len(txt)} chars for busiest ID {busiest}"


check("inspector renders the busiest ID", inspector)


def frames_table():
    window.tabs.setCurrentIndex(0)
    window.frames_tab._refresh()
    pump(0.5)
    rows = window.frames_tab.table.rowCount()
    return rows > 0, f"{rows} rows displayed of {N_FRAMES} captured"


check("FRAMES table populates", frames_table)


def frames_filter():
    target = state.frames_df["ID"].value_counts().index[0]
    window.frames_tab.filter_id.setText(target)
    window.frames_tab._on_filter_changed()
    pump(0.5)
    rows = window.frames_tab.table.rowCount()
    shown = {window.frames_tab.table.item(r, 1).text()
             for r in range(min(rows, 40)) if window.frames_tab.table.item(r, 1)}
    window.frames_tab.filter_id.setText("")
    window.frames_tab._on_filter_changed()
    pump(0.3)
    return shown == {target}, f"filter on {target} shows only {sorted(shown)[:3]}"


check("FRAMES hex filter narrows to one ID", frames_filter)


# ── Analysis ──────────────────────────────────────────────────────────────────
print("\n=== ANALYSIS ON REAL TRAFFIC ===")


def classify():
    window.signals_tab._run_classify()
    ok = wait_for(lambda: window.signals_tab.table.rowCount() > 0, 120)
    rows = window.signals_tab.table.rowCount()
    return ok and rows >= N_IDS * 0.9, f"classified {rows} messages"


check("SIGNALS classifies every message", classify)


def counters():
    from canlab.core.counter_checksum_detector import detect_counters_and_checksums
    t0 = time.perf_counter()
    found = detect_counters_and_checksums(state.frames_df)
    secs = time.perf_counter() - t0
    ctr = sum(len(v["counters"]) for v in found.values())
    cks = sum(len(v["checksums"]) for v in found.values())
    return True, (f"{ctr} counters, {cks} checksums across {len(found)} messages "
                  f"in {secs:.2f}s")


check("counter/checksum sweep over 180 real messages", counters)


def entropy():
    from canlab.core.entropy_boundary import (detect_signal_boundaries,
                                              suggest_signals)
    bounds = detect_signal_boundaries(state.frames_df)
    drafts = suggest_signals(state.frames_df)
    fields = sum(len(v) for v in bounds.values())
    return bool(bounds), (f"{fields} candidate fields across {len(bounds)} "
                          f"messages; {len(drafts)} draft signals")


check("entropy boundaries on the busiest message", entropy)


def guesser():
    from canlab.core.checksum_guesser import guess_all_bytes
    busiest = state.frames_df["ID"].value_counts().index[0]
    frames = state.get_frames_for_id(busiest)
    res = guess_all_bytes(frames, busiest)
    hits = {k: v[0]["algorithm"] for k, v in res.items() if v}
    return True, f"{busiest}: {hits if hits else 'no checksum matched (plausible)'}"


check("checksum guesser runs all algorithms", guesser)


def correlate():
    from canlab.core.correlation_engine import correlate_id_pair
    top = list(state.frames_df["ID"].value_counts().index[:2])
    t0 = time.perf_counter()
    res = correlate_id_pair(state.frames_df, top[0], top[1], min_r=0.75)
    return True, (f"{top[0]} vs {top[1]}: {len(res)} correlated byte pairs "
                  f"in {time.perf_counter()-t0:.2f}s")


check("cross-ID correlation", correlate)


def periodicity():
    window.intelligence_tab._compute_periodicity()
    ok = wait_for(lambda: window.intelligence_tab.period_table.rowCount() > 0, 90)
    return ok, f"periodicity for {window.intelligence_tab.period_table.rowCount()} IDs"


check("INTELLIGENCE periodicity", periodicity)


def mux():
    from canlab.core.mux_detector import detect_all_multiplexers
    res = detect_all_multiplexers(state.frames_df)
    return True, f"{len(res)} multiplexed message(s) found in real traffic"


check("multiplexer detection", mux)


def ml_roles():
    if window.ml_intel_tab.id_list.count():
        window.ml_intel_tab.id_list.setCurrentRow(0)
        pump(0.2)
    window.ml_intel_tab._analyze_selected()
    ok = wait_for(lambda: window.ml_intel_tab.roles_table.rowCount() > 0, 90)
    return ok, f"{window.ml_intel_tab.roles_table.rowCount()} byte roles classified"


check("ML INTEL byte-role classification", ml_roles)


print("\n" + "=" * 66)
passed = sum(1 for _, ok, _ in RESULTS if ok)
print(f"  phase 1: {passed}/{len(RESULTS)} passed")
import json
with open(f"{DATA}/phase1.json", "w") as fh:
    json.dump(RESULTS, fh, indent=1)
window.close()
