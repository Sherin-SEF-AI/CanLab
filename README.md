# CanLab — CAN Bus Reverse-Engineering Workstation

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python)](https://www.python.org)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-green?style=flat-square)](https://pypi.org/project/PyQt6/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

A desktop (PyQt6) tool for reverse-engineering CAN bus data: load a capture,
inspect frames and signals, run offline analysis to find counters / checksums /
signal boundaries, optionally get AI help interpreting an ID, and build/export a
DBC. It also includes diagnostics (UDS, ISO-TP, J1939, OBD-II, XCP, DoIP) and —
for isolated bench use only — injection, replay, fuzzing, and a MitM gateway.

> **Status:** actively developed, single-author project. It runs and is covered
> by an automated test suite (see [Testing](#testing)), but treat it as **alpha**:
> some features need optional dependencies, some analysis methods are heuristics
> (see [Honest limitations](#honest-limitations)), and it has not been validated
> across a wide range of real vehicles.

---

> ## ⚠️ Safety
>
> The **INJECTION** and **GATEWAY** features can transmit frames onto a bus.
> **Use them only on isolated bench setups** — a benchtop ECU, `vcan0`, or
> dedicated lab hardware. Injecting or forwarding frames on a live vehicle bus
> can interfere with braking, steering, and airbag systems.
>
> Built-in guards:
> - A safety acknowledgement dialog on first launch.
> - A global **ARM TX** toolbar toggle, **disarmed by default** — injection,
>   replay, fuzzing, gateway forwarding, and the REST `/inject` endpoint all
>   refuse to transmit until you explicitly arm it.
> - The UDS service scan probes only read-only services unless you tick
>   "Include destructive services" and confirm.

---

## Run from source

This is the supported, verified way to run it.

```bash
git clone https://github.com/Sherin-SEF-AI/CanLab.git
cd CanLab
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Optional AI providers (all optional; the app works fully offline without them):
export ANTHROPIC_API_KEY="sk-ant-..."   # Anthropic Claude
export GROQ_API_KEY="gsk_..."           # Groq
# or run a local Ollama server for offline AI (no key)

cd canlab            # source root — imports are relative to here
python3 main.py
```

Python 3.11+ is recommended (developed/tested on 3.12).

---

## What it does — 15 tabs

All 15 tabs build and render (verified by an automated smoke test that loads the
bundled sample log and cycles every tab).

| # | Tab | What it does |
|---|---|---|
| 1 | **FRAMES** | Raw frame table with per-byte delta highlighting, hex/bus filter, freeze/follow. |
| 2 | **SIGNALS** | DBC-decoded signal table (physical value, unit, entropy, suspected byte role). |
| 3 | **PLOT** | Multi-signal time series; per-byte traces; mouse-wheel zoom. |
| 4 | **AI ENGINE** | Send an ID to Anthropic, Groq, or a local Ollama model; offline ML findings are injected into the prompt. Persistent memory across sessions. |
| 5 | **DBC BUILDER** | Visual signal editor. Import: DBC, ARXML, CAN matrix. Export: DBC, openpilot DBC, CANdb++, ARXML (experimental), Wireshark Lua. |
| 6 | **CODE GEN** | Generate Python or C parsing code from DBC definitions. |
| 7 | **INTELLIGENCE** | Cross-ID byte Pearson correlation with lag sweep; embedding similarity; fingerprint. |
| 8 | **INJECTION** | Signal inject, fuzzer, trigger rules, replay (loop + scrubber). Gated by ARM TX. |
| 9 | **DIAGNOSTICS** | UDS scan, ISO-TP, J1939 (incl. DM1 DTCs), OBD-II Mode 01, bus-health monitor. |
| 10 | **DASHBOARD** | Byte-value heatmap, message timeline, physical overlay gauges. |
| 11 | **AUTO-RE** | Counter/checksum detection and entropy-boundary analysis across IDs (runs in worker threads). |
| 12 | **TIMELINE** | Scrubbable multi-ID event timeline + a video-sync sub-tab. |
| 13 | **OBD-II** | Live PID gauge grid; auto-discovers supported PIDs (incl. continuation windows). |
| 14 | **ML INTEL** | Byte-role classification, anomaly detection, change-point detection, embedding search. |
| 15 | **GATEWAY** | Bidirectional CAN MitM bridge with ordered Pass/Block/Modify rules. Gated by ARM TX. |

### Screenshots

![FRAMES](docs/screenshots/01_frames.png)
![AI ENGINE](docs/screenshots/04_ai_engine.png)
![AUTO-RE](docs/screenshots/11_auto_re.png)

<details><summary>More screenshots</summary>

![SIGNALS](docs/screenshots/02_signals.png)
![DBC BUILDER](docs/screenshots/05_dbc_builder.png)
![INTELLIGENCE](docs/screenshots/07_intelligence.png)
![DIAGNOSTICS](docs/screenshots/09_diagnostics.png)
![DASHBOARD](docs/screenshots/10_dashboard.png)
![OBD-II](docs/screenshots/13_obd_ii.png)
![ML INTEL](docs/screenshots/14_ml_intel.png)

</details>

---

## Supported log formats

| Format | Notes |
|---|---|
| SavvyCAN CSV | GVRET/SavvyCAN export |
| candump `.log` | `candump -l` output |
| pcap / pcapng | Linux SocketCAN linktype 227 (via dpkt) |
| Vector BLF | via python-can `BLFReader` |
| Vector ASC | via python-can `ASCReader` |
| MDF4 `.mf4` / `.mdf` | e.g. CANedge — **requires** `pip install asammdf` |
| openpilot `.rlog` / `.qlog` | **requires** pycapnp + the cereal `log.capnp` schema; fails with a clear error if missing (it does not guess) |

---

## Analysis / ML (offline, no API key)

| Feature | Module | Notes |
|---|---|---|
| Byte role classifier | `core/signal_classifier.py` | COUNTER / CHECKSUM / BOOLEAN / PHYSICAL / PADDING per byte (heuristic). |
| Counter & checksum detection | `core/counter_checksum_detector.py`, `core/checksum_guesser.py` | Tests several checksum algorithms per byte; reports a match fraction. |
| Cross-ID correlation | `core/correlation_engine.py` | Pearson r per byte pair with nearest-timestamp alignment and a lag sweep. |
| Anomaly detection | `core/anomaly_detector.py` | Z-score per byte and Isolation Forest on the frame vector. |
| Entropy boundaries | `core/entropy_boundary.py` | Per-bit entropy to suggest signal edges. |
| Multiplexer detection | `core/mux_detector.py` | Finds a mode-selector byte and per-mode active bytes. |
| Reference calibration | `core/reference_calibrate.py` | See below. |

These are **heuristics that suggest candidates**, not guarantees — always verify.
The checksum "confidence" is a train/validate match fraction over a chronological
split, not a statistical proof; the DASHBOARD heatmap shows message-timing
co-occurrence, not signal-value correlation (byte-value correlation lives in the
INTELLIGENCE tab / `correlation_engine.py`).

---

## Reference-driven calibration

`core/reference_calibrate.py` searches for the CAN field (ID, byte range,
endianness) whose values best fit a **physical reference** by least squares, and
reports scale/offset with an R² **PASS / UNCONFIRMED** verdict. The reference can
be a CSV of `timestamp,value` (Tools → *Calibrate signal from reference CSV*), or
a value OCR'd from a dashboard video (`core/vision_reference.py`, needs
`opencv-python` + `rapidocr`). "Signal unavailable" sentinel codes are masked and
the fitted scale is snapped to neat OEM values when that barely changes the decode
(these two refinements are adapted from CSS Electronics' RE skills — see
[Acknowledgements](#acknowledgements)).

---

## Diagnostics

| Protocol | Module | Notes |
|---|---|---|
| UDS (ISO 14229) | `core/uds.py` | DTC read, ECU info (DIDs), service scan (read-only by default). |
| ISO-TP (ISO 15765-2) | `core/isotp.py` | Single- and multi-frame TX (FC handshake + consecutive frames) and reassembly. |
| J1939 | `core/j1939.py` | PGN decoding + DM1 active-DTC (SPN/FMI/CM/OC) decode. |
| OBD-II (SAE J1979) | `core/obd2_pids.py` | 26-PID table; supported-PID discovery across continuation windows. |
| XCP over CAN | `core/xcp.py` | Read-only client (CONNECT / UPLOAD / SHORT_UPLOAD) + a poll worker. No memory writes. |
| DoIP (ISO 13400) | `core/doip.py` | Vehicle discovery, routing activation, UDS-over-IP (stdlib sockets). |

---

## DBC ecosystem

| Format | Import | Export |
|---|---|---|
| Standard DBC | Yes | Yes (cantools-parseable) |
| openpilot DBC | Yes (opendbc cross-reference) | Yes (cantools-parseable) |
| Vector CANdb++ | No | Yes (`BA_DEF_` blocks) |
| AUTOSAR ARXML 4.3 | Yes | **Experimental** — round-trips within CanLab but is **not** validated against the full AUTOSAR schema; don't rely on it in external AUTOSAR tools yet |
| Wireshark Lua dissector | No | Yes (little- and big-endian; big-endian verified against cantools) |
| Excel/CSV CAN matrix | Yes | No |

Multiplexed signals are supported on DBC export (`SG_ M` / `m<n>`).

**opendbc matching** (Tools → *Match against opendbc*): fetches the real
`commaai/opendbc` library, caches it, and ranks how well your capture's IDs match
each OEM DBC. First run needs network access.

---

## AI engine

Providers: **Anthropic** (`claude-sonnet-5` / `claude-opus-4-8`), **Groq**
(Llama 3.x), and **Ollama** (any local model, no API key). Before an AI call,
CanLab runs the offline detectors and injects their findings (byte roles, message
type/period, checksum guess, similar IDs) into the prompt so the model reasons on
structured facts rather than raw hex. Configure in Settings → API Keys.

---

## Integrations

| Capability | Module | Notes |
|---|---|---|
| REST API + live web dashboard | `core/rest_api.py` | Loopback-only, **token-authenticated** (`X-API-Token`, shown on start). `GET /` serves a self-contained live-frames page; `/inject` also requires ARM TX. |
| MCP server | `mcp_server.py` | Exposes load_log / list_ids / detectors / correlate / opendbc-match / mux / calibrate as MCP tools so Claude Code can drive the analysis loop. |
| Decoded time-series export | `core/timeseries_export.py` | Export a Timestamp×signal matrix to CSV or Parquet. |
| Plugin SDK | `docs/PLUGINS.md` | Documented `register(app)` API + two example plugins; the loader reads plugin metadata statically and only runs code on explicit activation. |
| Panda backend | `core/panda_backend.py` | comma.ai Panda as a python-can-compatible bus (safety mode selectable). |

---

## REST API

Start it from the **REST API** toolbar toggle. It binds to **127.0.0.1:8765**
and prints a per-session token; every request needs an `X-API-Token` header.

```bash
GET  /            # live web dashboard (HTML, open)
GET  /frames      # last N frames  (?n=N)
GET  /signals     # decoded DBC signals
GET  /status      # connection + frame count
GET  /memory      # AI memory entries
POST /inject      # inject a frame — requires token AND ARM TX
                  # {"id":"0x200","data":"01 02 03 04 05 06 07 08"}
```

---

## Testing

```bash
python -m pytest tests/ -q        # 136 passed, 1 skipped (MDF needs asammdf)
```

Tests cover ID normalization, ISO-TP multi-frame transmit, the ARM safety gate,
UDS destructive-service classification, OBD-II PID decoding, ML NaN-safety,
BLF/ASC import, opendbc matching, reference calibration + refinements, multiplexer
detection, J1939 DM1, XCP, DoIP, the REST auth model, and an import smoke test of
every tab.

---

## Honest limitations

- **Not validated on many real vehicles.** Signal identification is heuristic —
  verify every result before trusting it.
- **ARXML export is experimental** and not AUTOSAR-schema-validated.
- **openpilot rlog import** needs pycapnp + the cereal schema; without it, it
  raises rather than producing data.
- **MDF4** import needs `asammdf`; **vision OCR** needs `opencv-python` +
  `rapidocr` + `onnxruntime` (heavy, optional).
- CAN FD parsing/decoding is partial in places.
- No prebuilt binary is offered here — run from source.

---

## System requirements

- Linux, macOS, or Windows with Python 3.11+
- 4 GB RAM (8 GB recommended for the ML features)
- Optional: SocketCAN for live hardware; comma.ai Panda; a CANsub/other
  python-can-supported adapter

Live hardware is supported via [python-can](https://python-can.readthedocs.io)
(`socketcan`, `pcan`, `kvaser`, `virtual`, `serial`, `slcan`, …) and the Panda
backend. **Two hardware CAN channels are required for the MitM/Gateway feature.**

---

## Acknowledgements

The calibration refinements in `core/calibrate_refine.py` (sentinel masking and
OEM scale/offset snapping) are adapted from CSS Electronics'
[CAN bus reverse engineering skills](https://github.com/CSS-Electronics/can-bus-reverse-engineering-skills)
(MIT, © 2026 CSS Electronics).

---

## License

MIT License. See [LICENSE](LICENSE).

**Author:** Sherin Joseph Roy
**Repository:** https://github.com/Sherin-SEF-AI/CanLab
