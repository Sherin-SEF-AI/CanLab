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
for p in 1 2 3; do
  QT_QPA_PLATFORM=offscreen python tests/real_data/acceptance_phase$p.py tests/real_data
done
```

Phase 1 covers loading, the panels, and every offline analysis. Phase 2 covers
decoding, all six export formats round-tripped through the tool that has to
read them, code generation, the transmit gate, and the REST and MCP
integrations. Phase 3 is everything the first two left out: MDF4, the project
round trip, each transmit worker armed and disarmed, plugin approval, settings,
opendbc matching, and a full ISO-TP request and response against a scripted ECU
responder on a virtual bus. Each check prints what it actually observed, not just pass or
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
phase 3: 11/11 passed
  3000 real frames survive an MDF4 round trip
  replay, fuzzer and sweep each send 0 disarmed
  ECU answers OBD-II and UDS over ISO-TP
```

## A second corpus: other people's hardware

The run above uses one capture, all 11-bit, one bus, re-containered by this
project into the binary formats. That cannot see a bug in any extended-ID
path, any multi-bus path, or in the MDF4 reader, because those were only ever
given files this repository wrote itself.

`acceptance_new_sources.py` uses real device recordings instead, none of them
produced here:

| File | Frames | IDs | Span | Notes |
|---|---|---|---|---|
| `canedge_a.MF4` | 2,010 | 2 | 299 s | slow OBD/GPS log |
| `canedge_b.MF4` | 5,588 | 12 | 64 s | two channels |
| `canedge_c.MF4` | 9,600 | 50 | 60 s | NMEA 2000 (marine), every frame 29-bit |
| `canedge_big.MF4` | 145,534 | 142 | 196 s | J1939, every frame 29-bit |
| `canedge_nissan.MF4` | 154,896 | 16 | 1,369 s | 23 minutes, two channels |

Those are CANedge logger recordings in native MDF4, from
[CSS-Electronics/api-examples](https://github.com/CSS-Electronics/api-examples)
and
[canedge-influxdb-writer](https://github.com/CSS-Electronics/canedge-influxdb-writer)
(MIT). Alongside them, Vector BLF and ASC written by
[python-can](https://github.com/hardbyte/python-can/tree/develop/test/data)'s
own writers cover CAN FD, 64-byte FD, error frames, extended error frames,
remote frames and a comma-decimal locale, and a multi-bus ASC comes from
[mdf4-converters](https://github.com/CSS-Electronics/mdf4-converters).

```bash
python tests/real_data/acceptance_new_sources.py <data-dir>
```

Then the real marine NMEA 2000 log goes through every writer the application has and is
read back by every parser, so all five formats have to agree about the same
traffic with 29-bit IDs. Nothing transmits: no bus is opened at all.

### What it found

Two real defects that the old corpus could not reach.

- The **openpilot DBC exporter** wrote the bare frame id for a 29-bit
  message. A DBC needs `id | 0x80000000`, so every J1939 capture exported an
  unloadable file. The other four exporters were correct. Pinned by
  `tests/test_export_extended_ids.py`, which also gave the openpilot and
  CANdb++ writers their first unit coverage.
- The **sniffer** aged a loaded capture against wall-clock time, so every row
  expired the instant a file was opened. A file has no "now"; the newest frame
  is the present.

```
57/57 passed, 1 skipped
  5 real MDF4 recordings, 2 to 154,896 frames, 29-bit and dual-bus
  14 native BLF/ASC files including 64-byte FD and error frames
  9,600 real 29-bit frames identical across csv, blf, asc, log and pcap
  every detector, every exporter, the sniffer, the splitter, the MCP tools
  and the GVRET codec, over data none of them had seen
  60 GNSS fixes and 60 satellite lists reassembled from the marine log,
  85 BAM broadcasts from the truck, nothing dropped
```

### Multi-frame messages

`phase_multiframe` reassembles the marine log's NMEA 2000 fast packets (PGN
129029, 60 messages of 43 bytes, decoded to a position that agrees with the
single-frame rapid-position message, the date 2021-03-25 and an altitude of
173.5 m; PGN 129540, 60 messages of 135 bytes naming 11 satellites) and the
truck log's J1939 BAM broadcasts (46 of PGN 65251 from SA 0x00 and 39 of PGN
65249 from SA 0x0F), with nothing dropped and in under 2 s for 145,534
frames. No recording in the corpus contains an RTS/CTS session, so that path
is tested synthetically in the unit suite.

### A private capture

`phase_blocks` runs the repeated-block detector over a capture of the
author's own car, which is not published. It is skipped unless
`CANLAB_PRIVATE_DATA` names the directory holding `tatatigor.canlab.zip`:

```bash
CANLAB_PRIVATE_DATA=~/Documents/can-data \
    python tests/real_data/acceptance_new_sources.py <data-dir>
```

It expects the 27-message block 0x380 to 0x39A at 2 Hz with DLC 8 and nine
constant members, the four-message run 0x244, 0x245, 0x247, 0x249 with a gap
of two, every candidate name containing `CANDIDATE`, and the scan under 10 s
over 460,024 frames.

### The live watch in the stress run

`acceptance_stress.py` gained `phase_live_watch`: fit the first 60 s of the
two-channel car log, replay the remaining 149,800 frames in 2,000-frame
batches, require every batch under 20 ms, and report the events by kind
without asserting them (a per-byte baseline fitted on one minute flags the
range changes of the next 22).

### J1939 values that must agree

`phase_j1939` in the stress run checks the J1939 layouts on the truck by
readings that have to agree with each other rather than with an expected
number: the brakes' EBC2 front axle speed against the engine's CCVS
wheel-based speed (0.33 km/h apart over 1,957 pairs, r 0.9997); coolant, oil,
barometer, battery and fuel level each physical and each from the message
J1939-71 puts it in; absolute inlet pressure minus boost pressure equal to the
barometer; VD and HRVD distance within the coarser counter's 125 m step; and
lifetime distance over lifetime fuel against the ECU's own average economy.

### Reference calibration on real cars

`phase_reference` fetches comma.ai's comma2k19 example segment (MIT, about
6 MB) into `<data-dir>/comma2k19`: a minute of a Toyota RAV4's CAN bus, a
u-blox receiver, and openpilot's decoded speed. From the GNSS speed alone the
calibrator must find 0x0B4 bytes 5-6 and the four 0x0AA wheel words, return
the wheel scale as 0.01 km/h (openpilot's DBC), place a UTC-stamped reference
within 0.3 s of the true clock offset, and write a signal that decodes the car
within 2% of openpilot.

`phase_openpilot` fetches a 2021 RAV4 drive from openpilot's public CI routes
(8 MB, `rlog.bz2`) into `<data-dir>/openpilot`, opens it with the bundled
schema (received frames and the panda's own transmissions must add up to
pycapnp's count), and calibrates it against its own GPS. Both phases skip,
rather than fail, when the download is not possible or pycapnp is missing.

## What this does not cover

No real hardware is involved, so none of the following is verified here: a real
ECU answering a UDS scan or a security-access seed, a physical CAN adapter, the
gateway (which needs two channels), CAN FD (the capture is classic CAN), the AI
providers (no key), and the GUI as a person drives it, since these scripts call
the same slots the buttons call rather than clicking. One physical adapter has
been used outside these scripts: a CANalyst-II on a Tata Tigor EV.

All five parsers must agree on the same capture: 12,974 frames and 180 IDs from
the CSV, the candump log, BLF, ASC and pcap alike. A disagreement means one
parser is wrong, and the format that disagrees is the one to look at.
