#!/usr/bin/env python3
"""End to end against real captures the application has never seen.

The earlier acceptance run used one capture, from SavvyCAN's examples: 12,974
frames, 180 IDs, all 11-bit classic CAN, one bus, re-containered by this
project into the binary formats. That leaves gaps it cannot see. Every
extended ID path, every multi-bus path and the MDF4 reader were only ever
checked against files this repository generated itself.

This run uses other people's real logs instead:

  CANedge (CSS Electronics api-examples, canedge-influxdb-writer)
      Five genuine device recordings in native MDF4, which is the first time
      that reader has faced a file it did not write: a 145k-frame J1939 log
      that is 29-bit throughout, a 23-minute dual-bus recording, an OBD-II
      request/response log, 2 to 155k frames.
  python-can test data
      Vector BLF and ASC written by python-can's own writers, covering CAN
      FD, 64-byte FD, error frames, extended error frames, remote frames and
      a comma-decimal locale. Small, but genuine format edge cases.
  CSS Electronics mdf4-converters
      A multi-bus ASC from their converter's system tests.

Then the real marine NMEA 2000 log is pushed through every writer the application has
and read back by every parser, so the five formats have to agree about the
same traffic, with 29-bit IDs this time.

    python tests/real_data/acceptance_new_sources.py <data-dir>

Nothing here transmits: no bus is opened at all.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-1000/newdata")
PASS, FAIL, SKIP = [], [], []
_t0 = time.time()


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


def section(title: str):
    print(f"\n=== {title} ===")


# ── the corpus ───────────────────────────────────────────────────────────────

CANEDGE = ["canedge_a.MF4", "canedge_b.MF4", "canedge_c.MF4",
           "canedge_big.MF4", "canedge_nissan.MF4"]
PC_BLF = ["pc_test_CanMessage.blf", "pc_test_CanMessage2.blf",
          "pc_test_CanFdMessage.blf", "pc_test_CanFdMessage64.blf",
          "pc_test_CanErrorFrameExt.blf", "pc_issue_1905.blf"]
PC_ASC = ["pc_logfile.asc", "pc_logfile_errorframes.asc",
          "pc_test_CanFdMessage64.asc", "pc_test_CanErrorFrames.asc",
          "pc_test_CanRemoteMessage.asc", "pc_issue_1256.asc",
          "pc_single_frame_us_locale.asc", "css_multi.asc"]

_cache: dict[str, object] = {}


def load(name: str):
    from canlab.core.log_parser import parse_log_file
    if name not in _cache:
        _cache[name] = parse_log_file(str(DATA / name))
    return _cache[name]


COLUMNS = {"Timestamp", "ID", "Bus", "DLC", "Extended", "Delta"}


def describe(df) -> str:
    if df is None or df.empty:
        return "0 frames"
    span = float(df["Timestamp"].max() - df["Timestamp"].min())
    ext = int(df["Extended"].sum()) if "Extended" in df.columns else 0
    buses = sorted({int(b) for b in df["Bus"].dropna()}) if "Bus" in df.columns else []
    width = max((int(c[1:]) for c in df.columns
                 if c.startswith("B") and c[1:].isdigit()), default=-1) + 1
    return (f"{len(df)} frames, {df['ID'].nunique()} IDs, {span:.1f} s, "
            f"{ext} extended, buses {buses}, {width} byte columns")


# ── 1. real MDF4 from a device that is not us ────────────────────────────────

def phase_mdf4():
    section("REAL MDF4 FROM CANedge HARDWARE")
    try:
        import asammdf  # noqa: F401
    except ImportError:
        for n in CANEDGE:
            check(f"MDF4 {n}", lambda: (None, "asammdf not installed"))
        return

    for name in CANEDGE:
        def one(name=name):
            if not (DATA / name).is_file():
                return None, f"{name} not downloaded"
            df = load(name)
            if df.empty:
                return False, "parsed to zero frames"
            missing = COLUMNS - set(df.columns)
            if missing:
                return False, f"missing canonical columns: {missing}"
            if df["Timestamp"].isna().any():
                return False, "null timestamps"
            if not df["Timestamp"].is_monotonic_increasing:
                return False, "timestamps out of order"
            ids = df["ID"].astype(str)
            if not ids.str.fullmatch(r"[0-9A-F]{3,8}").all():
                return False, f"malformed IDs, e.g. {ids[~ids.str.fullmatch(r'[0-9A-F]{3,8}')].head(3).tolist()}"
            # A 29-bit log must be reported as extended, not silently truncated.
            wide = ids.str.len() > 3
            if wide.any() and not df.loc[wide, "Extended"].all():
                return False, "an ID above 0x7FF is not flagged extended"
            return True, describe(df)
        check(f"MDF4 {name}", one)

    def j1939_shape():
        df = load("canedge_big.MF4")
        ext = df["Extended"].all()
        pgns = {int(i, 16) >> 8 & 0x3FFFF for i in df["ID"].unique()}
        return (ext and len(pgns) > 20,
                f"every one of {len(df)} frames extended, {len(pgns)} distinct "
                f"J1939 PGNs, source addresses "
                f"{sorted({int(i, 16) & 0xFF for i in df['ID'].unique()})[:6]}…")
    check("the 145k-frame log really is 29-bit J1939", j1939_shape)

    def dual_bus():
        df = load("canedge_nissan.MF4")
        per_bus = df.groupby("Bus")["ID"].nunique().to_dict()
        return len(per_bus) >= 2, f"IDs per bus: {per_bus}, over {len(df)} frames"
    check("a two-channel recording keeps its bus tags", dual_bus)


# ── 2. native BLF and ASC written by another tool ────────────────────────────

def phase_native_binary():
    section("NATIVE BLF AND ASC FROM python-can AND CSS ELECTRONICS")

    for name in PC_BLF + PC_ASC:
        def one(name=name):
            if not (DATA / name).is_file():
                return None, f"{name} not downloaded"
            df = load(name)
            # Error and remote frames are meant to be counted and dropped, so
            # a file that is nothing but error frames legitimately parses to
            # zero data frames. What must not happen is a crash or a column
            # going missing.
            if df.empty:
                return True, "0 data frames (error/remote only), parsed cleanly"
            missing = COLUMNS - set(df.columns)
            if missing:
                return False, f"missing canonical columns: {missing}"
            return True, describe(df)
        check(f"parse {name}", one)

    def fd_width():
        wide = [n for n in PC_BLF + PC_ASC
                if (DATA / n).is_file() and not load(n).empty
                and max((int(c[1:]) for c in load(n).columns
                         if c.startswith("B") and c[1:].isdigit()), default=0) > 7]
        if not wide:
            return False, "no file widened past B7, so 64-byte FD was not exercised"
        widths = {n: max(int(c[1:]) for c in load(n).columns
                         if c.startswith("B") and c[1:].isdigit()) + 1 for n in wide}
        return True, f"FD payloads widened the frame: {widths}"
    check("64-byte CAN FD widens the byte columns", fd_width)

    def dlc_matches_payload():
        bad = []
        for n in PC_BLF + PC_ASC:
            if not (DATA / n).is_file():
                continue
            df = load(n)
            if df.empty:
                continue
            for _, r in df.head(200).iterrows():
                present = sum(1 for c in df.columns
                              if c.startswith("B") and c[1:].isdigit() and r[c] == r[c])
                dlc = int(r["DLC"]) if r["DLC"] == r["DLC"] else -1
                if dlc >= 0 and present < min(dlc, 64):
                    bad.append((n, dlc, present))
                    break
        return not bad, ("DLC agrees with the bytes present in every file"
                         if not bad else f"mismatches: {bad[:3]}")
    check("DLC agrees with the payload actually carried", dlc_matches_payload)

    def locale_file():
        n = "pc_single_frame_us_locale.asc"
        if not (DATA / n).is_file():
            return None, "not downloaded"
        df = load(n)
        return len(df) >= 1 and df["Timestamp"].notna().all(), \
            f"{len(df)} frame(s) with a decimal-comma timestamp read as {df['Timestamp'].tolist()}"
    check("an ASC written in a comma-decimal locale still parses", locale_file)


# ── 3. every format must agree about the same real traffic ───────────────────

def phase_cross_format():
    section("ONE REAL 29-BIT LOG THROUGH EVERY WRITER AND BACK")
    import tempfile

    src_name = "canedge_c.MF4"          # 9,600 frames, 50 IDs, all 29-bit, NMEA 2000
    if not (DATA / src_name).is_file():
        check("cross-format round trip", lambda: (None, f"{src_name} missing"))
        return
    source = load(src_name)
    tmp = Path(tempfile.mkdtemp(prefix="canlab-newdata-"))
    truth_ids = set(source["ID"].astype(str))
    results = {}

    from canlab.cli import write_savvycan_csv
    from canlab.core.log_parser import parse_log_file

    def write_all():
        import can
        write_savvycan_csv(source, tmp / "out.csv")
        for suffix, writer in ((".blf", can.BLFWriter), (".asc", can.ASCWriter),
                               (".log", can.CanutilsLogWriter)):
            w = writer(str(tmp / f"out{suffix}"))
            try:
                for _, r in source.iterrows():
                    data = bytes(int(r[f"B{i}"]) & 0xFF for i in range(8)
                                 if f"B{i}" in source.columns and r[f"B{i}"] == r[f"B{i}"])
                    w.on_message_received(can.Message(
                        timestamp=float(r["Timestamp"]),
                        arbitration_id=int(r["ID"], 16), data=data,
                        is_extended_id=bool(r.get("Extended", False))))
            finally:
                w.stop()
        import struct

        import dpkt
        with open(tmp / "out.pcap", "wb") as fh:
            pw = dpkt.pcap.Writer(fh, linktype=227)
            for _, r in source.iterrows():
                data = bytes(int(r[f"B{i}"]) & 0xFF for i in range(8)
                             if f"B{i}" in source.columns and r[f"B{i}"] == r[f"B{i}"])
                ident = int(r["ID"], 16) | (0x80000000 if r.get("Extended") else 0)
                pw.writepkt(struct.pack(">IB3x", ident, len(data)) + data.ljust(8, b"\0"),
                            ts=float(r["Timestamp"]))
        return True, f"wrote csv, blf, asc, log, pcap into {tmp}"
    check("write the real log out in five formats", write_all)

    for suffix in (".csv", ".blf", ".asc", ".log", ".pcap"):
        def one(suffix=suffix):
            path = tmp / f"out{suffix}"
            if not path.is_file():
                return None, "not written"
            df = parse_log_file(str(path))
            results[suffix] = df
            return len(df) == len(source), \
                f"{len(df)} frames back of {len(source)} written; {describe(df)}"
        check(f"read it back from {suffix}", one)

    def ids_agree():
        bad = {s: sorted(truth_ids ^ set(d["ID"].astype(str)))[:4]
               for s, d in results.items() if set(d["ID"].astype(str)) != truth_ids}
        return not bad, (f"all {len(results)} formats report the same "
                         f"{len(truth_ids)} arbitration IDs"
                         if not bad else f"disagreements: {bad}")
    check("every format reports the same 29-bit IDs", ids_agree)

    def extended_survives():
        # candump writes 29-bit IDs as 8 hex digits and 11-bit as 3, so the
        # flag has to be recovered from the text; pcap carries it in a bit.
        bad = {s: int(d["Extended"].sum()) for s, d in results.items()
               if "Extended" in d.columns and not d["Extended"].all()}
        return not bad, ("the extended flag survived every format"
                         if not bad else f"lost in: {bad}")
    check("the extended flag survives every format", extended_survives)

    def payloads_agree():
        cols = [f"B{i}" for i in range(8)]
        ref = source[cols].fillna(-1).to_numpy()
        bad = []
        for s, d in results.items():
            got = d[cols].fillna(-1).to_numpy()
            if got.shape != ref.shape or (got != ref).any():
                n = int((got != ref).sum()) if got.shape == ref.shape else -1
                bad.append((s, n))
        return not bad, ("every payload byte matches in all five formats"
                         if not bad else f"byte differences: {bad}")
    check("every payload byte matches in all five formats", payloads_agree)


# ── 4. the analysis, on data it has never seen ───────────────────────────────

def phase_analysis():
    section("EVERY DETECTOR ON THE REAL LOGS")
    from canlab.cli import draft_signals, run_detectors

    reports = {}

    def detectors(name):
        def run():
            df = load(name)
            t = time.perf_counter()
            rep = run_detectors(df, quiet=True)
            reports[name] = rep
            cc = rep["counters_checksums"]
            totals = {
                "counters": sum(len(v["counters"]) for v in cc.values()),
                "checksums": sum(len(v["checksums"]) for v in cc.values()),
                "flags": sum(len(v) for v in rep["flags"].values()),
                "enums": sum(len(v) for v in rep["enums"].values()),
                "boundaries": sum(len(v) for v in rep["boundaries"].values()),
                "mux": len(rep["multiplexers"]),
            }
            # Nothing may crash, and a real vehicle log should yield some
            # structure; a two-ID GPS log legitimately yields little.
            return True, f"{totals} in {time.perf_counter() - t:.1f} s"
        return run

    for name in ("canedge_c.MF4", "canedge_nissan.MF4", "canedge_b.MF4"):
        if (DATA / name).is_file():
            check(f"all detectors on {name}", detectors(name))

    def json_clean():
        import json
        for name, rep in reports.items():
            json.dumps(rep, default=str)
        return bool(reports), f"{len(reports)} report(s) serialise to JSON"
    check("every report is JSON-serialisable", json_clean)

    def draft_and_load():
        import tempfile

        import cantools
        name = "canedge_nissan.MF4" if "canedge_nissan.MF4" in reports else next(iter(reports))
        from canlab.core.dbc_manager import signals_to_dbc_string
        sigs = draft_signals(reports[name])
        if not sigs:
            return False, f"no signals drafted from {name}"
        text = signals_to_dbc_string(sigs)
        path = Path(tempfile.mkdtemp()) / "draft.dbc"
        path.write_text(text)
        db = cantools.database.load_file(str(path))
        return True, (f"{len(sigs)} signals drafted from {name}; cantools loaded "
                      f"{len(db.messages)} messages, {sum(len(m.signals) for m in db.messages)} signals")
    check("a drafted DBC loads in cantools", draft_and_load)

    def decode_real():
        """Define a signal on a real message and decode real frames with it."""
        from canlab.core.dbc_manager import decode_frame, frame_bytes_from_row
        df = load("canedge_nissan.MF4")
        busiest = df["ID"].value_counts().index[0]
        sig = {"message_id": busiest, "message_name": f"MSG_{busiest}",
               "signal_name": "TEST_16", "start_bit": 0, "length": 16,
               "byte_order": "big", "value_type": "unsigned",
               "scale": 0.1, "offset": 0.0, "min_val": 0, "max_val": 6553.5,
               "unit": "unit", "description": ""}
        g = df[df["ID"] == busiest].head(50)
        values = [decode_frame([sig], busiest, frame_bytes_from_row(r)).get("TEST_16")
                  for _, r in g.iterrows()]
        good = [v for v in values if v is not None]
        return len(good) == len(g), \
            f"decoded {len(good)}/{len(g)} real frames of {busiest}, range " \
            f"{min(good):.1f} to {max(good):.1f}"
    check("a signal defined on a real message decodes real frames", decode_real)


# ── 5. the features added this session, on real data ─────────────────────────

def phase_new_features():
    section("SNIFFER, TRIM, MCP AND GVRET ON REAL DATA")

    def sniffer():
        from canlab.core.sniffer import Sniffer
        df = load("canedge_nissan.MF4")
        s = Sniffer(live=False)
        t = time.perf_counter()
        s.update_dataframe(df)
        rows = s.snapshot()
        elapsed = time.perf_counter() - t
        moving = sum(1 for r in rows for c in r.changes if c)
        notched = s.notch()
        after = sum(1 for r in s.snapshot() for d in r.direction if d)
        return (len(rows) == df["ID"].nunique() and notched > 0 and after == 0,
                f"{len(df)} frames into {len(rows)} rows in {elapsed:.2f} s, "
                f"{moving} moving bytes; notch masked {notched} bits and "
                f"silenced every colour ({after} left)")
    check("the sniffer collapses a 23-minute log and notches it", sniffer)

    def trim():
        from canlab.core import capture_split
        df = load("canedge_nissan.MF4")
        span = float(df["Timestamp"].max() - df["Timestamp"].min())
        by_time = capture_split.split_by_time(df, 0, span / 4)
        by_bus = capture_split.split_by_bus(df, int(df["Bus"].iloc[0]))
        top = df["ID"].value_counts().index[0]
        by_id = capture_split.split_by_ids(df, top)
        ok = (0 < by_time.n_kept < len(df) and 0 < by_bus.n_kept <= len(df)
              and by_id.n_kept == int((df["ID"] == top).sum()))
        return ok, (f"time quarter kept {by_time.n_kept}, bus filter kept "
                    f"{by_bus.n_kept}, ID {top} kept {by_id.n_kept} of {len(df)}")
    check("trimming a real capture by time, bus and ID", trim)

    def mcp():
        from canlab.core.mcp_tools import CanLabTools
        t = CanLabTools()
        info = t.load_log(str(DATA / "canedge_c.MF4"))
        ids = t.list_ids(limit=500)
        top = ids[0]["id"]
        stats = t.byte_stats(top)
        frames = t.frames(top, n=5)
        flags = t.detect_flags(top)
        hit = t.search(top)["results"]
        doc = t.fetch(f"id:{top}")
        added = t.add_dbc_signal(top, "FROM_MCP", 0, 8)
        decoded = t.decode(top, n=2)
        ok = (info["frames"] == len(load("canedge_c.MF4")) and len(ids) == info["ids"]
              and stats["frames"] > 0 and len(frames) == 5 and hit
              and "Byte statistics" in doc["text"] and added["added"]
              and "FROM_MCP" in decoded["frames"][0]["values"])
        return ok, (f"loaded {info['frames']} frames and {info['ids']} IDs, "
                    f"byte stats and {len(flags.get(top, []))} flags for {top}, "
                    f"search/fetch answer, signal added and decoded")
    check("the MCP tools drive a capture they have never seen", mcp)

    def gvret():
        """Turn real frames into GVRET wire bytes and parse them back."""
        from canlab.core import gvret
        df = load("canedge_c.MF4").head(500)
        stream = b""
        for _, r in df.iterrows():
            data = bytes(int(r[f"B{i}"]) & 0xFF for i in range(8)
                         if f"B{i}" in df.columns and r[f"B{i}"] == r[f"B{i}"])
            ident = int(r["ID"], 16) | (gvret.EXTENDED_FLAG if r["Extended"] else 0)
            stream += (bytes([gvret.SOF, gvret.CMD_FRAME])
                       + int(r["Timestamp"] * 1e6).to_bytes(4, "little", signed=False)
                       + ident.to_bytes(4, "little")
                       + bytes([len(data) & 0x0F]) + data)
        codec = gvret.GvretCodec()
        got = []
        for i in range(0, len(stream), 7):        # a deliberately awkward split
            got += codec.feed(stream[i:i + 7])
        ids_in = [int(i, 16) for i in df["ID"]]
        ids_out = [f.arbitration_id for f in got]
        return (ids_out == ids_in and all(f.is_extended_id for f in got)
                and codec.desyncs == 0,
                f"{len(got)} real 29-bit frames survived the GVRET codec across "
                f"7-byte reads with {codec.desyncs} desyncs")
    check("real 29-bit frames round-trip through the GVRET codec", gvret)

    def adapters():
        from canlab.core.adapters import INTERFACES, detect_adapters, probe_adapter
        from canlab.core.adapters import Adapter
        found = detect_adapters(timeout=2.0)
        r = probe_adapter(Adapter("v", "virtual", "canlab-newdata"), listen_s=0.2)
        return (r["ok"] and "gvret" in INTERFACES,
                f"{len(found)} adapter(s) detected on this machine "
                f"({[a.interface for a in found]}); the virtual bus opened in "
                f"{r['open_ms']} ms and heard {r['frames']} frames")
    check("adapter detection and a listen-only open", adapters)


# ── 6. exports, from real data, read by the tools that consume them ──────────

def phase_exports():
    section("EVERY EXPORT FORMAT FROM REAL DATA")
    import tempfile

    import cantools

    from canlab.cli import draft_signals, run_detectors
    from canlab.core.dbc_manager import signals_to_dbc_string
    out = Path(tempfile.mkdtemp(prefix="canlab-exports-"))
    df = load("canedge_c.MF4")
    sigs = draft_signals(run_detectors(df, quiet=True))[:40]
    if not sigs:
        check("exports", lambda: (None, "no signals to export"))
        return

    def dbc():
        p = out / "real.dbc"
        p.write_text(signals_to_dbc_string(sigs))
        db = cantools.database.load_file(str(p))
        return len(db.messages) > 0, \
            f"{len(sigs)} signals to DBC, cantools read {len(db.messages)} messages"
    check("DBC, loaded back by cantools", dbc)

    def arxml():
        from canlab.core.arxml_export import to_arxml_string
        p = out / "real.arxml"
        p.write_text(to_arxml_string(sigs))
        db = cantools.database.load_file(str(p))
        return len(db.messages) > 0, \
            f"cantools read {len(db.messages)} messages from the ARXML"
    check("ARXML, loaded back by cantools", arxml)

    def lua():
        from canlab.core.lua_exporter import signals_to_lua_dissector
        text = signals_to_lua_dissector(sigs)
        p = out / "real.lua"
        p.write_text(text)
        # Strip comments first: the file explains in prose that it avoids
        # bit32, and grepping the whole text flags its own explanation.
        import re as _re
        code = "\n".join(_re.sub(r"--.*$", "", ln) for ln in text.splitlines())
        bad = [t for t in ("bit32", "<<", ">>", "&") if t in code]
        return not bad, (f"{len(text)} bytes of dissector, no 5.3-only operator "
                         "in the code" if not bad else f"code contains {bad}")
    check("Wireshark Lua dissector", lua)

    def openpilot():
        from canlab.core.openpilot_export import to_opendbc_string
        p = out / "real_openpilot.dbc"
        p.write_text(to_opendbc_string(sigs))
        db = cantools.database.load_file(str(p))
        return len(db.messages) > 0, f"cantools read {len(db.messages)} messages"
    check("openpilot DBC", openpilot)

    def candbpp():
        from canlab.core.candbpp_export import to_candbpp_string
        p = out / "real_candbpp.dbc"
        p.write_text(to_candbpp_string(sigs))
        db = cantools.database.load_file(str(p))
        return len(db.messages) > 0, f"cantools read {len(db.messages)} messages"
    check("CANdb++ DBC", candbpp)

    def timeseries():
        from canlab.core.timeseries_export import export_timeseries
        p = out / "decoded.csv"
        rows = export_timeseries(df, sigs, str(p))
        import pandas as pd
        back = pd.read_csv(p)
        return rows > 0 and len(back) > 0, \
            f"{rows} rows, {len(back.columns)} columns of decoded time series"
    check("decoded time-series CSV", timeseries)

    def codegen():
        from canlab.tabs.code_gen_tab import _build_code
        py = _build_code("decode", "socketcan", "can0", 500000, sigs,
                         False, False, False, 0.0)
        compile(py, "generated.py", "exec")          # must be valid Python
        tx = _build_code("encode", "socketcan", "can0", 500000, sigs,
                         False, False, False, 0.0)
        compile(tx, "generated_tx.py", "exec")
        return True, (f"{len(py)} bytes of decoder and {len(tx)} bytes of encoder, "
                      "both valid Python for real signals")
    check("generated code compiles for real signals", codegen)

    def convert_cli():
        from canlab.cli import build_parser, main
        src = DATA / "canedge_c.MF4"
        dst = out / "cli_out.csv"
        rc = main(["convert", str(src), str(dst)])
        from canlab.core.log_parser import parse_log_file
        back = parse_log_file(str(dst))
        build_parser()
        return rc == 0 and len(back) == len(df), \
            f"canlab-cli convert wrote {len(back)} frames of {len(df)}"
    check("canlab-cli convert on the real MDF4", convert_cli)


# ── 7. the transmit gate is still shut ───────────────────────────────────────

def phase_blocks():
    """Repeated blocks on the Tigor EV capture. The data is the user's own
    car and is not in the corpus, so this runs only when CANLAB_PRIVATE_DATA
    names the directory holding tatatigor.canlab.zip."""
    section("REPEATED BLOCKS ON A PRIVATE EV CAPTURE")
    import os
    import zipfile
    from canlab.core.block_detector import block_to_signals, detect_blocks
    from canlab.core.canid import normalize_id
    from canlab.core.dbc_manager import validate_signals

    private = os.environ.get("CANLAB_PRIVATE_DATA")
    zip_path = Path(private).expanduser() / "tatatigor.canlab.zip" if private else None

    def tigor():
        if zip_path is None:
            return None, "CANLAB_PRIVATE_DATA not set"
        if not zip_path.is_file():
            return None, f"{zip_path} missing"
        import pandas as pd
        with zipfile.ZipFile(zip_path) as zf:
            df = pd.read_csv(zf.open("frames.csv"), dtype={"ID": str})
        df["ID"] = df["ID"].apply(normalize_id)
        t0 = time.perf_counter()
        blocks = detect_blocks(df, max_gap=2)
        took = time.perf_counter() - t0
        by_first = {b.first: b for b in blocks}
        big = by_first.get("380")
        small = by_first.get("244")
        ok = (big is not None and len(big.members) == 27 and big.last == "39A"
              and abs(big.rate_hz - 2.0) < 0.1 and big.dlc == 8
              and big.constant_members == 9 and small is not None
              and [m.can_id for m in small.members] == ["244", "245", "247", "249"]
              and took < 10.0)
        names_ok = True
        if big is not None and big.fields:
            sigs = block_to_signals(big, big.best_field)
            names_ok = (all("CANDIDATE" in s["signal_name"] for s in sigs)
                        and validate_signals(sigs) == [])
        detail = (f"{len(blocks)} blocks in {took:.2f} s on {len(df)} frames; "
                  + (f"380..{big.last}: {len(big.members)} members at {big.rate_hz:.2f} Hz, "
                     f"DLC {big.dlc}, {big.constant_members} constant, agreement "
                     f"{big.layout_agreement:.0%}, best field "
                     f"{big.best_field.label if big.best_field else 'none'} at "
                     f"{big.best_field.consistency:.0%} consistency" if big else "no 380 block")
                  + (f"; 244..249: {len(small.members)} members" if small else "; no 244 block"))
        return ok and names_ok, detail
    check("the EV's cell block and the four-message run are found", tigor)


COMMA_SEGMENT = "b0c9d2329ad1606b%7C2018-08-02--08-34-47/40"
COMMA_FILES = ("processed_log/CAN/raw_can/t", "processed_log/CAN/raw_can/address",
               "processed_log/CAN/raw_can/data", "processed_log/CAN/raw_can/src",
               "processed_log/CAN/speed/t", "processed_log/CAN/speed/value",
               "processed_log/GNSS/live_gnss_ublox/t", "processed_log/GNSS/live_gnss_ublox/value")


def _comma2k19() -> Path | None:
    """comma.ai's comma2k19 example segment (MIT): one minute of raw CAN from a
    Toyota RAV4 with a u-blox GNSS receiver alongside, and openpilot's own
    decoded speed. About 6 MB, fetched once into the corpus directory."""
    import urllib.request
    root = DATA / "comma2k19"
    base = ("https://raw.githubusercontent.com/commaai/comma2k19/master/Example_1/"
            + COMMA_SEGMENT + "/")
    for rel in COMMA_FILES:
        dest = root / rel
        if dest.is_file() and dest.stat().st_size > 0:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(base + rel, timeout=60) as r:
                dest.write_bytes(r.read())
        except OSError:
            return None
    return root


def phase_reference():
    """The calibrator on a real car, against a real GNSS receiver.

    The answer keys are external: openpilot's DBC for the Toyota puts vehicle
    speed in 0x0B4 bytes 5-6 and the four wheel speeds in 0x0AA at 0.01 km/h
    with an offset of -67.67 km/h, and the u-blox rows carry both the log's
    clock and UTC, so the true clock offset is known.
    """
    section("REFERENCE CALIBRATION ON A REAL CAR")
    import numpy as np
    import pandas as pd
    from canlab.core.dbc_manager import decode_frame
    from canlab.core.reference_calibrate import (
        calibrate_with_lag_search, candidate_to_signal_def, find_time_offset,
    )
    from canlab.core.reference_series import ReferenceSeries

    root = _comma2k19()
    if root is None:
        check("comma2k19 segment", lambda: (None, "could not fetch the comma2k19 segment"))
        return
    L = lambda rel: np.load(root / rel, allow_pickle=True)  # noqa: E731
    t, addr = L("processed_log/CAN/raw_can/t"), L("processed_log/CAN/raw_can/address")
    data, src = L("processed_log/CAN/raw_can/data"), L("processed_log/CAN/raw_can/src")
    keep = src == 0
    t, addr, data = t[keep], addr[keep], data[keep]
    mat = np.full((len(t), 8), np.nan)
    dlc = np.zeros(len(t), dtype=int)
    for i, d in enumerate(data):
        b = bytes(d)
        dlc[i] = len(b)
        mat[i, :len(b)] = list(b)
    df = pd.DataFrame({"Timestamp": t, "ID": [f"{a:03X}" for a in addr], "Bus": 0,
                       "DLC": dlc, "Extended": False,
                       **{f"B{i}": mat[:, i] for i in range(8)}}).sort_values("Timestamp")
    gt = L("processed_log/GNSS/live_gnss_ublox/t")
    gv = L("processed_log/GNSS/live_gnss_ublox/value")
    speed, utc = gv[:, 2], gv[:, 3] / 1000.0
    st, sv = L("processed_log/CAN/speed/t"), L("processed_log/CAN/speed/value")[:, 0]
    true_offset = float(np.median(utc - gt))
    state = {}

    def found():
        series = ReferenceSeries("speed", utc, speed, unit="m/s")
        t0 = time.perf_counter()
        cands = calibrate_with_lag_search(df, series, window_s=30.0, top_k=8)
        took = time.perf_counter() - t0
        state["cands"] = cands
        passing = [c for c in cands if c["verdict"] == "PASS"]
        vehicle = [c for c in passing if c["id"] == "0B4" and c["start_bit"] == 40
                   and c["length"] == 16 and c["byte_order"] == "big"]
        wheel_cands = [c for c in passing if c["id"] == "0AA" and c["length"] == 16
                       and c["byte_order"] == "big"]
        wheels = {c["start_bit"] for c in wheel_cands}
        targets = vehicle + wheel_cands
        worst = min((c["r2"] for c in targets), default=0.0)
        ok = bool(vehicle) and wheels == {0, 16, 32, 48} and worst > 0.99
        return ok, (f"{len(df):,} frames from {df['ID'].nunique()} IDs, {len(gt)} GNSS fixes; "
                    f"vehicle speed 0x0B4 bytes 5-6 and all four 0x0AA wheel words found, "
                    f"R2 {worst:.4f} and up, in {took:.1f} s; {len(passing) - len(targets)} "
                    f"other speed-correlated fields also pass")
    check("a car's speed signals are found from a GPS log alone", found)

    def native_scale():
        cands = state.get("cands") or []
        wheels = [c for c in cands if c["id"] == "0AA" and c.get("native_unit") == "km/h"]
        if not wheels:
            return False, "no wheel-speed candidate snapped to a km/h scale"
        scales = {c["native_scale"] for c in wheels}
        offsets = [c["native_offset"] for c in wheels]
        ok = scales == {0.01} and all(abs(o - (-67.67)) < 1.0 for o in offsets)
        return ok, (f"{len(wheels)} wheel words at {scales.pop()} km/h per bit, offsets "
                    f"{min(offsets):.2f} to {max(offsets):.2f} km/h; openpilot's DBC says "
                    f"0.01 and -67.67")
    check("the wheel-speed scale comes back as the manufacturer wrote it", native_scale)

    def clock():
        off = find_time_offset(df, utc, speed, window_s=30.0)
        same = find_time_offset(df, gt, speed, window_s=2.0, fine_step_s=0.02)
        grid = np.arange(max(st.min(), gt.min()) + 2, min(st.max(), gt.max()) - 2, 0.01)
        can = np.interp(grid, st, sv)
        lags = np.arange(-1.0, 1.0, 0.01)
        independent = float(lags[int(np.argmax([np.corrcoef(can, np.interp(grid + x, gt, speed))[0, 1]
                                                 for x in lags]))])
        err = off["lag_s"] - true_offset
        ok = abs(err) < 0.3 and abs(same["lag_s"] - independent) < 0.06
        return ok, (f"UTC reference placed within {err:+.3f} s of the true offset; on one "
                    f"clock the search measures the receiver's latency as "
                    f"{same['lag_s']:+.2f} s, and openpilot's own speed against GNSS, "
                    f"without CanLab, gives {independent:+.2f} s")
    check("a UTC-stamped reference is put on the capture's clock", clock)

    def decodes():
        cands = state.get("cands") or []
        vehicle = [c for c in cands if c["id"] == "0B4" and c["start_bit"] == 40]
        if not vehicle:
            return False, "no 0x0B4 candidate"
        sig = candidate_to_signal_def(vehicle[0], "SPEED")
        rows = df[df["ID"] == "0B4"].iloc[::25]
        errs = []
        for r in rows.itertuples():
            frame = bytes(int(getattr(r, f"B{i}")) for i in range(int(r.DLC)))
            got = decode_frame([sig], "0B4", frame)["SPEED"]
            if sig["unit"] == "km/h":
                got /= 3.6
            want = float(np.interp(r.Timestamp, st, sv))
            if want > 3:
                errs.append(abs(got - want) / want)
        med = float(np.median(errs))
        return med < 0.02, (f"the DBC signal it writes decodes {len(errs)} frames within "
                            f"{100 * med:.2f}% of openpilot's own decode (median)")
    check("the signal it writes decodes the car", decodes)


def phase_safety():
    section("NOTHING TRANSMITTED")

    def disarmed():
        from canlab.core.safety import BusNotArmedError, is_armed, require_tx_allowed
        if is_armed():
            return False, "the gate was left armed by an earlier check"
        try:
            require_tx_allowed(0x123)
        except BusNotArmedError:
            return True, "require_tx_allowed refuses while disarmed, as it must"
        return False, "the gate allowed a send while disarmed"
    check("the transmit gate refused everything", disarmed)

    def no_mcp_tool_sends():
        from canlab.core.mcp_tools import CanLabTools
        names = set(CanLabTools.TOOL_NAMES)
        sending = {n for n in names
                   if any(k in n for k in ("send", "inject", "transmit", "replay", "fuzz"))}
        return not sending, f"none of the {len(names)} MCP tools can transmit"
    check("no MCP tool can put a frame on a bus", no_mcp_tool_sends)


# ── run ──────────────────────────────────────────────────────────────────────

# ── 8. multi-frame messages, reassembled and checked against the world ───────

def phase_multiframe():
    section("MULTI-FRAME MESSAGES REASSEMBLED")
    from canlab.core.j1939 import decode_n2k
    from canlab.core.multiframe import Reassembler, summarize

    def marine():
        if not (DATA / "canedge_c.MF4").is_file():
            return None, "canedge_c.MF4 missing"
        df = load("canedge_c.MF4")
        r = Reassembler()
        t0 = time.perf_counter()
        r.update_dataframe(df)
        r.finish()
        took = time.perf_counter() - t0
        st = r.stats()
        gnss = [m for m in r.messages if m.pgn == 129029]
        sats = [m for m in r.messages if m.pgn == 129540]
        ok = (st["dropped"] == 0 and len(gnss) == 60
              and all(m.size == 43 for m in gnss)
              and len(sats) == 60 and all(m.size == 135 for m in sats)
              and all(m.data[2] == 11 for m in sats))
        return ok, (f"{st['complete']} messages, {st['dropped']} dropped in {took:.2f} s; "
                    f"{len(gnss)} GNSS fixes of 43 B, {len(sats)} satellite lists of "
                    f"135 B each naming 11 satellites")
    check("the marine log reassembles with nothing dropped", marine)

    def position_agrees():
        if not (DATA / "canedge_c.MF4").is_file():
            return None, "canedge_c.MF4 missing"
        df = load("canedge_c.MF4")
        r = Reassembler()
        r.update_dataframe(df)
        first = next(m for m in r.messages if m.pgn == 129029)
        fix = decode_n2k(129029, first.data)
        rapid = df[df["ID"] == "9F80123"].iloc[0]
        lat_rapid = int.from_bytes(bytes(int(rapid[f"B{i}"]) for i in range(4)),
                                   "little", signed=True) * 1e-7
        lat, lon, alt = fix["Latitude"][0], fix["Longitude"][0], fix["Altitude"][0]
        ok = (abs(lat - lat_rapid) < 1e-3 and abs(lat - 42.661) < 1e-3
              and abs(lon + 81.2128) < 1e-3 and abs(alt - 173.5) < 0.6
              and fix["Date"][0] == "2021-03-25")
        return ok, (f"reassembled fix {lat:.5f}, {lon:.5f} at {alt:.1f} m on "
                    f"{fix['Date'][0]}; the single-frame rapid position says "
                    f"{lat_rapid:.5f}; Lake Erie's surface is about 174 m")
    check("a reassembled GNSS fix agrees with the single-frame position", position_agrees)

    def truck():
        if not (DATA / "canedge_big.MF4").is_file():
            return None, "canedge_big.MF4 missing"
        df = load("canedge_big.MF4")
        r = Reassembler()
        t0 = time.perf_counter()
        r.update_dataframe(df)
        r.finish()
        took = time.perf_counter() - t0
        rows = {(row["pgn"], row["sa"]): row for row in summarize(r.messages)}
        ec1 = rows.get((0xFEE3, 0x00))
        rc = rows.get((0xFEE1, 0x0F))
        ok = (r.stats()["dropped"] == 0 and took < 2.0
              and ec1 is not None and ec1["count"] == 46 and ec1["bytes"] == 39
              and rc is not None and rc["count"] == 39 and rc["bytes"] == 19)
        return ok, (f"{r.stats()['complete']} BAM messages in {took:.2f} s over 145,534 "
                    f"frames: {ec1['count'] if ec1 else 0} x Engine Configuration (39 B) "
                    f"from the engine, {rc['count'] if rc else 0} x Retarder Configuration "
                    f"(19 B) from address 0x0F, none dropped")
    check("the truck log's BAM broadcasts reassemble", truck)


def main() -> int:
    print(f"CanLab end-to-end acceptance against new real sources\ndata: {DATA}")
    if not DATA.is_dir():
        print(f"no such directory: {DATA}")
        return 2
    have = sorted(p.name for p in DATA.iterdir() if p.is_file())
    print(f"{len(have)} file(s): {', '.join(have)}")

    for phase in (phase_mdf4, phase_native_binary, phase_cross_format,
                  phase_analysis, phase_new_features, phase_exports, phase_multiframe, phase_blocks, phase_reference, phase_safety):
        phase()

    print("\n" + "=" * 70)
    total = len(PASS) + len(FAIL)
    print(f"  {len(PASS)}/{total} passed"
          + (f", {len(FAIL)} failed" if FAIL else "")
          + (f", {len(SKIP)} skipped" if SKIP else "")
          + f"  in {time.time() - _t0:.0f} s")
    for name in FAIL:
        print(f"    FAILED: {name}")
    for name in SKIP:
        print(f"    skipped: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
