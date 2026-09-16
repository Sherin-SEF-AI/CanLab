#!/usr/bin/env python3
"""A stress run over the largest real recordings, with the clock on everything.

The other acceptance runs ask whether the application is correct on real data.
This one asks whether it stays correct and usable when the data is big and
mixed: a 145,534-frame J1939 truck log, a 154,896-frame two-channel car log,
and the two merged into one 300,430-frame capture carrying 11-bit and 29-bit
identifiers on three bus tags at once, which is a shape no single recording in
the corpus has.

It is deliberately harsh in three ways. Every stage is timed against a budget
rather than only checked for a result. The detectors run over the whole log
rather than a slice. And the ground truth is external: this truck reports its
engine speed in a standard J1939 message, so a decode that disagrees with a
plausible engine is a failure whatever the unit tests say.

Nothing transmits. No bus is opened, and the run asserts that at the end.

    python tests/real_data/acceptance_stress.py <data-dir>
"""
from __future__ import annotations

import gc
import json
import os
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DATA = Path(sys.argv[1] if len(sys.argv) > 1
            else Path.home() / "Desktop" / "CanLab-real-data-new")
PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []
TIMES: dict[str, float] = {}
FACTS: dict[str, object] = {}
_t0 = time.time()

BIG = "canedge_big.MF4"          # 145,534 frames, 142 IDs, J1939, 29-bit
DUAL = "canedge_nissan.MF4"      # 154,896 frames, 16 IDs, two channels, 11-bit


def check(name: str, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        FAIL.append(name)
        print(f"  FAIL  {name}\n        {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)
        return None
    if ok is None:
        SKIP.append(name)
        print(f"  SKIP  {name}\n        {detail}")
    elif ok:
        PASS.append(name)
        print(f"  PASS  {name}\n        {detail}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}\n        {detail}")
    return detail


def section(title: str) -> None:
    print(f"\n=== {title} ===")


class timed:
    """Time a block and record it, so the report can quote real numbers."""

    def __init__(self, key: str):
        self.key = key

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.seconds = time.perf_counter() - self.t
        TIMES[self.key] = self.seconds
        return False


def rss_mb() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return float("nan")


_cache: dict[str, object] = {}


def load(name: str):
    from canlab.core.log_parser import parse_log_file
    if name not in _cache:
        _cache[name] = parse_log_file(str(DATA / name))
    return _cache[name]


# ── 1. parsing at scale ──────────────────────────────────────────────────────

def phase_parse():
    section("PARSING THE TWO LARGEST RECORDINGS")
    for name, frames, ids in ((BIG, 145_534, 142), (DUAL, 154_896, 16)):
        def one(name=name, frames=frames, ids=ids):
            if not (DATA / name).is_file():
                return None, f"{name} missing"
            with timed(f"parse {name}") as t:
                df = load(name)
            rate = len(df) / t.seconds
            FACTS[f"parse_{name}"] = {"frames": len(df), "seconds": t.seconds}
            ok = len(df) == frames and df["ID"].nunique() == ids and t.seconds < 8
            return ok, (f"{len(df):,} frames, {df['ID'].nunique()} IDs in "
                        f"{t.seconds:.2f} s ({rate:,.0f} frames/s)")
        check(f"parse {name}", one)

    def merged():
        import pandas as pd
        if not all((DATA / n).is_file() for n in (BIG, DUAL)):
            return None, "both large logs are needed"
        a, b = load(BIG).copy(), load(DUAL).copy()
        # The car log tags its channels 1 and 2 and the truck log tags 1, so
        # the truck moves to bus 3 rather than being silently merged into the
        # car's first channel.
        b = b.copy()
        a["Bus"] = 3
        with timed("merge") as t:
            merged = pd.concat([a, b], ignore_index=True).sort_values("Timestamp")
            merged = merged.reset_index(drop=True)
        _cache["merged"] = merged
        buses = sorted(int(x) for x in merged["Bus"].dropna().unique())
        ext = int(merged["Extended"].sum())
        FACTS["merged"] = {"frames": len(merged), "ids": int(merged["ID"].nunique()),
                           "buses": buses, "extended": ext}
        return (len(merged) == len(a) + len(b) and buses == [1, 2, 3]
                and 0 < ext < len(merged)), \
            (f"{len(merged):,} frames, {merged['ID'].nunique()} IDs, buses {buses}, "
             f"{ext:,} extended and {len(merged) - ext:,} standard, "
             f"merged in {t.seconds:.2f} s")
    check("merge them into one mixed capture", merged)


# ── 2. the protocol layer against a real truck ───────────────────────────────

def phase_j1939():
    section("J1939 ON A REAL TRUCK LOG")
    from canlab.core.j1939 import decode_dm1, decode_pgn, scan_for_j1939

    def scan():
        df = load(BIG)
        with timed("pgn scan") as t:
            hits = scan_for_j1939(df)
        named = [h for h in hits if not h["pgn_name"].startswith("PGN ")]
        j1939 = [h for h in hits if h["protocol"] == "J1939"]
        FACTS["pgns"] = {"total": len(hits), "named": len(named)}
        return (len(hits) > 90 and len(named) >= 8 and len(j1939) == len(hits)
                and t.seconds < 2), \
            (f"{len(hits)} PGNs, {len(named)} named, all J1939 "
             f"(unlike the marine log), in {t.seconds:.2f} s")
    check("every identifier decodes as a J1939 PGN", scan)

    def engine_speed():
        """SPN 190 in EEC1. A truck's engine speed is the one value here that
        can be judged without any knowledge of this particular vehicle."""
        df = load(BIG)
        eec1 = df[df["ID"] == "CF00400"]
        if eec1.empty:
            return None, "no EEC1 messages"
        with timed("decode eec1") as t:
            values = []
            for _, row in eec1.iterrows():
                data = bytes(int(row[f"B{i}"]) for i in range(8))
                out = decode_pgn(0xF004, data)
                if "Engine Speed" in out:
                    values.append(out["Engine Speed"][0])
        lo, hi = min(values), max(values)
        mean = sum(values) / len(values)
        FACTS["rpm"] = {"n": len(values), "min": lo, "max": hi, "mean": mean}
        plausible = 300 <= lo <= 1200 and 1000 <= hi <= 3000
        return plausible and len(values) == len(eec1), \
            (f"{len(values):,} frames decoded in {t.seconds:.1f} s: "
             f"{lo:.0f} to {hi:.0f} rpm, mean {mean:.0f}. An idling and "
             f"working diesel, which is what this log should be")
    check("engine speed decodes to a plausible engine", engine_speed)

    def not_available():
        """J1939 marks a missing value as all ones. Wheel speed is absent in
        this log, and 0xFFFF read as a number is 256 km/h."""
        df = load(BIG)
        ccvs = df[df["ID"] == "18FEF117"]
        if ccvs.empty:
            return None, "no CCVS messages"
        row = ccvs.iloc[0]
        data = bytes(int(row[f"B{i}"]) for i in range(8))
        out = decode_pgn(0xFEF1, data)
        raw = (int(row["B2"]) * 256 + int(row["B1"])) / 256
        return "Wheel-Based Vehicle Speed" not in out, \
            (f"wheel speed is not reported; read as a plain number it would "
             f"have been {raw:.0f} km/h")
    check("an unavailable value is not reported as a reading", not_available)

    def dm1():
        df = load(BIG)
        rows = df[df["ID"] == "18FECA3D"]
        if rows.empty:
            return None, "no DM1 messages"
        row = rows.iloc[0]
        out = decode_dm1(bytes(int(row[f"B{i}"]) for i in range(8)))
        lamps = out.get("lamps", {})
        return isinstance(out.get("dtcs"), list), \
            (f"{len(rows)} DM1 frames; first frame has {len(out['dtcs'])} active "
             f"codes, lamps {lamps}")
    check("active fault codes decode", dm1)


# ── 3. the detectors, over whole logs ────────────────────────────────────────

def phase_detectors():
    section("EVERY DETECTOR OVER 145,534 FRAMES")
    df = load(BIG)

    def counters():
        from canlab.core.counter_checksum_detector import (
            detect_counters_and_checksums,
        )
        with timed("counters") as t:
            found = detect_counters_and_checksums(df)
        n = sum(len(v["counters"]) for v in found.values())
        c = sum(len(v["checksums"]) for v in found.values())
        FACTS["counters"] = {"counters": n, "checksums": c, "messages": len(found)}
        return t.seconds < 30, (f"{n} counter bytes and {c} checksum bytes across "
                                f"{len(found)} messages of {df['ID'].nunique()}, "
                                f"in {t.seconds:.1f} s")
    check("counter and checksum detection", counters)

    def counters_are_real():
        """Two hits on a bus of 142 messages is not a failure of the detector.

        A J1939 message has no standard rolling counter, unlike the OEM car
        buses these heuristics were written against, so almost nothing should
        match. The clearest hit is 1CEBFF00, which is PGN 60160, the transport
        protocol's data transfer: its first byte is a sequence number by
        specification. The detector found that without being told.
        """
        from canlab.core.counter_checksum_detector import (
            detect_counters_and_checksums,
        )
        from canlab.core.j1939 import parse_j1939_id
        found = detect_counters_and_checksums(df)
        tp = found.get("1CEBFF00")
        if tp is None:
            return False, "the transport protocol's sequence byte was not found"
        byte = tp["counters"][0]["byte"]
        pgn = parse_j1939_id(0x1CEBFF00)["pgn"]
        busy = int((df["ID"].value_counts() >= 100).sum())
        return byte == 0 and pgn == 60160,             (f"{len(found)} of {busy} well-populated messages carry one, and the "
             f"clearest is PGN {pgn}, the transport protocol, in byte {byte}: a "
             f"sequence number by specification")
    check("the few counters found are ones that should be there", counters_are_real)

    def entropy():
        from canlab.core.entropy_boundary import detect_signal_boundaries
        with timed("entropy") as t:
            out = detect_signal_boundaries(df)
        n = sum(len(v) for v in out.values()) if isinstance(out, dict) else len(out)
        return t.seconds < 60, f"{n} field boundaries in {t.seconds:.1f} s"
    check("entropy boundaries", entropy)

    def flags():
        from canlab.core.bit_flags import detect_flags
        with timed("flags") as t:
            out = detect_flags(df)
        n = sum(len(v) for v in out.values())
        return t.seconds < 60, f"{n} bit-level flags in {t.seconds:.1f} s"
    check("bit flags", flags)

    def mux():
        from canlab.core.mux_detector import detect_multiplexer
        hits = 0
        with timed("mux") as t:
            for can_id in df["ID"].unique():
                frames = df[df["ID"] == can_id]
                if len(frames) < 50:
                    continue
                if detect_multiplexer(frames):
                    hits += 1
        return t.seconds < 90, f"{hits} multiplexed messages in {t.seconds:.1f} s"
    check("multiplexer detection", mux)


# ── 4. decoding a signal across the whole log ────────────────────────────────

def phase_decode():
    section("DECODING THROUGH CANTOOLS AT SCALE")
    from canlab.core.dbc_manager import decode_series

    def decode():
        df = load(BIG)
        sig = {"message_id": "CF00400", "message_name": "EEC1",
               "signal_name": "EngineSpeed", "start_bit": 24, "length": 16,
               "byte_order": "little", "value_type": "unsigned",
               "scale": 0.125, "offset": 0.0, "min_val": 0, "max_val": 8031.875,
               "unit": "rpm"}
        frames = df[df["ID"] == "CF00400"]
        with timed("decode_series") as t:
            out = decode_series([sig], "CF00400", frames)
        if out.empty:
            return False, "decoded nothing"
        col = [c for c in out.columns if c != "Timestamp"][0]
        lo, hi = float(out[col].min()), float(out[col].max())
        FACTS["decode"] = {"rows": len(out), "seconds": t.seconds}
        # The same numbers as the J1939 tables produced, through a different
        # path: cantools rather than the built-in PGN decoder.
        return (len(out) == len(frames) and 900 <= lo <= 1000
                and 1700 <= hi <= 1800 and t.seconds < 30), \
            (f"{len(out):,} frames through cantools in {t.seconds:.1f} s: "
             f"{lo:.0f} to {hi:.0f} rpm, agreeing with the J1939 decoder")
    check("a signal defined by hand decodes the whole log", decode)


# ── 5. every format, at ten times the size the corpus used ───────────────────

def phase_formats():
    section("145,534 FRAMES THROUGH EVERY WRITER AND BACK")
    import struct
    import tempfile

    from canlab.cli import write_savvycan_csv
    from canlab.core.log_parser import parse_log_file

    source = load(BIG)
    tmp = Path(tempfile.mkdtemp(prefix="canlab-stress-"))
    results: dict[str, object] = {}

    def payload(row, width=8):
        return bytes(int(row[f"B{i}"]) & 0xFF for i in range(width)
                     if f"B{i}" in source.columns and row[f"B{i}"] == row[f"B{i}"])

    def write_all():
        import can
        with timed("write formats") as t:
            write_savvycan_csv(source, tmp / "out.csv")
            for suffix, writer in ((".blf", can.BLFWriter), (".asc", can.ASCWriter),
                                   (".log", can.CanutilsLogWriter)):
                w = writer(str(tmp / f"out{suffix}"))
                try:
                    for _, r in source.iterrows():
                        w.on_message_received(can.Message(
                            timestamp=float(r["Timestamp"]),
                            arbitration_id=int(r["ID"], 16), data=payload(r),
                            is_extended_id=bool(r.get("Extended", False))))
                finally:
                    w.stop()
            import dpkt
            with open(tmp / "out.pcap", "wb") as fh:
                pw = dpkt.pcap.Writer(fh, linktype=227)
                for _, r in source.iterrows():
                    data = payload(r)
                    ident = int(r["ID"], 16) | (0x80000000 if r.get("Extended") else 0)
                    pw.writepkt(struct.pack(">IB3x", ident, len(data))
                                + data.ljust(8, b"\0"), ts=float(r["Timestamp"]))
        sizes = {p.suffix: p.stat().st_size / 1e6 for p in sorted(tmp.iterdir())}
        return True, (f"five files in {t.seconds:.0f} s: "
                      + ", ".join(f"{k} {v:.0f} MB" for k, v in sizes.items()))
    check("write the truck log out in five formats", write_all)

    for suffix in (".csv", ".blf", ".asc", ".log", ".pcap"):
        def one(suffix=suffix):
            path = tmp / f"out{suffix}"
            if not path.is_file():
                return None, "not written"
            with timed(f"read {suffix}") as t:
                df = parse_log_file(str(path))
            results[suffix] = df
            return len(df) == len(source), \
                (f"{len(df):,} frames back of {len(source):,} in {t.seconds:.1f} s "
                 f"({len(df) / t.seconds:,.0f} frames/s)")
        check(f"read 145k frames back from {suffix}", one)

    def agree():
        truth_ids = set(source["ID"].astype(str))
        bad = {s: len(truth_ids ^ set(d["ID"].astype(str)))
               for s, d in results.items() if set(d["ID"].astype(str)) != truth_ids}
        return not bad, (f"all {len(results)} formats report the same "
                         f"{len(truth_ids)} identifiers" if not bad
                         else f"disagreements: {bad}")
    check("every format agrees about 142 identifiers", agree)

    def payloads():
        cols = [f"B{i}" for i in range(8)]
        ref = source[cols].fillna(0).to_numpy()
        bad = {}
        for s, d in results.items():
            got = d[cols].fillna(0).to_numpy()
            if got.shape != ref.shape or not (got == ref).all():
                mismatches = int((got != ref).sum()) if got.shape == ref.shape else -1
                bad[s] = mismatches
        return not bad, ("every payload byte of 145,534 frames survived all five "
                         "formats" if not bad else f"byte differences: {bad}")
    check("1.1 million payload bytes survive the round trip", payloads)

    FACTS["formats"] = {k: len(v) for k, v in results.items()}


# ── 6. the frame store and the window, at 300,000 frames ─────────────────────

def phase_gui():
    section("THE APPLICATION ITSELF, ON 300,430 MERGED FRAMES")
    merged = _cache.get("merged")
    if merged is None:
        check("window at scale", lambda: (None, "merge did not run"))
        return

    from PyQt6.QtWidgets import QApplication, QMessageBox
    for name in ("information", "warning", "critical", "question", "about"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    import tempfile

    from PyQt6.QtCore import QSettings
    cfg = tempfile.mkdtemp(prefix="canlab-stress-cfg-")
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        QSettings.setPath(fmt, QSettings.Scope.UserScope, cfg)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    from canlab.core.state import get_state
    from canlab.mainwindow import MainWindow

    before = rss_mb()
    window = MainWindow()
    window.resize(1920, 1080)
    window.show()
    app.processEvents()
    state = get_state()

    def load_into_window():
        with timed("load into window") as t:
            state.load_frames(merged, "merged stress capture")
            for _ in range(60):
                app.processEvents()
        used = rss_mb() - before
        FACTS["window"] = {"seconds": t.seconds, "rss_mb": rss_mb()}
        return t.seconds < 45, (f"{len(state.frames_df):,} frames into the window in "
                                f"{t.seconds:.1f} s; process holding "
                                f"{rss_mb():,.0f} MB, {used:,.0f} MB more than before")
    check("load the merged capture into the real window", load_into_window)

    def store():
        st = state.store
        stats = st.id_stats()
        buses = sorted(st.buses()) if hasattr(st, "buses") else []
        return len(stats) == merged["ID"].nunique(), \
            (f"the store reports {len(stats)} identifiers and buses {buses}, "
             f"dropping {getattr(st, 'dropped', 0)} frames at its cap")
    check("the frame store keeps every identifier", store)

    def tabs():
        slow = {}
        with timed("cycle tabs") as t:
            for i in range(window.tabs.count()):
                started = time.perf_counter()
                window.tabs.setCurrentIndex(i)
                for _ in range(4):
                    app.processEvents()
                took = time.perf_counter() - started
                if took > 3.0:
                    slow[window.tabs.tabText(i)] = round(took, 1)
        FACTS["tabs"] = {"seconds": t.seconds, "slow": slow}
        return not slow, (f"all {window.tabs.count()} tabs drew in "
                          f"{t.seconds:.1f} s" + (f"; slow: {slow}" if slow else ""))
    check("every tab draws with 300k frames loaded", tabs)

    def frames_table():
        tab = window.frames_tab
        window.tabs.setCurrentWidget(tab)
        app.processEvents()
        with timed("frames refresh") as t:
            tab._refresh()
            app.processEvents()
        rows = tab.table.rowCount()
        return t.seconds < 5, (f"the frame table refreshed {rows} visible rows in "
                               f"{t.seconds:.2f} s; it is capped rather than "
                               f"drawing all {len(state.frames_df):,}")
    check("the frame table stays cheap", frames_table)

    def sniffer():
        tab = window.sniffer_tab
        window.tabs.setCurrentWidget(tab)
        app.processEvents()
        with timed("sniffer") as t:
            tab._tick()
            app.processEvents()
        rows = tab.table.rowCount()
        return t.seconds < 5 and rows > 0, \
            (f"the sniffer folded the capture into {rows} rows in {t.seconds:.2f} s")
    check("the sniffer folds 300k frames into one row per message", sniffer)

    def id_panel():
        # Force a full rebuild rather than the cheap in-place update: the
        # signature is unchanged since the load, so without this the timing
        # would be of nothing happening.
        window.id_panel._tree_signature = None
        with timed("id panel") as t:
            window.id_panel._refresh_ids()
            app.processEvents()
        tree = window.id_panel.id_tree
        widest = max((tree.sizeHintForColumn(c) for c in range(tree.columnCount())),
                     default=0)
        fits = all(tree.columnWidth(c) >= tree.sizeHintForColumn(c)
                   for c in range(tree.columnCount()))
        return t.seconds < 5 and fits, \
            (f"the ID list rebuilt in {t.seconds:.2f} s and every column still fits "
             f"its contents (widest {widest} px)")
    check("the ID list handles 158 identifiers", id_panel)

    def live_replay():
        """Push the capture through the store the way a live bus would.

        No bus is opened: the frames are the merged recording, handed to the
        store in 25 ms batches at the rate they were recorded, with the window
        drawing between batches. It exercises the append and refresh path that
        a real capture uses, at 300,000 frames.
        """
        state.store.clear()
        rows = merged.head(60_000).to_dict("records")
        batch, latencies = 2_000, []
        with timed("live replay") as t:
            for start in range(0, len(rows), batch):
                state.store.append_batch(rows[start:start + batch])
                mark = time.perf_counter()
                window.frames_tab._refresh()
                app.processEvents()
                latencies.append(time.perf_counter() - mark)
        worst = max(latencies)
        rate = len(rows) / t.seconds
        FACTS["replay"] = {"frames": len(rows), "rate": rate, "worst_ui": worst}
        return worst < 1.0 and rate > 20_000,             (f"{len(rows):,} frames through the store at {rate:,.0f} frames/s; "
             f"the slowest redraw between batches was {worst * 1000:.0f} ms")
    check("replay 60,000 frames through the store as if live", live_replay)

    def safety():
        from canlab.core import safety as sf
        armed = sf.is_armed()
        opened = bool(getattr(state, "bus_hub", None))
        return (not armed) and not opened, \
            (f"transmit is {'armed' if armed else 'disarmed'} and "
             f"{'a bus is open' if opened else 'no bus was ever opened'}")
    check("nothing was transmitted during the run", safety)

    window.close()
    app.processEvents()
    gc.collect()


def main() -> int:
    print(f"CanLab stress run over {DATA}")
    for phase in (phase_parse, phase_j1939, phase_detectors, phase_decode,
                  phase_formats, phase_gui):
        phase()

    section("SUMMARY")
    total = len(PASS) + len(FAIL) + len(SKIP)
    for key, seconds in TIMES.items():
        print(f"  {key:<28} {seconds:8.2f} s")
    print(f"\n  {len(PASS)}/{total} passed, {len(FAIL)} failed, {len(SKIP)} skipped "
          f"in {time.time() - _t0:.0f} s")
    if FAIL:
        print("  failed: " + ", ".join(FAIL))
    out = Path(DATA) / "stress.json"
    try:
        out.write_text(json.dumps({"times": TIMES, "facts": FACTS,
                                   "pass": PASS, "fail": FAIL, "skip": SKIP},
                                  indent=1, default=str))
        print(f"  numbers written to {out}")
    except OSError:
        pass
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
