"""Run the command line tour for real and write down exactly what happened.

Nothing here is staged. Every command in the tour is executed against real
recordings on a pseudo-terminal, and the bytes it writes are captured with the
time they appeared, so the video can replay a real session rather than a
transcript somebody typed out afterwards.

The captures are other people's published data:

  candump.log, GVRET_Log.csv   a 12,974-frame car capture from SavvyCAN's
                               examples (collin80/SavvyCAN, MIT)
  canedge_*.MF4                CANedge logger recordings from CSS Electronics
                               (MIT): a J1939 truck, a marine NMEA 2000 bus,
                               a two-channel car
  pc_*.blf / .asc              python-can's own test files, for CAN FD

The capture-kit chapter is a genuine capture: one process replays the SavvyCAN
log onto a UDP multicast bus, `canlab-cli capture` records it without knowing
where the frames come from, and a mark is posted over HTTP while it runs.

    python docs/demo/record_cli.py [--data DIR] [--out transcript.json]

Requires the data directory prepared by ``fetch_cli_data.py``.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_DATA = Path("/tmp/claude-1000/tourdata")
DEFAULT_OUT = HERE / "build-cli" / "transcript.json"

#: python-can's default port for the udp_multicast interface. Both sides of
#: the capture chapter have to agree, and the kit passes only the channel.
MCAST_CHANNEL = "224.0.0.7"
HTTP_PORT = 8799
HTTP_TOKEN = "tour-demo-token"

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r(?!\n)")


def clean(text: str) -> str:
    """Terminal control sequences out; the renderer draws plain text."""
    return ANSI.sub("", text).replace("\x08", "")


class Step:
    """One command in the tour, with what it printed and when."""

    def __init__(self, cmd: str, *, chapter: str, caption: str = "",
                 note: str = "", pause: float = 1.2, cwd: str = ""):
        self.cmd = cmd
        self.chapter = chapter
        self.caption = caption
        self.note = note
        self.pause = pause
        self.cwd = cwd
        self.chunks: list[tuple[float, str]] = []
        self.exit = 0
        self.seconds = 0.0

    def as_dict(self) -> dict:
        return {"cmd": self.cmd, "chapter": self.chapter, "caption": self.caption,
                "note": self.note, "pause": self.pause, "cwd": self.cwd,
                "chunks": self.chunks, "exit": self.exit,
                "seconds": round(self.seconds, 3)}


def run(step: Step, data: Path, env: dict) -> Step:
    """Execute one step on a pty and record its output with timings."""
    print(f"  $ {step.cmd}", flush=True)
    master, slave = pty.openpty()
    t0 = time.monotonic()
    proc = subprocess.Popen(step.cmd, shell=True, cwd=str(data), env=env,
                            stdin=subprocess.DEVNULL, stdout=slave, stderr=slave,
                            close_fds=True, preexec_fn=os.setsid)
    os.close(slave)
    buf = b""
    while True:
        try:
            ready, _, _ = select.select([master], [], [], 0.2)
        except OSError:
            break
        if ready:
            try:
                data_read = os.read(master, 65536)
            except OSError as e:
                if e.errno != errno.EIO:
                    raise
                data_read = b""
            if not data_read:
                break
            buf += data_read
            text = clean(buf.decode("utf-8", "replace"))
            if text:
                step.chunks.append((round(time.monotonic() - t0, 3), text))
                buf = b""
        elif proc.poll() is not None:
            break
    os.close(master)
    step.exit = proc.wait()
    step.seconds = time.monotonic() - t0
    return step


def tour_steps(data: Path) -> list[Step]:
    """The script of the tour. Every path here is a file that exists."""
    S = Step
    return [
        # ── 1. what it is ────────────────────────────────────────────────────
        S("canlab-cli --help", chapter="overview",
          caption="Five commands. No window, no display, no API key.",
          note="A test asserts this process never imports Qt.", pause=2.6),

        # ── 2. what is on this bus ───────────────────────────────────────────
        S("ls -lh canedge_big.MF4 candump.log canedge_c.MF4", chapter="ids",
          caption="Real recordings: a J1939 truck, a car, a marine bus.",
          pause=1.6),
        S("canlab-cli ids canedge_big.MF4 | head -14", chapter="ids",
          caption="145,534 frames off a truck. Which IDs exist, how fast, "
                  "which bytes move.",
          note="CF00400 at 100 Hz with six bytes moving is the engine message.",
          pause=3.2),

        # ── 3. every format ──────────────────────────────────────────────────
        S("for f in candump.log GVRET_Log.csv canedge_c.MF4 css_multi.asc "
          "pc_test_CanFdMessage64.blf; do printf '%-28s' \"$f\"; "
          "canlab-cli ids \"$f\" | head -1; done", chapter="formats",
          caption="The same reader for candump, GVRET, MDF4, ASC and BLF.",
          note="Including a 64-byte CAN FD file from python-can's own tests.",
          pause=3.0),

        # ── 4. find the structure ────────────────────────────────────────────
        S("canlab-cli detect candump.log --json report.json --dbc draft.dbc",
          chapter="detect",
          caption="Every offline detector over 12,974 real frames.",
          note="Counters, checksums, bit flags, value tables, multiplexers.",
          pause=3.4),
        S("python -c \"import json;r=json.load(open('report.json'));"
          "print('messages with a checksum:', sum(1 for v in "
          "r['counters_checksums'].values() if v['checksums']));"
          "print('first:', [f\\\"{k} {c['col']} {c['algorithm']} \\\"\n"
          "  f\\\"{c['confidence']:.0%}\\\" for k,v in "
          "r['counters_checksums'].items() for c in v['checksums']][:3])\"",
          chapter="detect",
          caption="The report is JSON, so the findings are yours to query.",
          pause=2.8),

        # ── 5. decode ────────────────────────────────────────────────────────
        S("head -c 420 draft.dbc", chapter="decode",
          caption="It drafts a DBC from what it found, with no overlaps.",
          note="cantools loads it: overlapping claims are resolved first.",
          pause=2.6),
        S("canlab-cli decode candump.log --dbc draft.dbc --out decoded.csv",
          chapter="decode",
          caption="Decode the whole capture through those signals.", pause=2.0),
        S("cut -d, -f1-6 decoded.csv | head -6", chapter="decode",
          caption="A timestamp-by-signal matrix, ready for pandas or a plot.",
          pause=2.8),

        # ── 6. convert ───────────────────────────────────────────────────────
        S("canlab-cli convert canedge_c.MF4 marine.csv && "
          "canlab-cli ids marine.csv | head -4", chapter="convert",
          caption="Convert between formats, then prove the traffic survived.",
          note="MDF4 in, SavvyCAN CSV out: same 50 IDs, same 9,600 frames.",
          pause=3.0),

        # ── 7. the capture kit ───────────────────────────────────────────────
        S("canlab-cli capture --help | head -18", chapter="capture",
          caption="The headless logger for a computer left in a car.",
          note="Receive only. It never asks for privileges.", pause=3.0),
    ]


def capture_chapter(data: Path, env: dict) -> list[Step]:
    """The live capture: a real replay, a real recording, a real mark.

    The replay and the mark run beside the kit, so the recorded session shows
    frames arriving and an interval opening while the command is still going.
    """
    steps = []
    out = data / "drive"
    if out.exists():
        shutil.rmtree(out)

    sender = subprocess.Popen(
        [sys.executable, str(data / "replay_sender.py"), str(data / "candump.log"), "1.0"],
        cwd=str(data), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid)
    marker = subprocess.Popen(
        ["bash", "-c",
         f"sleep 5; curl -sS -X POST -H 'X-API-Token: {HTTP_TOKEN}' "
         f"-H 'Content-Type: application/json' -d '{{\"label\":\"brake\"}}' "
         f"http://127.0.0.1:{HTTP_PORT}/mark >/dev/null; sleep 2.5; "
         f"curl -sS -X POST -H 'X-API-Token: {HTTP_TOKEN}' "
         f"-H 'Content-Type: application/json' -d '{{\"label\":\"brake\"}}' "
         f"http://127.0.0.1:{HTTP_PORT}/mark >/dev/null"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
    try:
        steps.append(run(Step(
            f"canlab-cli capture --interface udp_multicast --channel {MCAST_CHANNEL} "
            f"--out drive --prefix drive --max-frames 6000 --duration 12 "
            f"--http {HTTP_PORT} --token {HTTP_TOKEN}",
            chapter="capture",
            caption="Recording a live bus. A phone posts the marks over HTTP.",
            note="Another process is replaying the SavvyCAN capture onto the bus.",
            pause=2.4), data, env))
    finally:
        for proc in (sender, marker):
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=20)

    steps.append(run(Step("ls drive/", chapter="capture",
                          caption="Rotating segments, the marks, and a project.",
                          pause=2.2), data, env))
    steps.append(run(Step("cat drive/marks.json", chapter="capture",
                          caption="The mark became an interval on the timeline.",
                          note="Ctrl-C or --duration closes whatever is open.",
                          pause=2.8), data, env))
    steps.append(run(Step(
        "python -c \"from canlab.core.project import load_project;"
        "from canlab.core.state import AppState;s=AppState();"
        "load_project(s,'drive/drive.canlab.zip');"
        "print(f'{len(s.frames_df)} frames, {s.frames_df[chr(34)+chr(34)] "
        "if False else s.frames_df[\\\"ID\\\"].nunique()} IDs, "
        "{len(s.annotations.items)} mark');"
        "a=s.annotations.items[0];print(f'{a.label}: {a.end-a.start:.2f} s')\"",
        chapter="capture",
        caption="The desktop opens that project with the mark in place.",
        pause=3.0), data, env))
    return steps


def final_steps() -> list[Step]:
    return [
        Step("canlab-mcp --help | head -12", chapter="assistants",
             caption="The same analysis over MCP: 29 tools, none of them transmit.",
             note="Claude, ChatGPT and Codex can drive the capture you loaded.",
             pause=3.2),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--skip-capture", action="store_true",
                    help="leave out the live bus chapter")
    args = ap.parse_args()

    data = Path(args.data)
    if not (data / "candump.log").is_file():
        raise SystemExit(f"no data in {data}; run docs/demo/fetch_cli_data.py first")

    env = dict(os.environ)
    env["COLUMNS"] = "104"
    env["LINES"] = "40"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("QT_QPA_PLATFORM", None)
    env["QT_QPA_PLATFORM"] = "offscreen"

    for stale in ("report.json", "draft.dbc", "decoded.csv", "marine.csv"):
        (data / stale).unlink(missing_ok=True)

    steps: list[Step] = []
    print("recording the tour")
    for step in tour_steps(data):
        steps.append(run(step, data, env))
    if not args.skip_capture:
        steps.extend(capture_chapter(data, env))
    for step in final_steps():
        steps.append(run(step, data, env))

    failures = [s for s in steps if s.exit != 0]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"steps": [s.as_dict() for s in steps]}, indent=1))
    total = sum(s.seconds for s in steps)
    print(f"\n{len(steps)} steps, {total:.1f} s of real command time")
    print(f"transcript: {out}")
    if failures:
        print("\nthese exited non-zero:")
        for s in failures:
            print(f"  [{s.exit}] {s.cmd}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
