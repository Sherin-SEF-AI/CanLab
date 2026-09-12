# Real-data acceptance run

The unit suite uses small fixtures and a generated sample. This runs the whole
application against a **real vehicle capture** instead: 12,974 frames, 180
distinct arbitration IDs, control messages at 110 to 117 Hz.

Synthetic data hides things. The generated sample has 10 IDs and a SUM8
checksum, which is exactly what the counter and checksum sweep tested for, so
the sweep looked correct. Pointed at a real bus it reported **no checksums at
all**, because real vehicles use CRC-8 and the sweep only knew four algorithms.
That is the kind of defect this run exists to catch.

## Getting the data

The capture is published with SavvyCAN (MIT). It is not committed here, because
it belongs to that project.

```bash
cd tests/real_data
for f in candump.log GVRET_Log.csv; do
  curl -O "https://raw.githubusercontent.com/collin80/SavvyCAN/master/examples/$f"
done
```

`GVRET_Log.csv` and `candump.log` are the same capture in two formats. Use the
CSV as the primary: the candump export has been rounded to whole seconds, so
every frame in a second shares a timestamp and anything timing-related is
meaningless on it.

To exercise the binary parsers, re-container the same real frames. The traffic
is unchanged; only the file format differs.

```python
import can, dpkt, re, struct, pathlib
frames = [(float(m[1]), int(m[3], 16), bytes.fromhex(m[4]), len(m[3]) > 3)
          for m in (re.match(r"\((\d+\.\d+)\)\s+(\S+)\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)$", ln)
                    for ln in pathlib.Path("candump.log").read_text().splitlines()) if m]
msgs = [can.Message(timestamp=t, arbitration_id=i, data=d, is_extended_id=e)
        for t, i, d, e in frames]
with can.BLFWriter("real_capture.blf", channel=1) as w:
    for m in msgs: w.on_message_received(m)
with can.ASCWriter("real_capture.asc") as w:
    for m in msgs: w.on_message_received(m)
with open("real_capture.pcap", "wb") as fh:                 # SocketCAN, link type 227
    pw = dpkt.pcap.Writer(fh, linktype=227)
    for t, i, d, e in frames:
        pw.writepkt(struct.pack(">IB3x", i | (0x80000000 if e else 0), len(d))
                    + d.ljust(8, b"\0"), ts=t)
```

## Running it

```bash
QT_QPA_PLATFORM=offscreen python tests/real_data/acceptance_phase1.py tests/real_data
QT_QPA_PLATFORM=offscreen python tests/real_data/acceptance_phase2.py tests/real_data
```

Phase 1 covers loading, the panels, and every offline analysis. Phase 2 covers
decoding, all six export formats round-tripped through the tool that has to
read them, code generation, the transmit gate, and the REST and MCP
integrations. Each check prints what it actually observed, not just pass or
fail, so a regression tells you what the application did.

## What a good run looks like

```
phase 1: 14/14 passed
  12974 frames, 180 unique IDs
  25 counters, 5 checksums across 19 messages
  92 candidate fields across 50 messages
  periodicity for 80 IDs, 2 multiplexed messages
phase 2: 11/11 passed
  every export loaded back by cantools
  disarmed sent 0 frames, armed sent 1
```

All five parsers must agree on the same capture: 12,974 frames and 180 IDs from
the CSV, the candump log, BLF, ASC and pcap alike. A disagreement means one
parser is wrong, and the format that disagrees is the one to look at.
