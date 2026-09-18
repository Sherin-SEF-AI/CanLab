"""Headless command line for CanLab.

The analysis modules are Qt-free, so everything here runs without a display:
in CI over a folder of drives, from a notebook, or piped into something else.

    canlab-cli ids      capture.csv
    canlab-cli detect   capture.csv [--json report.json] [--dbc draft.dbc]
    canlab-cli decode   capture.csv --dbc signals.dbc [--out decoded.csv]
    canlab-cli convert  capture.blf out.csv
    canlab-cli capture  --interface socketcan --channel can0 [--http 8765] [--gpio 17=brake]

Every command reads any format the application does. ``detect`` runs the
counter and checksum sweep, entropy boundaries, bit-level flags, value-table
inference and multiplexer detection, and can write a draft DBC from what it
found. ``capture`` is the headless logger for a small computer in a car: it
writes rotating segments and event marks and folds them into a project the
desktop opens. Nothing here transmits, and nothing here asks for privileges.
"""
from __future__ import annotations

import argparse
import json
import os
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
    bytes, and the trailing comma SavvyCAN writes. The row goes through the
    same formatter the capture kit's segment writer uses, so a converted file
    and a recorded one are byte for byte the same layout."""
    from canlab.core.capture_writer import SAVVYCAN_HEADER, format_savvycan_row
    with open(path, "w", newline="") as fh:
        fh.write(SAVVYCAN_HEADER)
        for _, r in df.iterrows():
            dlc = int(r["DLC"]) if r.get("DLC") == r.get("DLC") else 8
            data = bytearray()
            for i in range(8):
                v = r.get(f"B{i}")
                if v == v and v is not None:
                    data.append(int(v) & 0xFF)
            fh.write(format_savvycan_row(float(r["Timestamp"]), int(r["ID"], 16),
                                         bool(r.get("Extended")), r.get("Bus", 0),
                                         dlc, bytes(data)))
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


# ── capture ──────────────────────────────────────────────────────────────────

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _resolve_adapter(args):
    """The adapter to open: a saved one by name, or the flags."""
    from canlab.core.adapters import Adapter, load_adapters_file
    if args.adapter:
        adapters = load_adapters_file(args.adapters_json)
        match = [a for a in adapters if a.name == args.adapter]
        if not match:
            names = ", ".join(a.name for a in adapters) or "none saved"
            raise SystemExit(f"no adapter named {args.adapter!r} (saved: {names})")
        return match[0]
    return Adapter(name=args.channel, interface=args.interface, channel=args.channel,
                   bitrate=int(args.bitrate), fd=bool(args.fd),
                   data_bitrate=int(args.data_bitrate or 2_000_000))


def _parse_key_map(text: str) -> dict[str, str]:
    """``"b=brake,h=horn"`` -> ``{"b": "brake", "h": "horn"}``."""
    out: dict[str, str] = {}
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item or len(item.split("=", 1)[0].strip()) != 1:
            raise SystemExit(f"--keys expects KEY=label with one character keys, got {item!r}")
        key, label = item.split("=", 1)
        out[key.strip()] = label.strip()
    return out


def _key_reader(session, keymap: dict[str, str], stop) -> None:
    """One key per mark, no Enter. The terminal is put into cbreak mode by the
    caller, which also restores it."""
    while not stop.is_set():
        ch = sys.stdin.read(1)
        if not ch:
            break
        if ch in ("q", "\x03", "\x04"):
            stop.set()
            break
        label = keymap.get(ch)
        if label:
            session.mark(label, "toggle")


def cmd_capture(args) -> int:
    import signal
    import threading
    from canlab.core.capture_kit import (
        CaptureOptions, CaptureSession, build_project, gpio_marker, mark_curl_example,
        parse_pin_map, stdin_marker, summary_text, write_token_file,
    )

    adapter = _resolve_adapter(args)
    if adapter.interface == "socketcan":
        from canlab.core import privileged
        st = privileged.interface_state(str(adapter.channel))
        if not st["up"]:
            cmd = " ".join(privileged.bring_up_command(
                str(adapter.channel), adapter.bitrate, fd=adapter.fd,
                data_bitrate=adapter.data_bitrate))
            why = (st["detail"].splitlines()[0] if st["detail"] else "no such device")
            print(f"{adapter.channel} is not up ({why}).\n"
                  f"Bring it up first, as root:\n  {cmd}\n"
                  "The capture kit never asks for privileges itself.", file=sys.stderr)
            return 2
    try:
        pins = parse_pin_map(args.gpio) if args.gpio else {}
    except ValueError as e:
        raise SystemExit(f"--gpio: {e}")
    keymap = _parse_key_map(args.keys) if args.keys else {}

    out_dir = Path(args.out or f"capture-{time.strftime('%Y%m%d-%H%M%S')}")
    opts = CaptureOptions(out_dir=out_dir, prefix=args.prefix, max_frames=args.max_frames,
                          max_seconds=args.max_seconds,
                          project_path=Path(args.project) if args.project else None,
                          no_project=bool(args.no_project))

    import can
    try:
        bus = can.Bus(**adapter.bus_kwargs())
    except Exception as e:
        raise SystemExit(f"could not open {adapter.describe()}: {e}")

    def log(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    session = CaptureSession(bus, opts, name=adapter.name, bitrate=adapter.bitrate, log=log)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):        # not the main thread
            pass

    server = None
    if args.http:
        from canlab.core.rest_api import RestAPIServer
        token = (args.token or "").strip()
        token_path = Path(args.token_file) if args.token_file else None
        if not token and token_path is not None and token_path.is_file():
            token = token_path.read_text().strip()
        remote = args.http_host not in LOOPBACK
        if remote:
            log(f"HTTP marks on {args.http_host}: anyone on that network with the "
                "token can read frames and add marks. /inject is not served.")
        server = RestAPIServer(session.state_getter, host=args.http_host, port=int(args.http),
                               token=token or None, allow_remote=remote,
                               expose_inject=False, on_mark=session.mark)
        try:
            server.start()
        except Exception as e:
            bus.shutdown()
            raise SystemExit(f"could not start the HTTP server: {e}")
        if token_path is not None and not token_path.is_file():
            write_token_file(token_path, server.token)
        log("marks: " + mark_curl_example(args.http_host, server.port, server.token))

    for note in gpio_marker(session, pins):
        log(note)

    termios_state = None
    tty_keys = bool(keymap) and sys.stdin is not None and sys.stdin.isatty()
    if tty_keys:
        import termios
        import tty
        fd = sys.stdin.fileno()
        termios_state = (fd, termios.tcgetattr(fd))
        tty.setcbreak(fd)
        log("keys: " + ", ".join(f"{k} toggles {v}" for k, v in keymap.items()) + ", q stops")
        threading.Thread(target=_key_reader, args=(session, keymap, stop),
                         daemon=True, name="canlab-keys").start()
    elif sys.stdin is not None:
        if keymap:
            log("stdin is not a terminal; type a label and Enter to mark, q to stop")
        threading.Thread(target=stdin_marker, args=(session, sys.stdin, stop),
                         daemon=True, name="canlab-stdin").start()

    try:
        summary = session.run(stop, duration_s=args.duration)
    finally:
        if server is not None:
            server.stop()
        if termios_state is not None:
            import termios
            termios.tcsetattr(termios_state[0], termios.TCSADRAIN, termios_state[1])
        try:
            bus.shutdown()
        except Exception:
            pass

    print(summary_text(summary))
    if opts.no_project:
        print(f"segments and marks in {out_dir}")
        return 0
    project = opts.project_path or (out_dir / f"{opts.prefix}.canlab.zip")
    n = build_project(session.writer.segments, session.marks.annotations, project,
                      meta={"adapter": adapter.describe(), "capture": summary})
    print(f"project: {project} ({n} frames, {summary['marks']} marks)")
    from canlab.core.frame_store import DEFAULT_CAP
    if n > DEFAULT_CAP:
        print(f"note: the desktop keeps the newest {DEFAULT_CAP:,} frames of a loaded "
              "capture; open one segment for an exact window", file=sys.stderr)
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

    s = sub.add_parser("capture", help="record a bus headless, with event marks",
                       description="Record a bus into rotating SavvyCAN CSV segments "
                                   "with event marks from the keyboard, HTTP or GPIO, "
                                   "then fold them into a .canlab project. Receive "
                                   "only; never asks for privileges.")
    bus = s.add_argument_group("bus")
    bus.add_argument("--interface", default="socketcan", help="python-can interface (default socketcan)")
    bus.add_argument("--channel", default="can0")
    bus.add_argument("--bitrate", type=int, default=500_000)
    bus.add_argument("--fd", action="store_true", help="CAN FD")
    bus.add_argument("--data-bitrate", type=int, default=None)
    bus.add_argument("--adapter", help="a saved adapter by name, instead of the flags above")
    bus.add_argument("--adapters-json", help="the adapters file (default ~/.canlab/adapters.json)")
    files = s.add_argument_group("files")
    files.add_argument("--out", help="directory for segments and marks (default capture-<time>)")
    files.add_argument("--prefix", default="capture")
    files.add_argument("--max-frames", type=int, default=200_000, help="frames per segment")
    files.add_argument("--max-seconds", type=float, default=600.0, help="seconds per segment")
    files.add_argument("--duration", type=float, default=None, help="stop after this many seconds")
    files.add_argument("--project", help="write the project here (default <out>/<prefix>.canlab.zip)")
    files.add_argument("--no-project", action="store_true", help="leave the segments and marks only")
    marks = s.add_argument_group("marks")
    marks.add_argument("--keys", help="single-key toggles on a terminal, e.g. b=brake,h=horn")
    marks.add_argument("--http", type=int, default=None, metavar="PORT",
                       help="serve /mark, /frames and /status on this port")
    marks.add_argument("--http-host", default="127.0.0.1",
                       help="bind address; anything but loopback is announced")
    marks.add_argument("--token", help="bearer token for the HTTP server")
    marks.add_argument("--token-file", help="read the token here, or write the generated one")
    marks.add_argument("--gpio", help="pins that mark while pressed, e.g. 17=brake,27=horn")
    s.set_defaults(fn=cmd_capture)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except BrokenPipeError:
        # `canlab-cli ids capture.csv | head` closes the pipe while the listing
        # is still being written. That is the reader saying it has enough, not
        # an error here, but unbuffered stdout turns it into a traceback and a
        # non-zero exit. Point what is left at /dev/null so the interpreter's
        # own flush at shutdown cannot raise it again.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
