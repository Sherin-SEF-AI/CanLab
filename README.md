# CanLab

**A desktop workstation for reverse-engineering a CAN bus.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python)](https://www.python.org)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-green?style=flat-square)](https://pypi.org/project/PyQt6/)
[![Tests](https://img.shields.io/badge/tests-305%20passing-brightgreen?style=flat-square)](#testing)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

Load a capture, work out which bytes carry what, write the signal definitions
down, check them against real frames, and export a DBC that other tools can
read. It also speaks the diagnostic protocols (UDS, ISO-TP, J1939, OBD-II, XCP,
DoIP), and, for isolated bench use only, can inject, replay, fuzz and bridge.

> **Status:** beta. Single-author project, actively developed. It runs, and the
> behaviour described here is covered by an automated suite of 305 tests (see
> [Testing](#testing)). But the analysis methods are heuristics that suggest
> candidates rather than identify signals, some features need optional
> dependencies, and it has not been validated across a wide range of real
> vehicles. Read [Limitations](#limitations) before relying on a result.

**Contents:** [Safety](#safety) · [Install](#install-and-run) · [Demo](#demo) ·
[Workflow](#a-typical-session) · [Tabs](#what-it-does-15-tabs) ·
[Log formats](#supported-log-formats) ·
[Analysis](#analysis-offline-no-api-key) ·
[Vehicle profiles](#vehicle-profiles) · [Diagnostics](#diagnostics) ·
[DBC](#dbc-ecosystem) · [Integrations](#integrations) ·
[Architecture](#how-it-fits-together) · [Testing](#testing) ·
[Limitations](#limitations)

---

## Safety

> CanLab can transmit on a CAN bus. **Use it only on isolated bench setups:** a
> benchtop ECU, `vcan0`, or dedicated lab hardware. Injecting or forwarding
> frames on a live vehicle bus can interfere with braking, steering and airbag
> systems.
>
> Four guards are built in:
>
> 1. A safety warning you have to accept on first launch. The acceptance is
>    remembered, so it appears once.
> 2. A global **ARM TX** toolbar toggle, **disarmed by default**. Nothing leaves
>    the tool until you arm it: not injection, replay, fuzzing or gateway
>    forwarding, and not diagnostic requests either (UDS, OBD-II, XCP, DoIP).
>    Every transmit path goes through one function, and a test asserts that each
>    of them stays silent while disarmed.
> 3. Disarming stops transmits that are already running. It does not merely
>    block the next frame: a fuzzer or replay in flight is asked to stop and
>    joined.
> 4. A blocked-ID list that is refused even while armed.
>
> The UDS service scan probes read-only services unless you tick "Include
> destructive services" and confirm.

---

## Install and run

```bash
git clone https://github.com/Sherin-SEF-AI/CanLab.git
cd CanLab
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ai,rest,mcp]"      # extras: ai, rest, mcp, mdf, dev, demo

canlab                                # or: python -m canlab
```

Python 3.11 or newer (developed and tested on 3.12). Keys for the optional AI
providers go in Settings → API Keys and are stored in the OS keyring; a local
Ollama server needs no key. Logs are written to `~/.canlab/logs/canlab.log`.

A sample capture ships with the package
(`canlab/sample_data/sample_kona_drive.csv`: 6,610 frames across 10 IDs over 10
seconds, from 1 Hz to 100 Hz) so every feature can be tried without hardware.

---

## Demo

A narrated walkthrough of every tab, recorded from the running application:
**[`docs/canlab-demo.mp4`](docs/canlab-demo.mp4)**, 1080p, 11 minutes, with
subtitles burned in and also separate in
[`docs/canlab-demo.srt`](docs/canlab-demo.srt).

It is generated rather than hand-recorded, so it cannot drift away from what the
application does. [`docs/demo/record.py`](docs/demo/record.py) drives a real
`MainWindow` under Qt's offscreen platform through 24 scenes, calling the same
slots the buttons call, so a scene that stops working fails the run instead of
quietly recording a stale screen.
[`docs/demo/build.py`](docs/demo/build.py) synthesises the narration, times each
scene's frames to its own audio, and muxes with ffmpeg.

```bash
pip install -e ".[demo]"                   # plus ffmpeg on PATH
QT_QPA_PLATFORM=offscreen python docs/demo/record.py    # about a minute
python docs/demo/build.py
```

Narration clips are cached by a digest of their own text, so editing one scene
re-voices that scene alone.

---

## A typical session

1. **Open a log.** FRAMES shows every frame in time order. A byte lights up when
   it changes, so the parts of a message that move are visible at a glance.
2. **Pick an ID.** The inspector gives you the last frames in hex, a per-byte
   activity bar, and minimum, maximum and mean per byte.
3. **Narrow it down.** AUTO-RE finds the rolling counters and checksum bytes,
   which are not signals, and entropy boundaries suggest where one field ends
   and the next begins. SIGNALS classifies each message; ML INTEL classifies
   each byte.
4. **Write the definition.** DBC BUILDER has a bit grid that converts between
   grid position and DBC bit numbering, which is the part that is easy to get
   wrong by hand.
5. **Check it.** The live preview decodes real frames from your capture through
   the definition. PLOT draws the decoded value over time, where a wrong byte
   order shows up as a sawtooth.
6. **Export.** DBC, openpilot DBC, CANdb++, ARXML or a Wireshark dissector. Each
   format is verified in the test suite by loading it back with the tool that
   has to read it.

If you have a physical reference for a signal, a GPS speed log for instance,
[reference calibration](#reference-driven-calibration) can search for the field
that fits it and report the scale and offset.

---

## What it does: 15 tabs

| # | Tab | What it does |
|---|---|---|
| 1 | **FRAMES** | Raw frame table with per-byte change highlighting, hex and bus filters, freeze and follow. |
| 2 | **SIGNALS** | Per-message classification: frame count, rate, payload entropy, suspected message type. |
| 3 | **PLOT** | Multi-signal time series. Raw bytes and decoded signals share a time axis, each with its own scale. |
| 4 | **AI ENGINE** | Send one ID's statistics to Anthropic, Groq or a local Ollama model. The offline findings go with the question. Memory persists across sessions. |
| 5 | **DBC BUILDER** | Visual signal editor with a bit grid and a live decode preview. Imports DBC, ARXML and CAN matrix; exports DBC, openpilot DBC, CANdb++, ARXML and Wireshark Lua. |
| 6 | **CODE GEN** | Generates Python or C that opens the bus and decodes or encodes your signals. |
| 7 | **INTELLIGENCE** | Message periodicity, cross-ID byte correlation with a lag sweep, capture diffing, J1939 PGN decode, value lookup. |
| 8 | **INJECTION** | Six sub-tabs: inject, replay with a scrubber, trigger rules, actuator sweep with a watchdog, fuzzer, scripted test sequences. Gated by ARM TX. |
| 9 | **DIAGNOSTICS** | Eight sub-tabs: OBD-II/UDS, UDS deep scan, UDS services, security access, bus load, bus health, XCP, DoIP. |
| 10 | **DASHBOARD** | Byte-activity heatmap across all messages, message timeline, gauges pointed at signals you have defined. |
| 11 | **AUTO-RE** | Counter and checksum detection, entropy boundaries, correlation, and a per-byte checksum algorithm guesser. Runs in worker threads. |
| 12 | **TIMELINE** | Several signals stacked on one scrubbable axis with a shared playhead, plus video sync with an adjustable offset. |
| 13 | **OBD-II** | Live PID gauges. Discovers supported PIDs by walking the continuation windows rather than assuming the first 32. |
| 14 | **ML INTEL** | Per-byte role classification with confidence, anomaly scoring against a fitted baseline, change-point detection, embedding similarity. |
| 15 | **GATEWAY** | Bridges two CAN channels with ordered pass, block and modify rules. Gated by ARM TX. |

---

## Supported log formats

| Format | Notes |
|---|---|
| SavvyCAN CSV | GVRET and SavvyCAN export. IDs and data bytes are read as hex. |
| candump `.log` | `candump -l` output, including CAN FD (`##`) lines. Error and remote frames are counted and skipped. |
| pcap / pcapng | Linux SocketCAN linktype 227, classic and FD, via dpkt. |
| Vector BLF | via python-can `BLFReader`. |
| Vector ASC | via python-can `ASCReader`. |
| MDF4 `.mf4` / `.mdf` | CANedge and similar. Needs `pip install canlab[mdf]`. |
| openpilot `.rlog` / `.qlog` | Needs pycapnp and the cereal `log.capnp` schema. Raises a clear error if either is missing. |

Every parser produces the same columns: `Timestamp, ID, Bus, DLC, Extended,
B0..B7`, widened to `B63` when FD frames are present, plus a per-ID `Delta`. The
parsers are tested against fixtures in the genuine formats, including
byte-order-mark and CRLF variants.

---

## Analysis (offline, no API key)

None of this sends anything anywhere.

| Feature | Module | Notes |
|---|---|---|
| Checksum algorithms | `core/checksums.py` | Parametrised CRC-8 plus OEM variants (Hyundai, Toyota, Honda, Subaru, AUTOSAR), checked against published check values and against commaai/opendbc. |
| Counter and checksum detection | `core/counter_checksum_detector.py` | Sweeps every message. Counters are whole-byte or per-nibble, with the modulus read from the values seen and reported only when a roll-over was actually observed. |
| Checksum algorithm guesser | `core/checksum_guesser.py` | Takes one message and one byte and scores all twelve algorithms, fitting on the first 70% of the capture and validating on the rest. Reports both numbers. |
| Byte role classifier | `core/signal_classifier.py` | COUNTER, CHECKSUM, BOOLEAN, PHYSICAL or PADDING per byte. |
| Cross-ID correlation | `core/correlation_engine.py` | Pearson r per byte pair, nearest-timestamp alignment, lag sweep. |
| Anomaly detection | `core/anomaly_detector.py` | Z-score per byte and Isolation Forest on the frame vector. |
| Entropy boundaries | `core/entropy_boundary.py` | Per-bit entropy to suggest where one field ends and the next begins. |
| Multiplexer detection | `core/mux_detector.py` | Finds a mode-selector byte and the bytes active in each mode. |
| Reference calibration | `core/reference_calibrate.py` | See [below](#reference-driven-calibration). |

**On checksum detection.** A byte counts as a checksum only if the relation
beats simply guessing that byte's most common value, so constant padding does
not qualify. A relation that holds across most of the payload is discarded
rather than reported: "is byte k the exclusive-or of the other seven?" is the
same question as "does the whole message exclusive-or to zero?", so a genuine
checksum and a payload that merely repeats each value an even number of times
both make all eight bytes match, and the data alone cannot say which byte it is.
On the shipped sample capture the detector finds exactly the 6 counters and 7
checksums the generator wrote, with the right byte and the right algorithm, and
reports nothing for the three messages that have none.

These remain **heuristics that suggest candidates**. A confidence figure is a
match fraction over the frames you loaded, not a proof. Verify before you trust.

---

## Vehicle profiles

Framing conventions are a setting, not an assumption. A profile says where a
rolling counter and checksum live and which algorithm computes the checksum. It
does not claim to identify your vehicle. The default, **generic**, adds neither.
Hyundai/Kia, Toyota, Honda, Subaru and AUTOSAR E2E ship as presets, selectable
in Settings → VEHICLE. The chosen profile drives injection stamping, the
openpilot export metadata, the generated code, and the framing hint given to the
AI.

---

## Reference-driven calibration

`core/reference_calibrate.py` searches for the CAN field (ID, byte range,
endianness) whose values best fit a **physical reference** by least squares, and
reports scale and offset with an R² verdict of PASS or UNCONFIRMED. The
reference is a CSV of `timestamp,value` (Tools → *Calibrate signal from
reference CSV*). Sentinel codes meaning "signal unavailable" are masked, and the
fitted scale is snapped to a neat value when that barely changes the decode.
Both refinements are adapted from CSS Electronics' reverse-engineering skills
(see [Acknowledgements](#acknowledgements)).

---

## Diagnostics

| Protocol | Module | Notes |
|---|---|---|
| ISO-TP (ISO 15765-2) | `core/isotp.py` | Single and multi-frame transmit with the flow-control handshake and STmin, reassembly, CAN FD escape frames, functional addressing. |
| UDS (ISO 14229) | `core/uds.py` | Read DTCs, read ECU identification, service scan (read-only by default), NRC 0x78 response-pending handling, periodic TesterPresent during long scans. |
| Security access | `core/security_access.py` | Seed and key algorithms, scripted key functions, rate-limited brute force that stops on the ECU's attempt-limit response. |
| J1939 | `core/j1939.py` | PGN decoding plus DM1 active-DTC decode (SPN, FMI, CM, OC). |
| OBD-II (SAE J1979) | `core/obd2_pids.py` | PID table and supported-PID discovery across continuation windows. |
| XCP over CAN | `core/xcp.py` | Read-only client (CONNECT, UPLOAD, SHORT_UPLOAD) and a measurement poller. No memory-write or programming commands are implemented. |
| DoIP (ISO 13400) | `core/doip.py` | Vehicle discovery, routing activation, UDS over IP, on stdlib sockets. |

Every diagnostic request goes through the ARM TX gate, reads included. One
receive thread dispatches frames to each consumer, so a diagnostic scan and the
live frame table never take responses from each other.

---

## DBC ecosystem

| Format | Import | Export |
|---|---|---|
| Standard DBC | Yes | Yes, parseable by cantools |
| openpilot DBC | Yes, as an opendbc cross-reference | Yes |
| Vector CANdb++ | No | Yes, with `BA_DEF_` blocks carrying cycle times measured from your capture |
| AUTOSAR ARXML 4.3 | Yes, via cantools | Yes, verified loadable by cantools |
| Wireshark Lua dissector | No | Yes |
| Excel/CSV CAN matrix | Yes | No |

Extended 29-bit IDs, multiplexed signals and value tables round-trip. All
decoding and encoding goes through one cantools-backed path, so the value the
preview shows is the value the exported file produces.

The Lua dissector extracts bits with plain arithmetic, using neither `bit32`
(removed in Lua 5.3) nor the 5.3+ bitwise operators (a syntax error on earlier
versions), so one file works across the Lua versions Wireshark ships. Its output
is tested against cantools for little-endian, big-endian and signed fields, and
executed under a real Lua runtime.

**opendbc matching** (Tools → *Match against opendbc*) fetches the
`commaai/opendbc` index, caches it under `~/.canlab/opendbc_cache`, and ranks
how well your capture's IDs match each OEM DBC. The first run needs network
access; afterwards it works from the cache.

---

## Integrations

| Capability | Module | Notes |
|---|---|---|
| REST API and live web dashboard | `core/rest_api.py` | Loopback only, token-authenticated. `GET /` serves a live-frames page; `/inject` also requires ARM TX. |
| MCP server | `mcp_server.py` | Exposes load_log, list_ids, byte_stats, detect_counters_checksums, correlate, match_opendbc, detect_multiplexers and calibrate as MCP tools. Run `canlab-mcp`. |
| Decoded time-series export | `core/timeseries_export.py` | A Timestamp-by-signal matrix to CSV or Parquet. |
| Plugin SDK | [`docs/PLUGINS.md`](docs/PLUGINS.md) | Documented `register(app)` API, an event bus and two example plugins. Plugin code does not run until you enable it in Settings. |
| Panda backend | `core/panda_backend.py` | comma.ai Panda as a python-can-compatible bus, with the safety model selectable. |

### REST API

Start it from the **REST API** toolbar toggle. It binds to `127.0.0.1:8765` and
prints a per-session token. Every data request needs an `X-API-Token` header.

```
GET  /            # live web dashboard (HTML, open)
GET  /frames      # last N frames  (?n=N)
GET  /signals     # decoded DBC signals
GET  /status      # connection state and frame count
GET  /memory      # AI memory entries
POST /inject      # inject a frame: needs the token AND ARM TX
                  # {"id":"0x200","data":"01 02 03 04 05 06 07 08"}
```

---

## How it fits together

Three pieces carry most of the design, and they are worth knowing if you are
reading the code or writing a plugin.

**One receive thread.** `core/bus_hub.py` is the only place that calls `recv()`.
It hands each frame to every subscriber that asked for it, filtered by ID. This
is why a UDS scan and the live table can run at the same time without taking
each other's frames. Workers are given a subscription rather than the bus.

**One transmit gate.** `core/safety.py` holds the armed flag, the blocked-ID
set, and a registry of running transmit workers. Every send goes through
`gated_send`. Disarming notifies observers and stops registered workers, rather
than only refusing the next frame.

**A capped ring buffer.** `core/frame_store.py` keeps frames in preallocated
NumPy arrays (500,000 by default, oldest dropped) and appends in constant time,
so a long capture does not get slower as it grows. The pandas DataFrame the rest
of the application reads is built on demand and cached until the next change.
Views that update live read only what they display: the frame table takes a
fixed tail, the inspector takes a per-ID tail, and the ID tree reads
incrementally maintained statistics rather than the frames themselves.

---

## Testing

```bash
pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen python -m pytest -q     # 305 passed, 1 skipped
ruff check canlab tests
```

The suite covers the log parsers against fixtures in the genuine formats; DBC
encode and decode round trips through cantools (little-endian, big-endian,
signed, extended IDs, multiplexing, value tables); the ARXML and Lua exports
loaded back by cantools and by a real Lua runtime; the checksum algorithms
against published check values; the ARM TX gate on every transmit path,
including that disarming stops a running worker; the receive dispatcher; the
frame store including its growth cost; ISO-TP and UDS wire format and NRC 0x78
handling; settings, plugin opt-in and project round trips; and an offscreen
smoke test that builds the real window, cycles every tab, runs a live capture
and asserts no thread is left running.

The single skip is the MDF4 parser, which needs the optional `asammdf` extra.

---

## Performance

Measured on the development machine (Linux, Python 3.12) with a capture of
150,000 frames across 16 IDs held in memory. Treat these as the shape of the
cost rather than a benchmark.

| Operation | Cost |
|---|---|
| Build the full DataFrame from the ring buffer | 5 ms |
| Fetch one ID's frames | 0.5 ms |
| Refresh the frame table, 1,000 rows on screen | 72 ms |
| Rebuild the plot signal tree, 16 IDs | 70 ms |

Live capture is drained on a timer and the views are throttled, so the cost per
batch stays flat as the capture grows. Memory is bounded by the ring buffer cap.

---

## Limitations

- **Not validated on many real vehicles.** Signal identification is heuristic.
  Verify every result before trusting it.
- The analysis suggests candidates. A confidence figure is a match fraction over
  the frames you loaded, not a statistical proof.
- **openpilot rlog import** needs pycapnp plus the cereal schema. Without them
  it raises rather than producing data.
- **MDF4** import needs `asammdf` (`pip install canlab[mdf]`).
- CAN FD is parsed and stored end to end, but FD transmit is exercised only on
  ISO-TP; there is no dedicated FD injection UI.
- The Gateway needs **two** hardware CAN channels.
- The AI features send the selected ID's frame statistics to whichever provider
  you configure. Nothing is sent until you enter a key and click Analyze, and a
  suggested signal is refused unless the response actually states a bit
  position.
- Plugins run with full application privileges once enabled. Only enable plugins
  you trust.
- No prebuilt binary is published here. Run from source.

---

## System requirements

- Linux, macOS or Windows with Python 3.11 or newer
- 4 GB RAM, 8 GB recommended for the machine-learning features
- Optional: SocketCAN for live hardware, a comma.ai Panda, or any
  [python-can](https://python-can.readthedocs.io) adapter (`socketcan`, `pcan`,
  `kvaser`, `virtual`, `serial`, `slcan` and others)

---

## Acknowledgements

The calibration refinements in `core/calibrate_refine.py`, sentinel masking and
scale snapping, are adapted from CSS Electronics'
[CAN bus reverse engineering skills](https://github.com/CSS-Electronics/can-bus-reverse-engineering-skills)
(MIT). The OEM checksum algorithms in `core/checksums.py` follow
[commaai/opendbc](https://github.com/commaai/opendbc) (MIT).

Built on [python-can](https://python-can.readthedocs.io),
[cantools](https://github.com/cantools/cantools), PyQt6, pandas, NumPy and
pyqtgraph.

---

## License

MIT. See [LICENSE](LICENSE).

**Author:** Sherin Joseph Roy
**Repository:** https://github.com/Sherin-SEF-AI/CanLab
