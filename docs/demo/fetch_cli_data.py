"""Collect the real recordings the command line tour runs against.

None of this data was produced here. It is fetched from the projects that
published it, or copied from the acceptance corpus if that is already on the
machine, so the tour demonstrates the tool against traffic it has never seen
rather than against something generated to suit it.

    python docs/demo/fetch_cli_data.py [--dir /tmp/tourdata]

Sources:
  SavvyCAN examples (collin80/SavvyCAN, MIT)      candump.log, GVRET_Log.csv
  CANedge recordings (CSS Electronics, MIT)       canedge_*.MF4
  python-can test files (hardbyte/python-can)     pc_*.blf, pc_*.asc, css_multi.asc

The CANedge and python-can files are large or awkward to fetch one by one, so
they are copied from ``~/Desktop/CanLab-real-data-new`` when it exists (the
acceptance corpus, whose own README says how to get them).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_DIR = Path("/tmp/claude-1000/tourdata")
CORPUS = Path.home() / "Desktop" / "CanLab-real-data-new"
REPO = Path(__file__).resolve().parents[2]

SAVVYCAN = "https://raw.githubusercontent.com/collin80/SavvyCAN/master/examples/"
DOWNLOAD = ["candump.log", "GVRET_Log.csv"]

#: Copied from the acceptance corpus when it is present. The tour degrades
#: gracefully: a file that is missing is simply not shown.
FROM_CORPUS = ["canedge_big.MF4", "canedge_c.MF4", "canedge_nissan.MF4",
               "css_multi.asc", "pc_test_CanFdMessage64.asc"]

REPLAY_SENDER = '''"""Replay a real capture onto a bus, one frame at a time, on its own clock.

The capture kit does not know where the frames come from: this is an ordinary
sender, and the kit is an ordinary receiver, in a different process.
"""
import sys
import time

import can

from canlab.core.log_parser import parse_log_file

df = parse_log_file(sys.argv[1])
speed = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
bus = can.Bus(interface="udp_multicast", channel="224.0.0.7")
t0 = float(df["Timestamp"].min())
start = time.time()
sent = 0
try:
    for r in df.itertuples():
        wait = start + (float(r.Timestamp) - t0) / speed - time.time()
        if wait > 0:
            time.sleep(wait)
        data = bytes(int(getattr(r, f"B{i}")) for i in range(int(r.DLC)))
        bus.send(can.Message(arbitration_id=int(r.ID, 16), data=data,
                             is_extended_id=bool(r.Extended)))
        sent += 1
finally:
    bus.shutdown()
    print(f"replayed {sent} frames", flush=True)
'''

SUMMARISE = '''"""What the detect report says about checksums, read back from the JSON."""
import json

report = json.load(open("report.json"))
found = [(cid, c) for cid, v in report["counters_checksums"].items()
         for c in v["checksums"]]
print(f"{report['capture']['frames']} frames, "
      f"{report['capture']['ids']} IDs, {report['capture']['span_s']} s")
print(f"{len(found)} checksum bytes across {len(report['counters_checksums'])} messages")
for cid, c in found:
    print(f"  {cid}  {c['col']}  {c['algorithm']:<12} {c['confidence']:.0%} confident")
'''

OPEN_PROJECT = '''"""Open the project the capture kit wrote, the way the desktop does."""
import sys

from canlab.core.project import load_project
from canlab.core.state import AppState

state = AppState()
load_project(state, sys.argv[1])
df = state.frames_df
print(f"{len(df):,} frames, {df['ID'].nunique()} IDs, "
      f"{df['Timestamp'].max() - df['Timestamp'].min():.1f} s")
for a in state.annotations.items:
    print(f"mark: {a.label}  {a.end - a.start:.2f} s long")
'''


def fetch(url: str, dest: Path) -> bool:
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"  have {dest.name}")
        return True
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            dest.write_bytes(r.read())
    except (urllib.error.URLError, OSError) as e:
        print(f"  could not fetch {dest.name}: {e}")
        return False
    print(f"  fetched {dest.name} ({dest.stat().st_size / 1e3:.0f} kB)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    args = ap.parse_args()
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    print(f"collecting the tour's data into {out}")

    ok = all(fetch(SAVVYCAN + name, out / name) for name in DOWNLOAD)
    if not ok:
        print("\nthe SavvyCAN examples are required; see tests/real_data/README.md")
        return 1

    for name in FROM_CORPUS:
        src = CORPUS / name
        if (out / name).is_file():
            print(f"  have {name}")
        elif src.is_file():
            shutil.copy2(src, out / name)
            print(f"  copied {name} from the acceptance corpus")
        else:
            print(f"  skipping {name} (not in {CORPUS})")

    sample = REPO / "canlab" / "sample_data" / "sample_kona_drive.csv"
    if sample.is_file():
        shutil.copy2(sample, out / sample.name)

    for name, text in (("replay_sender.py", REPLAY_SENDER),
                       ("summarise_report.py", SUMMARISE),
                       ("open_project.py", OPEN_PROJECT)):
        (out / name).write_text(text)
    print("  wrote the three helper scripts")

    have = sorted(p.name for p in out.iterdir() if p.suffix.lower()
                  in (".log", ".csv", ".mf4", ".asc", ".blf"))
    print(f"\n{len(have)} capture files ready:")
    for name in have:
        print(f"  {name:<32} {(out / name).stat().st_size / 1e3:>9.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
