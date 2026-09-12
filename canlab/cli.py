"""Headless command line for CanLab.

The analysis modules are Qt-free, so everything here runs without a display:
in CI over a folder of drives, from a notebook, or piped into something else.

    canlab-cli ids      capture.csv
    canlab-cli detect   capture.csv [--json report.json] [--dbc draft.dbc]
    canlab-cli decode   capture.csv --dbc signals.dbc [--out decoded.csv]
    canlab-cli convert  capture.blf out.csv

Every command reads any format the application does. ``detect`` runs the
counter and checksum sweep, entropy boundaries, bit-level flags, value-table
inference and multiplexer detection, and can write a draft DBC from what it
found. Nothing here transmits.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _load(path: str):
    from canlab.core.log_parser import parse_log_file
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"no such file: {path}")
    t0 = time.perf_counter()
    df = parse_log_file(str(p))
    if df.empty:
        raise SystemExit(f"no frames found in {path}")
    df.attrs["load_seconds"] = time.perf_counter() - t0
    return df


def _fmt_rate(hz: float) -> str:
    return f"{hz:7.1f} Hz" if hz >= 1 else f"{hz:7.2f} Hz"


# ── ids ──────────────────────────────────────────────────────────────────────

def cmd_ids(args) -> int:
    df = _load(args.capture)
    span = float(df["Timestamp"].max() - df["Timestamp"].min()) or 1.0
    counts = df["ID"].value_counts()
    print(f"{len(df)} frames, {len(counts)} IDs, {span:.1f} s  "
          f"(parsed in {df.attrs['load_seconds']:.2f} s)")
    print(f"{'ID':<10}{'frames':>8}  {'rate':>10}  {'DLC':>4}  bytes that change")
    for cid, n in counts.items():
        g = df[df["ID"] == cid]
        moving = [f"B{i}" for i in range(8)
                  if f"B{i}" in g.columns and g[f"B{i}"].nunique() > 1]
        dlc = int(g["DLC"].mode().iloc[0]) if "DLC" in g.columns and len(g) else 8
        print(f"{cid:<10}{n:>8}  {_fmt_rate(n / span)}  {dlc:>4}  {' '.join(moving)}")
    return 0


# ── detect ───────────────────────────────────────────────────────────────────

def run_detectors(df, *, quiet: bool = False) -> dict:
    """Every offline detector, as one JSON-serialisable report."""
    from canlab.core.bit_flags import detect_flags
    from canlab.core.counter_checksum_detector import detect_counters_and_checksums
    from canlab.core.entropy_boundary import detect_signal_boundaries
    from canlab.core.mux_detector import detect_all_multiplexers
    from canlab.core.value_tables import infer_enums

    def step(name, fn):
        t0 = time.perf_counter()
        out = fn()
        if not quiet:
            print(f"  {name:<24} {time.perf_counter() - t0:6.2f} s", file=sys.stderr)
        return out

    span = float(df["Timestamp"].max() - df["Timestamp"].min()) or 1.0
    counts = df["ID"].value_counts()
    report = {
        "capture": {"frames": int(len(df)), "ids": int(len(counts)),
                    "span_s": round(span, 3)},
        "ids": {cid: {"frames": int(n), "rate_hz": round(n / span, 2)}
                for cid, n in counts.items()},
        "counters_checksums": step("counters/checksums",
                                   lambda: detect_counters_and_checksums(df)),
        "boundaries": step("entropy boundaries",
                           lambda: detect_signal_boundaries(df)),
        "flags": {k: [f.as_dict() for f in v]
                  for k, v in step("bit flags", lambda: detect_flags(df)).items()},
        "enums": {k: [e.as_dict() for e in v]
                  for k, v in step("value tables", lambda: infer_enums(df)).items()},
        "multiplexers": step("multiplexers", lambda: detect_all_multiplexers(df)),
    }
    return report


def draft_signals(report: dict) -> list[dict]:
    """Signal definitions from what the detectors found, without overlaps.

    Several detectors can claim the same bits: a byte with a switch in bit 1
    also looks enumerated, because it only takes two values. cantools refuses
    a message whose signals overlap, so bits are claimed in priority order and
    a later, coarser claim on a claimed bit is dropped. Checksums and counters
    first, since a DBC that omits them is harder to use for injection; then
    flags, which are the finest decomposition; then enumerations.
    """
    from canlab.core.bit_flags import BitFlag, flag_to_signal
    from canlab.core.value_tables import EnumField, EnumState, enum_to_signal

    claimed: dict[str, set[int]] = {}
    sigs: list[dict] = []

    def take(sig: dict) -> None:
        bits = set(range(sig["start_bit"], sig["start_bit"] + sig["length"]))
        owned = claimed.setdefault(sig["message_id"], set())
        if bits & owned:
            return
        owned |= bits
        sigs.append(sig)

    def byte_signal(cid, byte, name, description):
        return {"message_id": cid, "message_name": f"MSG_{cid}",
                "signal_name": name, "start_bit": byte * 8, "length": 8,
                "byte_order": "little", "value_type": "unsigned",
                "scale": 1.0, "offset": 0.0, "min_val": 0, "max_val": 255,
                "unit": "", "description": description}

    for cid, found in report["counters_checksums"].items():
        for c in found.get("checksums", []):
            take(byte_signal(cid, c["byte"], f"CHECKSUM_{cid}_B{c['byte']}",
                             f"checksum, {c['algorithm']}"))
        for c in found.get("counters", []):
            take(byte_signal(cid, c["byte"], f"COUNTER_{cid}_B{c['byte']}",
                             f"rolling counter, {c['type']}"))
    for cid, flags in report["flags"].items():
        for f in flags:
            take(flag_to_signal(BitFlag(**f)))
    for cid, enums in report["enums"].items():
        for e in enums:
            e = dict(e)
            e.pop("value_table", None)
            e["states"] = [EnumState(**s) for s in e["states"]]
            take(enum_to_signal(EnumField(**e)))
    return sigs


def cmd_detect(args) -> int:
    df = _load(args.capture)
    print(f"{len(df)} frames, {df['ID'].nunique()} IDs", file=sys.stderr)
    report = run_detectors(df, quiet=args.quiet)

    cc = report["counters_checksums"]
    n_ctr = sum(len(v["counters"]) for v in cc.values())
    n_cks = sum(len(v["checksums"]) for v in cc.values())
    n_flag = sum(len(v) for v in report["flags"].values())
    n_enum = sum(len(v) for v in report["enums"].values())
    n_bound = sum(len(v) for v in report["boundaries"].values())
    print(f"counters {n_ctr}  checksums {n_cks}  flags {n_flag}  "
          f"enumerations {n_enum}  field boundaries {n_bound}  "
          f"multiplexed {len(report['multiplexers'])}")

    if not args.quiet:
        for cid, found in sorted(cc.items()):
            for c in found["checksums"]:
                print(f"  {cid}: checksum {c['col']} {c['algorithm']} "
                      f"({c['confidence']:.0%})")
        for cid, flags in sorted(report["flags"].items())[:12]:
            best = flags[0]
            print(f"  {cid}: {best['label']}")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str))
        print(f"wrote {args.json}", file=sys.stderr)
    if args.dbc:
        from canlab.core.dbc_manager import signals_to_dbc_string
        sigs = draft_signals(report)
        Path(args.dbc).write_text(signals_to_dbc_string(sigs))
        print(f"wrote {args.dbc} with {len(sigs)} draft signal(s)", file=sys.stderr)
    return 0


# ── decode ───────────────────────────────────────────────────────────────────

def cmd_decode(args) -> int:
    from canlab.core.dbc_manager import load_dbc
    from canlab.core.timeseries_export import export_timeseries
    df = _load(args.capture)
    sigs = load_dbc(args.dbc)
    if not sigs:
        raise SystemExit(f"no signals in {args.dbc}")
    out = args.out or (Path(args.capture).stem + "_decoded.csv")
    rows = export_timeseries(df, sigs, out)
    print(f"decoded {rows} rows through {len(sigs)} signal(s) -> {out}")
    return 0


# ── convert ──────────────────────────────────────────────────────────────────

def write_savvycan_csv(df, path) -> int:
    """SavvyCAN's own layout, so the file opens there and reads back through
    CanLab's parser: microsecond timestamps, 8-digit hex IDs, two-digit hex
    bytes, and the trailing comma SavvyCAN writes."""
    with open(path, "w", newline="") as fh:
        fh.write("Time Stamp,ID,Extended,Dir,Bus,LEN,D1,D2,D3,D4,D5,D6,D7,D8\n")
        for _, r in df.iterrows():
            dlc = int(r["DLC"]) if r.get("DLC") == r.get("DLC") else 8
            cells = []
            for i in range(8):
                v = r.get(f"B{i}")
                cells.append(f"{int(v):02X}" if v == v and v is not None else "")
            fh.write(f"{int(round(float(r['Timestamp']) * 1e6))},"
                     f"{int(r['ID'], 16):08X},"
                     f"{'true' if r.get('Extended') else 'false'},Rx,"
                     f"{int(r.get('Bus', 0) or 0)},{dlc},{','.join(cells)},\n")
    return len(df)


def cmd_convert(args) -> int:
    df = _load(args.capture)
    out = Path(args.out)
    suffix = out.suffix.lower()
    if suffix == ".csv":
        write_savvycan_csv(df, out)
    elif suffix in (".blf", ".asc", ".log"):
        import can
        writer = {".blf": can.BLFWriter, ".asc": can.ASCWriter,
                  ".log": can.CanutilsLogWriter}[suffix](str(out))
        try:
            for _, r in df.iterrows():
                data = bytes(int(r[f"B{i}"]) & 0xFF for i in range(8)
                             if f"B{i}" in df.columns and r[f"B{i}"] == r[f"B{i}"])
                writer.on_message_received(can.Message(
                    timestamp=float(r["Timestamp"]), arbitration_id=int(r["ID"], 16),
                    data=data, is_extended_id=bool(r.get("Extended", False))))
        finally:
            writer.stop()
    else:
        raise SystemExit(f"cannot write {suffix}; use .csv, .blf, .asc or .log")
    print(f"wrote {len(df)} frames -> {out}")
    return 0


# ── entry ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="canlab-cli",
                                description="CanLab without the window.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("ids", help="list arbitration IDs with rate and moving bytes")
    s.add_argument("capture")
    s.set_defaults(fn=cmd_ids)

    s = sub.add_parser("detect", help="run every offline detector")
    s.add_argument("capture")
    s.add_argument("--json", help="write the full report here")
    s.add_argument("--dbc", help="write a draft DBC from the findings here")
    s.add_argument("--quiet", action="store_true", help="totals only")
    s.set_defaults(fn=cmd_detect)

    s = sub.add_parser("decode", help="decode a capture through a DBC to CSV")
    s.add_argument("capture")
    s.add_argument("--dbc", required=True)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_decode)

    s = sub.add_parser("convert", help="rewrite a capture as csv, blf, asc or log")
    s.add_argument("capture")
    s.add_argument("out")
    s.set_defaults(fn=cmd_convert)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
