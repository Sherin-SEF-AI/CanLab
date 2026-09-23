"""Distil canboat's NMEA 2000 definitions into the table CanLab decodes with.

canboat (github.com/canboat/canboat, Apache License 2.0, Kees Verruijt)
publishes a machine-readable definition of nearly every NMEA 2000 PGN, built
from years of reverse engineering real buses. This keeps the part CanLab
needs: each standard PGN's name, whether it is a single frame or a fast
packet, and its fixed-position fields with resolution, sign, unit, valid
range and lookup table.

    python tools/build_n2k_table.py [--source canboat.json] [--tag vX.Y.Z]

Writes canlab/core/data/n2k_pgns.json and the licence notice beside it.
Proprietary PGNs are left out: canboat defines several per PGN, one per
manufacturer, and choosing among them needs the manufacturer code, which a
table this simple cannot do honestly. So are fields after the first
variable-length one, because their positions depend on the data.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "canlab" / "core" / "data" / "n2k_pgns.json"
NOTICE = REPO / "canlab" / "core" / "data" / "CANBOAT-NOTICE.txt"
URL = "https://raw.githubusercontent.com/canboat/canboat/{tag}/docs/canboat.json"

DECODED = {"NUMBER", "LOOKUP", "DURATION", "TIME", "DATE", "MMSI", "PGN",
           "ADDRESS", "STRING_FIX", "INDIRECT_LOOKUP"}


def proprietary(pgn: int) -> bool:
    return (pgn == 0xEF00 or 0xFF00 <= pgn <= 0xFFFF or pgn == 126720
            or 130816 <= pgn <= 131071 or 0x1EF00 <= pgn <= 0x1EFFF)


def build(src: dict) -> dict:
    by_pgn: dict[int, list] = {}
    for p in src["PGNs"]:
        by_pgn.setdefault(p["PGN"], []).append(p)
    used_lookups: set[str] = set()
    table = {}
    skipped = {"proprietary": 0, "ambiguous": 0}
    for pgn, defs in sorted(by_pgn.items()):
        if proprietary(pgn):
            skipped["proprietary"] += 1
            continue
        if len(defs) > 1:
            skipped["ambiguous"] += 1
            continue
        p = defs[0]
        if p.get("Type") not in ("Single", "Fast"):
            continue
        fields = []
        # A repeating group (satellites, AIS slots, ...) has a count field and
        # then N copies of a set of fields. Decoding the first copy as if it
        # were the only one would present one satellite's PRN as "the" PRN,
        # so the table stops where the group starts.
        repeat_at = min((p[k] for k in ("RepeatingFieldSet1StartField",
                                          "RepeatingFieldSet2StartField") if p.get(k)),
                        default=None)
        for f in p.get("Fields", []):
            if repeat_at is not None and f.get("Order", 0) >= repeat_at:
                break
            if f.get("BitOffset") is None:
                break                        # variable length: positions unknown
            kind = f.get("FieldType")
            if kind not in DECODED:
                continue
            lookup = f.get("LookupEnumeration")
            if lookup:
                used_lookups.add(lookup)
            fields.append([
                f["Name"], f["BitOffset"], f["BitLength"], f.get("Resolution") or 1,
                bool(f.get("Signed")), f.get("Unit") or "", kind,
                f.get("RangeMin"), f.get("RangeMax"), f.get("Offset") or 0, lookup or "",
            ])
        table[str(pgn)] = {"name": p["Description"], "fast": p["Type"] == "Fast",
                           "fields": fields}
    lookups = {}
    for e in src.get("LookupEnumerations", []):
        if e["Name"] in used_lookups:
            lookups[e["Name"]] = {str(v["Value"]): v["Name"] for v in e["EnumValues"]}
    return {"source": f"canboat {src.get('Version', '')}",
            "licence": "Apache-2.0, see CANBOAT-NOTICE.txt",
            "pgns": table, "lookups": lookups, "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", help="a local canboat.json instead of downloading")
    ap.add_argument("--tag", default="master", help="canboat git ref to download")
    args = ap.parse_args()
    if args.source:
        src = json.loads(Path(args.source).read_text())
    else:
        with urllib.request.urlopen(URL.format(tag=args.tag), timeout=120) as r:
            src = json.loads(r.read())
    table = build(src)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(table, separators=(",", ":"), ensure_ascii=False))
    NOTICE.write_text(
        "canlab/core/data/n2k_pgns.json is derived from docs/canboat.json in\n"
        "CANboat, https://github.com/canboat/canboat\n\n"
        f"{src.get('Copyright', '(C) 2009-2026, Kees Verruijt, Harlingen, The Netherlands.')}\n\n"
        "Licensed under the Apache License, Version 2.0 (the \"License\");\n"
        "you may not use this file except in compliance with the License.\n"
        "You may obtain a copy of the License at\n\n"
        "    http://www.apache.org/licenses/LICENSE-2.0\n\n"
        "Unless required by applicable law or agreed to in writing, software\n"
        "distributed under the License is distributed on an \"AS IS\" BASIS,\n"
        "WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n"
        "See the License for the specific language governing permissions and\n"
        "limitations under the License.\n\n"
        "Changes: kept each standard PGN's name, frame type and fixed-position\n"
        "fields, and the lookup tables those fields use; dropped proprietary\n"
        "PGNs and fields whose position depends on earlier variable-length data.\n")
    n_fields = sum(len(p["fields"]) for p in table["pgns"].values())
    print(f"{len(table['pgns'])} PGNs, {n_fields} fields, {len(table['lookups'])} lookups "
          f"from {table['source']}; skipped {table['skipped']}")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e3:.0f} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
