"""Vendor the capnp schema openpilot logs are written in.

An openpilot rlog is a stream of capnp Event messages; reading one needs the
schema. It lives in two comma.ai repositories, both MIT licensed: cereal's
log.capnp and its neighbours in openpilot, and car.capnp in opendbc. This
copies them into canlab/core/data/cereal/ so an rlog opens with nothing
else installed but pycapnp.

    python tools/fetch_cereal.py [--ref master]

capnp is backward compatible for added fields, so a current schema reads
older logs; the acceptance run checks that on a 2021 drive.
"""
from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "canlab" / "core" / "data" / "cereal"
FILES = {
    "log.capnp": "openpilot/{ref}/openpilot/cereal/log.capnp",
    "custom.capnp": "openpilot/{ref}/openpilot/cereal/custom.capnp",
    "deprecated.capnp": "openpilot/{ref}/openpilot/cereal/deprecated.capnp",
    "include/c++.capnp": "openpilot/{ref}/openpilot/cereal/include/c++.capnp",
    "car.capnp": "opendbc/{ref}/opendbc/car/car.capnp",
}
NOTICE = """The capnp schema in this directory is copied from comma.ai's repositories:

  log.capnp, custom.capnp, deprecated.capnp, include/c++.capnp
      https://github.com/commaai/openpilot (openpilot/cereal)
      Copyright (c) 2018, Comma.ai, Inc.
  car.capnp
      https://github.com/commaai/opendbc (opendbc/car)
      Copyright (c) 2020, Comma.ai, Inc.

Both are released under the MIT License:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

The files are unmodified.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", default="master")
    args = ap.parse_args()
    for name, path in FILES.items():
        url = "https://raw.githubusercontent.com/commaai/" + path.format(ref=args.ref)
        dest = OUT / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=60) as r:
            dest.write_bytes(r.read())
        print(f"  {name:<22} {dest.stat().st_size:>7} bytes")
    (OUT / "NOTICE.txt").write_text(NOTICE)
    print(f"schema in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
