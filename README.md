# CanLab — CAN Bus Reverse-Engineering Workstation

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python)](https://www.python.org)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-green?style=flat-square)](https://pypi.org/project/PyQt6/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

A desktop (PyQt6) tool for reverse-engineering CAN bus data: load a capture,
inspect frames and signals, run offline analysis to find counters, checksums and
signal boundaries, optionally get AI help interpreting an ID, and build and
export a DBC. It also includes diagnostics (UDS, ISO-TP, J1939, OBD-II, XCP,
DoIP) and — for isolated bench use only — injection, replay, fuzzing and a MitM
gateway.

> **Status:** actively developed, single-author project. It runs and is covered
> by an automated test suite (see [Testing](#testing)), but treat it as **beta**:
> some features need optional dependencies, the analysis methods are heuristics
> (see [Honest limitations](#honest-limitations)), and it has not been validated
> across a wide range of real vehicles.

---

> ## ⚠️ Safety
>
> CanLab can transmit on a CAN bus. **Use it only on isolated bench setups** — a
> benchtop ECU, `vcan0`, or dedicated lab hardware. Injecting or forwarding
> frames on a live vehicle bus can interfere with braking, steering and airbag
> systems.
>
> Built-in guards:
> - A safety acknowledgement dialog on first launch.
> - A global **ARM TX** toolbar toggle, **disarmed by default**. Nothing leaves
>   the tool until you arm it — not injection, replay, fuzzing or gateway
>   forwarding, and not diagnostic requests either (UDS, OBD-II, XCP, DoIP).
>   Disarming stops any transmit that is already running.
> - A per-session blocked-ID list that is refused even while armed.
> - The UDS service scan probes only read-only services unless you tick
>   "Include destructive services" and confirm.

---

## Install and run

```bash
git clone https://github.com/Sherin-SEF-AI/CanLab.git
cd CanLab
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ai,rest,mcp]"      # extras: ai, rest, mcp, mdf, dev

canlab                                # or: python -m canlab
```

Python 3.11+ (developed and tested on 3.12). API keys for the optional AI
providers are entered in Settings → API Keys and stored in the OS keyring; a
local Ollama server needs no key. Logs are written to
`~/.canlab/logs/canlab.log`.

---

## What it does — 15 tabs

| # | Tab | What it does |
|---|---|---|
| 1 | **FRAMES** | Raw frame table with per-byte delta highlighting, hex/bus filter, freeze/follow. |
| 2 | **SIGNALS** | DBC-decoded signal table (physical value, unit, entropy, suspected byte role). |
| 3 | **PLOT** | Multi-signal time series; per-byte traces; mouse-wheel zoom. |
| 4 | **AI ENGINE** | Send an ID to Anthropic, Groq or a local Ollama model; offline findings are injected into the prompt. Persistent memory across sessions. |
| 5 | **DBC BUILDER** | Visual signal editor with a bit grid. Import: DBC, ARXML, CAN matrix. Export: DBC, openpilot DBC, CANdb++, ARXML, Wireshark Lua. |
| 6 | **CODE GEN** | Generate Python or C parsing code from DBC definitions. |
| 7 | **INTELLIGENCE** | Cross-ID byte correlation with lag sweep; embedding similarity; log diff; J1939; value lookup. |
| 8 | **INJECTION** | Signal inject, fuzzer, trigger rules, replay (loop + scrubber), test sequences. Gated by ARM TX. |
| 9 | **DIAGNOSTICS** | UDS scan, security access, OBD-II, bus health, XCP, DoIP. |
| 10 | **DASHBOARD** | Byte-value heatmap, message timeline, gauges driven by your DBC signals. |
| 11 | **AUTO-RE** | Counter/checksum detection and entropy-boundary analysis across IDs (in worker threads). |
| 12 | **TIMELINE** | Scrubbable multi-ID event timeline plus a video-sync sub-tab. |
| 13 | **OBD-II** | Live PID gauge grid; auto-discovers supported PIDs across continuation windows. |
| 14 | **ML INTEL** | Byte-role classification, anomaly detection, change-point detection, embedding search. |
| 15 | **GATEWAY** | Bidirectional CAN MitM bridge with ordered Pass/Block/Modify rules. Gated by ARM TX. |

---

## Supported log formats

| Format | Notes |
|---|---|
| SavvyCAN CSV | GVRET/SavvyCAN export; IDs and data bytes read as hex |
| candump `.log` | `candump -l` output, including CAN FD (`##`) lines; error and remote frames are skipped |
| pcap / pcapng | Linux SocketCAN linktype 227, classic and FD (via dpkt) |
| Vector BLF | via python-can `BLFReader` |
| Vector ASC | via python-can `ASCReader` |
| MDF4 `.mf4` / `.mdf` | e.g. CANedge — needs `pip install canlab[mdf]` |
| openpilot `.rlog` / `.qlog` | needs pycapnp + the cereal `log.capnp` schema; fails with a clear error if missing |

Every parser produces the same columns: `Timestamp, ID, Bus, DLC, Extended,
B0..B7` (widened to `B63` when FD frames are present) and a per-ID `Delta`.

---

## Analysis (offline, no API key)

| Feature | Module | Notes |
|---|---|---|
| Checksum algorithms | `core/checksums.py` | Parametrised CRC-8 plus OEM variants (Hyundai, Toyota, Honda, Subaru, AUTOSAR), verified against published check values and commaai/opendbc. |
| Checksum guessing | `core/checksum_guesser.py` | Scores every algorithm per byte with a chronological train/validate split. |
| Byte role classifier | `core/signal_classifier.py` | COUNTER / CHECKSUM / BOOLEAN / PHYSICAL / PADDING per byte. |
| Counter & checksum detection | `core/counter_checksum_detector.py` | Counters from 2 to 8 bits, whole-byte or nibble. |
| Cross-ID correlation | `core/correlation_engine.py` | Pearson r per byte pair with nearest-timestamp alignment and a lag sweep. |
| Anomaly detection | `core/anomaly_detector.py` | Z-score per byte and Isolation Forest on the frame vector. |
| Entropy boundaries | `core/entropy_boundary.py` | Per-bit entropy to suggest signal edges. |
| Multiplexer detection | `core/mux_detector.py` | Finds a mode-selector byte and per-mode active bytes. |
| Reference calibration | `core/reference_calibrate.py` | See below. |

These are **heuristics that suggest candidates**, not identifications — always
verify. The checksum "confidence" is a match fraction over a chronological
split, not a proof.

---

## Vehicle profiles

Framing conventions are a setting, not an assumption. A profile says where a
rolling counter and checksum live and which algorithm computes the checksum; it
never claims to identify your vehicle. The default, **generic**, adds neither.
Hyundai/Kia, Toyota, Honda, Subaru and AUTOSAR E2E ship as presets, selectable
in Settings → VEHICLE. The profile drives injection stamping, the openpilot
export metadata, the generated code and the framing hint given to the AI.

---

## Reference-driven calibration

`core/reference_calibrate.py` searches for the CAN field (ID, byte range,
endianness) whose values best fit a **physical reference** by least squares and
reports scale/offset with an R² **PASS / UNCONFIRMED** verdict. The reference is
a CSV of `timestamp,value` (Tools → *Calibrate signal from reference CSV*).
"Signal unavailable" sentinel codes are masked and the fitted scale is snapped
to neat OEM values when that barely changes the decode (both adapted from CSS
Electronics' RE skills — see [Acknowledgements](#acknowledgements)).

---

## Diagnostics

| Protocol | Module | Notes |
|---|---|---|
| ISO-TP (ISO 15765-2) | `core/isotp.py` | Single- and multi-frame transmit (FC handshake, STmin), reassembly, CAN FD escape frames, functional addressing. |
| UDS (ISO 14229) | `core/uds.py` | DTC read, ECU info (DIDs), service scan (read-only by default), NRC 0x78 handling, periodic TesterPresent. |
| Security Access | `core/security_access.py` | Seed/key algorithms, scripted keys, rate-limited brute force with lockout detection. |
| J1939 | `core/j1939.py` | PGN decoding plus DM1 active-DTC (SPN/FMI/CM/OC) decode. |
| OBD-II (SAE J1979) | `core/obd2_pids.py` | PID table with supported-PID discovery across continuation windows. |
| XCP over CAN | `core/xcp.py` | Read-only client (CONNECT / UPLOAD / SHORT_UPLOAD) and a measurement poller. No memory writes. |
| DoIP (ISO 13400) | `core/doip.py` | Vehicle discovery, routing activation, UDS-over-IP (stdlib sockets). |

All diagnostic requests go through the ARM TX gate, and one receive thread
dispatches frames to each consumer, so a scan and the live table never compete
for responses.

---

## DBC ecosystem

| Format | Import | Export |
|---|---|---|
| Standard DBC | Yes | Yes (cantools-parseable) |
| openpilot DBC | Yes (opendbc cross-reference) | Yes |
| Vector CANdb++ | No | Yes (`BA_DEF_` blocks with measured cycle times) |
| AUTOSAR ARXML 4.3 | Yes (via cantools) | Yes (verified loadable by cantools) |
| Wireshark Lua dissector | No | Yes (Lua 5.1–5.4, little- and big-endian, verified against cantools) |
| Excel/CSV CAN matrix | Yes | No |

Extended (29-bit) IDs, multiplexed signals and value tables round-trip. All
decoding and encoding goes through one cantools-backed path, so what you see
decoded is what gets exported.

**opendbc matching** (Tools → *Match against opendbc*): fetches the real
`commaai/opendbc` library, caches it, and ranks how well your capture's IDs
match each OEM DBC. First run needs network access.

---

## Integrations

| Capability | Module | Notes |
|---|---|---|
| REST API + live web dashboard | `core/rest_api.py` | Loopback-only, token-authenticated (`X-API-Token`). `GET /` serves a live-frames page; `/inject` also requires ARM TX. |
| MCP server | `mcp_server.py` | Exposes load_log / list_ids / detectors / correlate / opendbc-match / mux / calibrate as MCP tools. Run `canlab-mcp`. |
| Decoded time-series export | `core/timeseries_export.py` | Export a Timestamp×signal matrix to CSV or Parquet. |
| Plugin SDK | `docs/PLUGINS.md` | Documented `register(app)` API and two example plugins. Plugins are disabled until you enable them in Settings. |
| Panda backend | `core/panda_backend.py` | comma.ai Panda as a python-can-compatible bus (safety mode selectable). |

### REST API

Start it from the **REST API** toolbar toggle. It binds to **127.0.0.1:8765**
and prints a per-session token; every data request needs an `X-API-Token`
header.

```
GET  /            # live web dashboard (HTML, open)
GET  /frames      # last N frames  (?n=N)
GET  /signals     # decoded DBC signals
GET  /status      # connection + frame count
GET  /memory      # AI memory entries
POST /inject      # inject a frame — requires token AND ARM TX
                  # {"id":"0x200","data":"01 02 03 04 05 06 07 08"}
```

---

## Performance

Live capture is bounded and flat-cost: frames are kept in a capped ring buffer
(`core/frame_store.py`, 500k frames by default, oldest dropped) and appended in
constant time, so a busy bus does not slow down as the log grows. The frame
table, ID tree and inspector are throttled and read only what they display.

---

## Testing

```bash
pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen python -m pytest -q     # 297 passed, 1 skipped
```

Coverage includes: the log parsers against real-format fixtures; DBC
encode/decode round trips through cantools (little- and big-endian, signed,
extended IDs, multiplexing); the ARXML and Lua exports verified against cantools
and a real Lua runtime; checksum algorithms against published check values; the
ARM TX gate for every transmit path; the receive dispatcher; the frame store
including its growth cost; ISO-TP/UDS wire format and NRC 0x78 handling;
settings, plugin opt-in and project round trips; and an offscreen smoke test
that builds the real window, cycles every tab and runs a live capture.

---

## Honest limitations

- **Not validated on many real vehicles.** Signal identification is heuristic —
  verify every result before trusting it.
- **openpilot rlog import** needs pycapnp plus the cereal schema; without it, it
  raises rather than producing data.
- **MDF4** import needs `asammdf` (`pip install canlab[mdf]`).
- CAN FD is parsed and stored end to end, but FD transmit is only exercised on
  ISO-TP; there is no dedicated FD injection UI.
- The AI features send the selected ID's frame statistics to whichever provider
  you configure. Nothing is sent until you enter a key and click Analyze.
- No prebuilt binary is offered here — run from source.

---

## System requirements

- Linux, macOS or Windows with Python 3.11+
- 4 GB RAM (8 GB recommended for the ML features)
- Optional: SocketCAN for live hardware; comma.ai Panda; any python-can adapter

Live hardware is supported via [python-can](https://python-can.readthedocs.io)
(`socketcan`, `pcan`, `kvaser`, `virtual`, `serial`, `slcan`, …) and the Panda
backend. **Two hardware CAN channels are required for the MitM/Gateway feature.**

---

## Acknowledgements

The calibration refinements in `core/calibrate_refine.py` (sentinel masking and
OEM scale/offset snapping) are adapted from CSS Electronics'
[CAN bus reverse engineering skills](https://github.com/CSS-Electronics/can-bus-reverse-engineering-skills)
(MIT, © 2026 CSS Electronics). The OEM checksum algorithms in
`core/checksums.py` follow [commaai/opendbc](https://github.com/commaai/opendbc)
(MIT).

---

## License

MIT License. See [LICENSE](LICENSE).

**Author:** Sherin Joseph Roy
**Repository:** https://github.com/Sherin-SEF-AI/CanLab
