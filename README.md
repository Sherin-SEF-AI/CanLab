# CanLab

**A desktop (PyQt6) tool for reverse-engineering CAN bus data.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python)](https://www.python.org)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-green?style=flat-square)](https://pypi.org/project/PyQt6/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

Load a capture, inspect frames and signals, run offline analysis to find
counters, checksums and signal boundaries, optionally get AI help interpreting
an ID, and build and export a DBC. It also includes diagnostics (UDS, ISO-TP,
J1939, OBD-II) and, for isolated bench use only, injection, replay, fuzzing and
a man-in-the-middle gateway.

> **Status:** actively developed, single-author project. It runs and is covered
> by an automated test suite (see [Testing](#testing)), but treat it as
> **alpha**: some features need optional dependencies, some analysis methods are
> heuristics (see [Limitations](#limitations)), and it has not been validated
> across a wide range of real vehicles.

---

## Safety

> The **INJECTION** and **GATEWAY** features transmit frames onto a bus.
> **Use them only on isolated bench setups:** a benchtop ECU, `vcan0`, or
> dedicated lab hardware. Injecting or forwarding frames on a live vehicle bus
> can interfere with braking, steering and airbag systems.
>
> Built-in guards:
>
> - A safety warning you have to accept on first launch. The acceptance is
>   remembered, so it appears once.
> - A global **ARM TX** toolbar toggle, **disarmed by default**. These paths
>   refuse to transmit until you arm it, and re-check on every frame, so
>   disarming stops a run that is already going: signal injection, replay, the
>   fuzzer, trigger-driven sends, scripted test sequences, the actuator sweep,
>   gateway forwarding, UDS Clear DTC, and the REST `/inject` endpoint.
> - The UDS service scan probes only read-only services unless you tick
>   "Include destructive services" and confirm.
>
> **What the gate does not cover.** Ordinary diagnostic reads put request frames
> on the bus without checking ARM TX: the UDS scans and DID reads, OBD-II
> polling, and ISO-TP requests. This is deliberate, on the grounds that they
> only read ECU state, but it does mean "disarmed" is not the same as "silent".
> Disconnect the bus if you need the tool to emit nothing at all.

---

## Documentation

Full documentation, with the walkthrough videos playing in the page:
**[sherin-sef-ai.github.io/CanLab](https://sherin-sef-ai.github.io/CanLab/)**

Installation, the reverse-engineering workflow end to end, every tab, how the
analysis actually works, the diagnostics protocols, the export formats, the
integrations, and the safety model.

---

## Run from source

This is the supported, verified way to run it.

```bash
git clone https://github.com/Sherin-SEF-AI/CanLab.git
cd CanLab
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Optional AI providers. The app works fully offline without any of them:
export ANTHROPIC_API_KEY="sk-ant-..."   # Anthropic
export GROQ_API_KEY="gsk_..."           # Groq
# or run a local Ollama server for offline AI (no key)

cd canlab            # source root, imports are relative to here
python3 main.py
```

Or download the Linux x86_64 build from the
[releases page](https://github.com/Sherin-SEF-AI/CanLab/releases), which needs
no Python installation:

```bash
tar -xzf CanLab-1.3.0-linux-x86_64.tar.gz
cd CanLab/
./CanLab
```

Python 3.11 or newer (developed and tested on 3.12).

---

## What it does: 15 tabs

All 15 tabs build and render. An automated smoke test loads the bundled sample
log and cycles every one of them.

| # | Tab | What it does |
|---|---|---|
| 1 | **FRAMES** | Raw frame table with per-byte change highlighting, hex and bus filters, freeze and follow. |
| 2 | **SIGNALS** | DBC-decoded signal table: physical value, unit, entropy, suspected byte role. |
| 3 | **PLOT** | Multi-signal time series, per-byte traces, mouse-wheel zoom. |
| 4 | **AI ENGINE** | Send an ID to Anthropic, Groq or a local Ollama model. The offline findings are injected into the prompt. Memory persists across sessions. |
| 5 | **DBC BUILDER** | Visual signal editor. Imports DBC, ARXML and CAN matrix; exports DBC, openpilot DBC, CANdb++, ARXML (experimental) and Wireshark Lua. |
| 6 | **CODE GEN** | Generates Python or C parsing code from DBC definitions. |
| 7 | **INTELLIGENCE** | Cross-ID byte Pearson correlation with a lag sweep, embedding similarity, fingerprinting. |
| 8 | **INJECTION** | Six sub-tabs: inject, replay with a scrubber, trigger rules, actuator sweep with a watchdog, fuzzer, scripted test sequences. Gated by ARM TX. |
| 9 | **DIAGNOSTICS** | Six sub-tabs: OBD-II/UDS, UDS deep scan, UDS services, security access, bus load, bus health. |
| 10 | **DASHBOARD** | Byte-value heatmap, message timeline, physical overlay gauges. |
| 11 | **AUTO-RE** | Counter and checksum detection and entropy-boundary analysis across IDs, in worker threads. |
| 12 | **TIMELINE** | Scrubbable multi-ID event timeline plus a video-sync sub-tab. |
| 13 | **OBD-II** | Live PID gauge grid. Discovers supported PIDs across the continuation windows. |
| 14 | **ML INTEL** | Byte-role classification, anomaly detection, change-point detection, embedding search. |
| 15 | **GATEWAY** | Bidirectional CAN bridge with ordered pass, block and modify rules. Gated by ARM TX. |

### Walkthrough

![CanLab in use](docs/demo-preview.gif)

Twenty seconds of the real application, above. The full narrated walkthrough of
every tab is below, in four parts, 1080p with subtitles burned in.

**GitHub will not play these in the page.** It serves `.mp4` from a repository
as a download, so the links below save the file rather than opening a player.
They are also attached to the
[latest release](https://github.com/Sherin-SEF-AI/CanLab/releases/latest) if
that is easier to grab.

| Part | Covers | Length |
|---|---|---|
| [1. Loading a capture and finding structure](docs/canlab-demo-part1-analysis.mp4?raw=1) | FRAMES, the ID panel and inspector, SIGNALS, counter and checksum detection, the checksum guesser, entropy boundaries | 3.0 min |
| [2. Defining signals and checking them](docs/canlab-demo-part2-signals.mp4?raw=1) | DBC BUILDER and its bit grid, the live decode preview, PLOT, INTELLIGENCE, ML INTEL, DASHBOARD | 2.6 min |
| [3. Timeline, code generation and exports](docs/canlab-demo-part3-outputs.mp4?raw=1) | TIMELINE, CODE GEN, the five export formats, DIAGNOSTICS, security access | 2.4 min |
| [4. The transmit gate, injection and live capture](docs/canlab-demo-part4-transmitting.mp4?raw=1) | ARM TX, INJECTION, replay, fuzzing, GATEWAY, OBD-II, the AI engine, live capture | 3.3 min |

Subtitles: [part 1](docs/canlab-demo-part1-analysis.srt),
[part 2](docs/canlab-demo-part2-signals.srt),
[part 3](docs/canlab-demo-part3-outputs.srt),
[part 4](docs/canlab-demo-part4-transmitting.srt).

> Recorded from the `fix/production-readiness` branch, so two things in it are
> ahead of `main`: the DIAGNOSTICS tab there has XCP and DoIP panels (on `main`
> those protocols are library-only, see below), and it has selectable vehicle
> profiles. Everything else shown is what `main` does.

The preview above is cut from those four files by `docs/demo/build.py`. The
whole thing is generated, not hand-recorded, so it cannot drift away from what
the application does: `docs/demo/record.py` drives a real `MainWindow` under Qt's
offscreen platform and calls the same slots the buttons call, so a scene that
stops working fails the run instead of quietly recording a stale screen.

---

## Supported log formats

| Format | Notes |
|---|---|
| SavvyCAN CSV | GVRET and SavvyCAN export, including 2-digit hex data bytes and a trailing comma. |
| candump `.log` | `candump -l` output. |
| pcap / pcapng | Linux SocketCAN linktype 227, via dpkt. |
| Vector BLF | via python-can `BLFReader`. |
| Vector ASC | via python-can `ASCReader`. |
| MDF4 `.mf4` / `.mdf` | CANedge and similar. **Requires** `pip install asammdf`. |
| openpilot `.rlog` / `.qlog` | **Requires** pycapnp and the cereal `log.capnp` schema. Fails with a clear error if either is missing; it does not guess. |

---

## Analysis and ML (offline, no API key)

| Feature | Module | Notes |
|---|---|---|
| Byte role classifier | `core/signal_classifier.py` | COUNTER, CHECKSUM, BOOLEAN, PHYSICAL or PADDING per byte (heuristic). |
| Counter and checksum detection | `core/counter_checksum_detector.py`, `core/checksum_guesser.py` | Tests several checksum algorithms per byte and reports a match fraction. |
| Cross-ID correlation | `core/correlation_engine.py` | Pearson r per byte pair with nearest-timestamp alignment and a lag sweep. |
| Anomaly detection | `core/anomaly_detector.py` | Z-score per byte and Isolation Forest on the frame vector. |
| Entropy boundaries | `core/entropy_boundary.py` | Per-bit entropy to suggest signal edges. |
| Multiplexer detection | `core/mux_detector.py` | Finds a mode-selector byte and per-mode active bytes. |
| Reference calibration | `core/reference_calibrate.py` | See below. |

These are **heuristics that suggest candidates**, not guarantees. Always verify.
The checksum confidence is a train/validate match fraction over a chronological
split, not a statistical proof. The DASHBOARD heatmap shows message-timing
co-occurrence, not signal-value correlation; byte-value correlation lives in the
INTELLIGENCE tab (`core/correlation_engine.py`).

---

## Reference-driven calibration

`core/reference_calibrate.py` searches for the CAN field (ID, byte range,
endianness) whose values best fit a **physical reference** by least squares, and
reports scale and offset with an R² verdict of PASS or UNCONFIRMED. The
reference can be a CSV of `timestamp,value` (Tools → *Calibrate signal from
reference CSV*), or a value read by OCR from a dashboard video
(`core/vision_reference.py`, which needs `opencv-python` and `rapidocr`).
Sentinel codes meaning "signal unavailable" are masked, and the fitted scale is
snapped to a neat value when that barely changes the decode. Both refinements
are adapted from CSS Electronics' reverse-engineering skills; see
[Acknowledgements](#acknowledgements).

---

## Diagnostics

| Protocol | Module | Notes |
|---|---|---|
| UDS (ISO 14229) | `core/uds.py` | DTC read, ECU info (DIDs), service scan, read-only by default. |
| ISO-TP (ISO 15765-2) | `core/isotp.py` | Single and multi-frame transmit (flow-control handshake and consecutive frames) and reassembly. |
| J1939 | `core/j1939.py` | PGN decoding plus DM1 active-DTC decode (SPN, FMI, CM, OC). |
| OBD-II (SAE J1979) | `core/obd2_pids.py` | 26-PID table, supported-PID discovery across the continuation windows. |

### Library-only protocols

`core/xcp.py` (XCP over CAN, a read-only CONNECT / UPLOAD / SHORT_UPLOAD client
and poll worker, no memory writes) and `core/doip.py` (ISO 13400 vehicle
discovery, routing activation and UDS over IP on stdlib sockets) are implemented
and unit-tested, but **there is no user interface for either**. They are usable
from a script or a plugin, not from the application.

---

## DBC ecosystem

| Format | Import | Export |
|---|---|---|
| Standard DBC | Yes | Yes, parseable by cantools |
| openpilot DBC | Yes, as an opendbc cross-reference | Yes, parseable by cantools |
| Vector CANdb++ | No | Yes (`BA_DEF_` blocks) |
| AUTOSAR ARXML 4.3 | Yes | **Experimental.** Round-trips within CanLab but is **not** validated against the full AUTOSAR schema. Do not rely on it in external AUTOSAR tools yet. |
| Wireshark Lua dissector | No | Yes, little- and big-endian; big-endian verified against cantools |
| Excel/CSV CAN matrix | Yes | No |

Multiplexed signals are supported on DBC export (`SG_ M` and `m<n>`).

**opendbc matching** (Tools → *Match against opendbc*) fetches the real
`commaai/opendbc` library, caches it, and ranks how well your capture's IDs
match each OEM DBC. The first run needs network access.

---

## AI engine

Providers: **Anthropic**, **Groq** (Llama 3.x) and **Ollama** (any local model,
no API key). Before an AI call, CanLab runs the offline detectors and injects
their findings (byte roles, message type and period, checksum guess, similar
IDs) into the prompt, so the model reasons on structured facts rather than raw
hex. Configure in Settings → API Keys. Nothing is sent until you supply a key
and ask for an analysis.

---

## Integrations

| Capability | Module | Notes |
|---|---|---|
| REST API and live web dashboard | `core/rest_api.py` | Loopback only, **token-authenticated** (`X-API-Token`, shown on start). `GET /` serves a self-contained live-frames page; `/inject` also requires ARM TX. |
| MCP server | `mcp_server.py` | Exposes load_log, list_ids, detectors, correlate, opendbc-match, mux and calibrate as MCP tools, so an MCP client can drive the analysis loop. |
| Decoded time-series export | `core/timeseries_export.py` | Exports a Timestamp-by-signal matrix to CSV or Parquet. |
| Plugin SDK | [`docs/PLUGINS.md`](docs/PLUGINS.md) | Documented `register(app)` API and two example plugins. The loader reads plugin metadata statically and runs code only on explicit activation. |
| Panda backend | `core/panda_backend.py` | comma.ai Panda as a python-can-compatible bus, with the safety model selectable. |

---

## REST API

Start it from the **REST API** toolbar toggle. It binds to **127.0.0.1:8765**
and prints a per-session token. Every request needs an `X-API-Token` header.

```bash
GET  /            # live web dashboard (HTML, open)
GET  /frames      # last N frames  (?n=N)
GET  /signals     # decoded DBC signals
GET  /status      # connection and frame count
GET  /memory      # AI memory entries
POST /inject      # inject a frame: needs the token AND ARM TX
                  # {"id":"0x200","data":"01 02 03 04 05 06 07 08"}
```

---

## Testing

```bash
python -m pytest tests/ -q        # 154 passed, 1 skipped
```

The skip is the MDF4 importer, which needs the optional `asammdf` package. If
the MCP SDK is not installed either, that is a second skip.

Tests cover ID normalisation; ISO-TP single and multi-frame transmit (PCI
framing); the ARM TX gate, including that disarming mid-run stops replay and the
actuator sweep; UDS destructive-service classification and DTC and PID decoding;
OBD-II PID decoding; NaN-safety in the ML paths; BLF, ASC and candump FD import;
SavvyCAN CSV with hex bytes and a trailing comma; opendbc matching; reference
calibration and its refinements; multiplexer detection; J1939 DM1; XCP; DoIP;
the REST auth and NaN-safe JSON model; DBC round trip (message length and
extended ID); big-endian and signed injection packing; the lazy live-frame
store; the vectorised correlation aligner; and an import smoke test of every
tab.

---

## Recent fixes

A deep-audit pass fixed a batch of protocol, correctness, safety and performance
defects: ISO-TP framing, UDS, DTC and OBD decoding, DBC decoding on cantools 40
and later, DBC round trip, replay DLC, NaN-safe REST JSON, per-frame ARM TX
re-checks, injection byte-order packing, plugin consent, and a live-capture
store that was quadratic in the number of frames. See
[docs/AUDIT_FIXES.md](docs/AUDIT_FIXES.md) for the full list; each item has a
regression test in `tests/test_audit_fixes.py`.

Since then, the actuator sweep in the INJECTION tab has been brought under the
ARM TX gate. It was the one transmit path with no check, and it put frames on
the bus while the toolbar read DISARMED.

---

## Limitations

- **Not validated on many real vehicles.** Signal identification is heuristic.
  Verify every result before trusting it.
- ARM TX covers the transmit features, not diagnostic reads. See
  [Safety](#safety) for exactly which paths it gates.
- **XCP and DoIP have no user interface.** They are library modules only.
- **ARXML export is experimental** and is not validated against the AUTOSAR
  schema.
- **openpilot rlog import** needs pycapnp and the cereal schema. Without them it
  raises rather than producing data.
- **MDF4** import needs `asammdf`. **Vision OCR** needs `opencv-python`,
  `rapidocr` and `onnxruntime`, which are heavy and optional.
- CAN FD parsing and decoding is partial in places.
- The prebuilt Linux binary on the releases page is built from `main` on
  x86_64 and is not signed. There is no macOS or Windows binary; run from
  source on those.

---

## System requirements

- Linux, macOS or Windows with Python 3.11 or newer
- 4 GB RAM, 8 GB recommended for the ML features
- Optional: SocketCAN for live hardware, a comma.ai Panda, or another
  python-can-supported adapter

Live hardware is supported via [python-can](https://python-can.readthedocs.io)
(`socketcan`, `pcan`, `kvaser`, `virtual`, `serial`, `slcan` and others) and the
Panda backend. **Two hardware CAN channels are required for the gateway
feature.**

---

## Acknowledgements

The calibration refinements in `core/calibrate_refine.py`, sentinel masking and
scale snapping, are adapted from CSS Electronics'
[CAN bus reverse engineering skills](https://github.com/CSS-Electronics/can-bus-reverse-engineering-skills)
(MIT, © 2026 CSS Electronics).

---

## License

MIT License. See [LICENSE](LICENSE).

**Author:** Sherin Joseph Roy
**Repository:** https://github.com/Sherin-SEF-AI/CanLab
