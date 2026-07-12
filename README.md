# CanLab - CAN Bus Reverse Engineering Workstation 

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python)](https://www.python.org)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-green?style=flat-square)](https://pypi.org/project/PyQt6/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Release](https://img.shields.io/badge/Release-v1.2.0-brightgreen?style=flat-square)](https://github.com/Sherin-SEF-AI/CanLab/releases/tag/v1.2.0)
[![Groq](https://img.shields.io/badge/AI-Groq%20LLaMA-purple?style=flat-square)](https://groq.com)
[![CAN Bus](https://img.shields.io/badge/Protocol-CAN%20%7C%20CAN%20FD%20%7C%20ISO--TP%20%7C%20J1939-red?style=flat-square)](https://en.wikipedia.org/wiki/CAN_bus)

**15-module desktop CAN RE workstation with offline ML signal classification, dual AI engines, and MitM gateway.**

https://github.com/user-attachments/assets/cb47f85b-2551-44b2-9939-4f529962fbe0

---

> **SAFETY WARNING**
>
> The INJECTION and GATEWAY tabs contain a CAN fuzzer, signal injector, replay engine, and bidirectional bus bridge.
> **These features must only be used on isolated bench setups** — benchtop ECUs, `vcan0`, or dedicated lab hardware.
> Injecting or forwarding frames on a live vehicle CAN bus can interfere with braking, steering, and airbag systems.
> A safety disclaimer modal appears on first launch and must be accepted before the application starts.

---

## Why CanLab

Most CAN RE tools do one thing: SavvyCAN captures and filters, canutils decodes, Wireshark dissects. CanLab's gap is the **analysis loop** — the round-trip between raw frame capture, signal hypothesis, ML validation, AI explanation, and DBC output.

Specifically:

- **Offline ML context injection** — before every AI prompt, CanLab runs byte role classification, checksum detection, and embedding similarity and injects those findings into the prompt. The AI reasons on structured facts, not raw hex.
- **Counter and checksum auto-detection** — one button runs 9 checksum algorithms across all IDs with a 70/30 train/validation split and reports confidence scores.
- **AUTOSAR round-trip** — import `.arxml`, edit signals visually, export back to AUTOSAR 4.3, Vector CANdb++, openpilot DBC, and Wireshark Lua dissector from the same UI.
- **OBD-II live gauges** — 26-PID polling dashboard with auto-discover (Mode 01 PID 0x00 bitmask), no ELM327 script needed.
- **MitM/Gateway** — bridge two CAN buses with ordered Pass/Block/Modify rules, byte-level rewrite, and live frame log.
- **Video-to-Log sync** — load a dashcam or bench recording and scrub CAN signals in sync with the video.

---

## What's New in v1.2.0

| Feature | Details |
|---|---|
| **GATEWAY tab** | Bidirectional CAN MitM bridge. Two independent buses, ordered filter rules (Pass/Block/Modify), byte rewrite, ID rewrite, live frame log |
| **Video-to-Log sync** | TIMELINE tab now has a VIDEO SYNC sub-tab — load any video, click a signal spike to seek, scrub to move the signal playhead |
| **pcap/pcapng import** | Load Wireshark captures directly (SocketCAN linktype 227 via dpkt) |
| **Replay v2** | Loop checkbox, scrubber slider, 29-bit extended ID support, clean thread shutdown |
| **ARXML round-trip** | DBC Builder can import and export AUTOSAR 4.3 System Template `.arxml` |
| **CANdb++ export** | Exports Vector CANdb++ `.dbc` with `BA_DEF_` / `BA_` attribute blocks |
| **Correlation heatmap fix** | DASHBOARD heatmap switched to matplotlib plasma colormap (was invisible on dark theme) |

---

## New Integrations

| Capability | Module | What it does |
|---|---|---|
| **Reference-driven calibration** | `core/reference_calibrate.py` | Search (ID, byte-range, endianness) for the field that linearly explains a physical reference (OBD-II/GPS/OCR); least-squares scale/offset fit with a **PASS/UNCONFIRMED** R² gate. Tools → *Calibrate signal from reference CSV*. |
| **Vision-OCR reference** | `core/vision_reference.py` | OCR a numeric gauge value from a dashcam video ROI into a reference series (opencv + rapidocr, no PyTorch). Feeds the calibrator. |
| **opendbc matching** | `core/opendbc_matcher.py` | Match a capture's IDs against the real `commaai/opendbc` library (fetch+cache+parse, Jaccard scoring). Tools → *Match against opendbc*. |
| **Multiplexer detection** | `core/mux_detector.py` | Detect mode-dependent (multiplexed) messages; DBC export emits `SG_ M`/`m<n>`. Tools → *Detect multiplexed signals*. |
| **XCP-over-CAN** | `core/xcp.py` | Read-only XCP client + poll worker for live ECU internal measurements. |
| **DoIP (ISO 13400)** | `core/doip.py` | Diagnostics over IP: vehicle discovery, routing activation, UDS-over-Ethernet. |
| **More log formats** | `core/log_parser.py` | Vector **BLF**, Vector **ASC**, and **MDF4** (CANedge) import, in addition to CSV/candump/rlog/pcap. |
| **Decoded time-series export** | `core/timeseries_export.py` | Export a Timestamp×signal matrix to CSV/Parquet for Grafana/InfluxDB/pandas. |
| **Live web dashboard** | `core/rest_api.py` | Token-gated `GET /` serves a self-contained page that live-plots frames in a browser (phone-friendly remote monitoring). |
| **Local LLM (Ollama)** | `core/ai_client.py` | Fully-offline AI analysis via a local Ollama server — no API key. |
| **MCP server** | `mcp_server.py` | Exposes load_log / detectors / correlate / opendbc-match as MCP tools so Claude Code can drive the analysis loop. |
| **Plugin SDK** | `docs/PLUGINS.md` | Documented `register(app)` API + example plugins on the sandboxed loader. |

---

## Download

| Platform | File | Size |
|---|---|---|
| Linux x86_64 | [CanLab-1.2.0-linux-x86_64.tar.gz](https://github.com/Sherin-SEF-AI/CanLab/releases/download/v1.2.0/CanLab-1.2.0-linux-x86_64.tar.gz) | 200 MB |

```bash
tar -xzf CanLab-1.2.0-linux-x86_64.tar.gz
cd CanLab/
./CanLab
```

No Python installation required.

---

## Run from Source

```bash
git clone https://github.com/Sherin-SEF-AI/CanLab.git
cd CanLab
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Optional: Groq (free tier) or Anthropic key for AI features
export GROQ_API_KEY="gsk_..."
export ANTHROPIC_API_KEY="sk-ant-..."

cd canlab         # source root — all imports are relative to here
python3 main.py
```

> **Note on directory layout:** The repo root is `CanLab/`. The Python source lives in `canlab/`. The `cd canlab` before `python3 main.py` is intentional — all module imports are relative to that directory.

---

## System Requirements

- Linux (Ubuntu 20.04+), macOS 12+, or Windows 10+ with Python 3.11+
- 4 GB RAM (8 GB recommended for ML features)
- Optional: SocketCAN kernel module for live hardware (`sudo modprobe can_raw`)
- Optional: comma.ai Panda for vehicle CAN capture

---

## What It Does — 15 Tabs

| # | Tab | What it does |
|---|---|---|
| 1 | **FRAMES** | Raw frame table with byte-delta highlighting. Hex filter, bus filter, freeze/follow. |
| 2 | **SIGNALS** | DBC-decoded signal table — physical value, unit, min/max, entropy, suspected type. |
| 3 | **PLOT** | Multi-signal time series. Per-byte traces, mouse-wheel zoom, live update. |
| 4 | **AI ENGINE** | Send any ID to Groq LLaMA or Claude AI. ML context auto-injected. NL query across full dataset. Persistent memory across sessions. |
| 5 | **DBC BUILDER** | Visual signal editor. Export: DBC, openpilot DBC, CANdb++, ARXML, Wireshark Lua. Import: DBC, ARXML, CAN matrix. |
| 6 | **CODE GEN** | Auto-generate Python or C parsing code from DBC definitions. |
| 7 | **INTELLIGENCE** | Cross-ID Pearson correlation. Lag sweep. Cosine similarity embedding search. |
| 8 | **INJECTION** | Signal inject, CAN fuzzer (random/sequential/mutation), trigger rules, replay with loop + scrubber. |
| 9 | **DIAGNOSTICS** | UDS deep scan, ISO-TP sessions, J1939 PGN decoder, OBD-II Mode 01, bus health monitor. |
| 10 | **DASHBOARD** | Byte-value heatmap (matplotlib plasma), message timeline, physical overlay gauges. |
| 11 | **AUTO-RE** | One-click counter/checksum detection across all IDs. 9 algorithms, confidence scoring. Entropy boundaries. |
| 12 | **TIMELINE** | Scrubable multi-ID event timeline + VIDEO SYNC sub-tab (dashcam/bench video ↔ signal playhead). |
| 13 | **OBD-II** | 26-PID live gauge grid. Auto-discovers supported PIDs. Configurable polling rate. |
| 14 | **ML INTEL** | Byte role classification, anomaly detection, change-point detection, signal embedding search. |
| 15 | **GATEWAY** | Bidirectional CAN MitM bridge. Two independent buses, ordered Pass/Block/Modify rules, byte + ID rewrite, live log. |

---

## Screenshots

### FRAMES tab — raw frames with byte delta highlighting
![FRAMES](docs/screenshots/01_frames.png)

### AI ENGINE tab — streaming analysis with ML context
![AI ENGINE](docs/screenshots/04_ai_engine.png)

### AUTO-RE tab — counter and checksum detection
![AUTO-RE](docs/screenshots/11_auto_re.png)

### DBC BUILDER tab
![DBC BUILDER](docs/screenshots/05_dbc_builder.png)

### OBD-II live gauge dashboard
![OBD-II](docs/screenshots/13_obd_ii.png)

<details>
<summary>All tab screenshots</summary>

![SIGNALS](docs/screenshots/02_signals.png)
![PLOT](docs/screenshots/03_plot.png)
![CODE GEN](docs/screenshots/06_code_gen.png)
![INTELLIGENCE](docs/screenshots/07_intelligence.png)
![INJECTION](docs/screenshots/08_injection.png)
![DIAGNOSTICS](docs/screenshots/09_diagnostics.png)
![DASHBOARD](docs/screenshots/10_dashboard.png)
![TIMELINE](docs/screenshots/12_timeline.png)
![ML INTEL](docs/screenshots/14_ml_intel.png)

</details>

---

## ML Features (all offline, no API key required)

| Algorithm | Module | What it detects |
|---|---|---|
| Byte role classifier | `core/signal_classifier.py` | COUNTER, CHECKSUM, BOOLEAN, PHYSICAL, PADDING per byte |
| Checksum reverser | `core/checksum_guesser.py` | XOR8, SUM8, CRC8-SAE, HYUNDAI\_FULL, and 5 others — 70/30 train/validate split |
| Cross-ID correlation | `core/correlation_engine.py` | Pearson r per byte pair across IDs, nearest-neighbour timestamp alignment, lag sweep |
| Change-point detector | `core/change_detector.py` | Before/after byte distribution shift on user-marked events |
| Anomaly detector | `core/anomaly_detector.py` | Z-score per byte + Isolation Forest on full frame vector |
| Periodicity classifier | `core/periodicity.py` | CYCLIC vs EVENT, median period in ms, jitter % |
| Signal embedding search | `core/signal_embedding.py` | Cosine similarity across all IDs for structural pattern matching |

---

## Supported Log Formats

| Format | Notes |
|---|---|
| SavvyCAN CSV | Default export from SavvyCAN |
| candump log | `candump -l` output |
| openpilot rlog | `.rlog` / `.bz2` via openpilot `cereal` |
| pcap / pcapng | Wireshark captures with SocketCAN linktype 227 (via dpkt) |
| CAN FD | Up to 64-byte payloads |

## Supported Hardware (via python-can)

`socketcan`, `pcan`, `kvaser`, `virtual`, `usb2can`, `serial`, comma.ai Panda

---

## DBC Ecosystem

| Format | Import | Export |
|---|---|---|
| Standard DBC | Yes | Yes |
| openpilot DBC | Yes (via opendbc cross-reference) | Yes |
| Vector CANdb++ | No | Yes (BA\_DEF\_ attribute blocks) |
| AUTOSAR ARXML 4.3 | Yes | Experimental¹ |
| Wireshark Lua dissector | No | Yes (little-endian²) |
| Excel/CSV CAN matrix | Yes | No |

¹ ARXML export round-trips within CanLab but is **not yet validated against the
full AUTOSAR 4.3 schema** — do not rely on it in external AUTOSAR toolchains yet.
² The Lua dissector currently decodes little-endian (Intel) signals; big-endian
(Motorola) signals are not yet decoded.

---

## AI Engine

Supported providers: **Groq LLaMA 3.3 70B** (free tier) and **Anthropic Claude Sonnet**.

Before every AI call, CanLab runs offline ML and injects structured context into the prompt:
- Byte roles with confidence %
- Message type (CYCLIC/EVENT) and period
- Checksum algorithm and confidence
- Top-3 similar IDs by embedding cosine similarity

Configure in **Settings > API Keys**. Groq's free tier is sufficient for hundreds of analyses per day.

---

## REST API

Start via the **REST API** toolbar toggle. Binds to **127.0.0.1:8765** (loopback
only). On start, the app shows a per-session **access token** — every request
must send it as an `X-API-Token` header.

```bash
GET  /frames            # last 200 loaded frames (append ?n=N to change limit)
GET  /signals           # decoded DBC signals
GET  /status            # connection state, frame count, repo URL, fingerprint
GET  /memory            # AI memory entries
POST /inject            # inject a raw CAN frame  {"id":"0x200","data":"01 02 …","extended":false}
```

`POST /inject` additionally requires the global **ARM TX** toggle to be enabled;
with TX disarmed it returns `409`. Example:

```bash
curl -X POST http://127.0.0.1:8765/inject \
  -H "X-API-Token: <token shown on start>" \
  -H "Content-Type: application/json" \
  -d '{"id":"0x200","data":"01 02 03 04 05 06 07 08"}'
```

---

## Architecture

```
canlab/
├── main.py                 Entry point — safety disclaimer + QApplication
├── mainwindow.py           Main window, tab registration, menus, toolbar
├── theme.py                Dark theme QSS + mono_font
├── settings_dialog.py      API keys, CAN interface, AI provider settings
├── core/                   Pure-logic modules (no UI, fully testable)
│   ├── ai_client.py        Groq + Anthropic streaming workers
│   ├── dbc_manager.py      DBC string builder, cantools decode, ARXML round-trip
│   ├── log_parser.py       SavvyCAN CSV, candump, rlog, pcap/pcapng parsers
│   ├── signal_classifier.py  Byte role classifier
│   ├── checksum_guesser.py   9-algorithm checksum reverser
│   ├── correlation_engine.py Pearson cross-ID correlation
│   ├── anomaly_detector.py   Z-score + Isolation Forest
│   ├── replay.py           ReplayWorker — loop, scrubber, extended ID
│   ├── gateway.py          GatewayWorker — bidirectional MitM bridge
│   ├── isotp.py            ISO-TP session layer
│   ├── uds.py              UDS scanner + OBD-II Mode 01
│   ├── j1939.py            J1939 PGN decoder
│   ├── obd2_pids.py        26-PID SAE J1979 table with decode lambdas
│   ├── obd2_poller.py      OBD2Poller QThread
│   ├── arxml_export.py     AUTOSAR 4.3 emitter
│   ├── arxml_import.py     AUTOSAR 4.3 parser
│   └── candbpp_export.py   Vector CANdb++ exporter
└── tabs/                   One file per tab
```

##  Supported Hardware Guide

Navigating the world of CAN bus hardware can be confusing. The hardware you need depends entirely on what you want to do: **Passive Sniffing** (just listening to the car) or **Active Interception** (Man-in-the-Middle routing). 

CanLab relies on `python-can`, meaning it supports a massive variety of adapters. Here is a breakdown of what hardware unlocks which features in CanLab:

### 1. Passive Sniffing & Logging (Single Channel)
If you only want to plug into your OBD2 port to read traffic, log hex data, and use the offline Machine Learning tools to find signals, you only need a standard single-channel adapter.
* **Supported Devices:**
  * **CANable / CANable Pro** (Highly recommended, cheap, uses `slcan` or SocketCAN)
  * **PCAN-USB** (PEAK-System)
  * **Kvaser** adapters
  * Any generic ELM327 / OBD2 dongle (Limited speeds, not recommended for raw high-speed CAN)
  * Any Linux SocketCAN compatible adapter.
* **Supported Features:** ✔️ Live Sniffing | ✔️ UDS Requests | ✔️ ML Signal Decoding | ✔️ Traffic Logging

### 2. Man-in-the-Middle (MitM) & Active Routing (Dual Channel)
To actively bypass OEM lockouts, drop specific packets, or inject synthetic steering/braking commands, you must physically cut the CAN lines and sit *between* two ECUs. This requires a microcontroller with at least **two CAN interfaces**.
* **Supported Devices:**
  * **Teensy 4.0 / 4.1** (DIY route: requires wiring two standard CAN transceivers to the Teensy's built-in CAN pins).
  * **Macchina M2** (Commercial off-the-shelf: excellent dual-channel board built specifically for automotive hacking).
  * **ESP32 with Dual CAN** (Ensure your specific board actually has two hardware CAN controllers).
* **Supported Features:** ✔️ Live Sniffing | ✔️ ML Decoding | ✔️ **Hardware MitM Routing** | ✔️ **Rule-based Packet Dropping/Injection**

### Feature Matrix

| Hardware Type | Example Devices | Live Sniffing | ML Decoding | UDS Tools | MitM Interception |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Single-Channel USB** | CANable, PCAN, Kvaser | ✅ | ✅ | ✅ | ❌ |
| **Dual-Channel Micro** | Teensy 4.x, Macchina M2 | ✅ | ✅ | ✅ | ✅ |

> **A note for beginners:** If you are just getting started, buy a $30 CANable, plug it into your OBD2 port, and use CanLab's sniffing and ML tools to map out the network first. Do not attempt a Dual-Channel MitM setup until you know exactly which physical wires you need to intercept.
---

## Safety

**Injection, fuzzing, and gateway features are for isolated bench use only.**

Connecting CanLab to a vehicle's live CAN bus while using injection, replay, fuzzing, or gateway features can:
- Disable ABS or ESC
- Trigger unintended airbag deployment
- Interfere with electric power steering
- Cause unintended acceleration or braking

Use `vcan0` (virtual CAN) or a benchtop ECU for all testing. The application shows a safety acknowledgement dialog on first launch.

**Safety guards built in:**
- **ARM TX toggle** — a global transmit gate (in the toolbar) that is **disarmed by default**. Injection, replay, fuzzing, gateway forwarding, and `POST /inject` all refuse to send until you explicitly arm it.
- **UDS service scan** probes only read-only services by default; destructive services (ECUReset, ClearDTC, RoutineControl, RequestDownload, …) are skipped unless you tick "Include destructive services" and confirm.
- **REST API** is loopback-only and token-authenticated.

---

## Acknowledgements

The calibration refinements in `core/calibrate_refine.py` (sentinel masking and
OEM scale/offset snapping) are adapted from CSS Electronics'
[CAN bus reverse engineering skills](https://github.com/CSS-Electronics/can-bus-reverse-engineering-skills)
(MIT, © 2026 CSS Electronics).

## License

MIT License. See [LICENSE](LICENSE).

**Author:** Sherin Joseph Roy — sherin.joseph2217@gmail.com
**Repository:** https://github.com/Sherin-SEF-AI/CanLab
