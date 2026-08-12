# Deep-audit fix pass

A full pass over the application found and fixed a set of correctness, safety,
and performance defects. Every fix has a regression test in
`tests/test_audit_fixes.py` (plus updates to the existing suites).

## Critical correctness (protocol / data was wrong on the wire or on disk)

- **ISO-TP double PCI.** The UDS deep scan, service scan, security-access, and
  OBD-II poller all pre-built an ISO-TP frame (`02 01 0C …`) *and* passed it to
  `ISOTPSession.send()`, which added a second PCI byte — producing `03 02 01 0C`
  that no ECU answers, and pushing 8-byte padded requests down the multi-frame
  path as a bogus First Frame. `send()` now takes the service payload only and
  builds/pads the frame; all callers were updated. Frames are padded to 8 bytes.
- **UDS response parsing off-by-one.** Response payloads are now PCI-less and
  parsed at the correct offsets (DID positive-response SID at index 0, service
  NRC at index 2). The ECU-info (DID) scan previously checked the wrong index
  and reported nothing on real hardware.
- **DTC decoding.** Records are now decoded as 4-byte `[hi, mid, lo, status]`
  groups (was a 3-byte stride starting on the availability-mask byte, which
  misaligned every code after the first). Added `decode_dtc()`.
- **OBD-II PID width.** The scan now uses the canonical 26-PID table with correct
  one- and two-byte decoders (RPM/MAF/etc. were being read as a single byte, ~4×
  wrong). Removed the duplicate narrow PID table from `core.uds`.
- **cantools ≥ 40 incompatibility (app-wide).** `signal_dict_to_cantools()` used
  the `scale=`/`offset=`/`comment=` kwargs that cantools 40 removed, and
  `db.add_message()` which was renamed. Every DBC decode path swallowed the
  resulting exception, so **all DBC decoding silently produced nothing** on the
  cantools version `requirements.txt` actually installs (42.x). Now uses the
  conversion API with a legacy fallback and a version-agnostic add-message shim.
- **DBC round-trip loss.** Export forced every message to 8 bytes and dropped the
  extended-ID flag, so a 3-byte message re-imported as 8 bytes and a 29-bit ID
  re-imported as an 11-bit ID. Message length and the extended flag are now
  preserved on both import and export (BO_ id gets bit 31 for extended frames).
- **DBC live plot never updated.** The plot key stored a stringified signal dict;
  live update re-parsed it and never matched a decoded signal name. Live updates
  now use structured per-trace metadata.
- **Replay ignored DLC.** Replay always sent 8 bytes with NaN→0, changing every
  short frame. It now honours the recorded DLC.
- **REST JSON was invalid.** `/frames` and `/signals` serialized NaN (short-DLC
  byte cells) as bare `NaN`, which is not valid JSON — every such request 500'd,
  including the bundled live dashboard. Responses are now NaN-safe.
- **ID normalization in matching.** Signals/plot/timeline compared message IDs
  with `.upper()` string equality, which failed on differing zero-padding. All
  three now normalize both sides through `core.canid.normalize_id`.

## Safety

- **ARM-TX re-check.** Replay, gateway, and fuzzer checked the transmit gate only
  once at start, so disarming mid-run kept sending. All three now re-check every
  frame/iteration and halt immediately on disarm.
- **Injection packing.** Packing ignored byte order and signedness (big-endian /
  signed signals were injected with a different value than the DBC decodes) and
  hardcoded the checksum at byte 7 / extended=False. Packing now goes through
  cantools (matching the decode path), honours the signal's extended flag, and
  places the checksum at the last byte.
- **Plugin consent.** Plugins in `~/.canlab/plugins/` were auto-executed at every
  startup. Activation now requires explicit approval, trust-on-first-use keyed by
  the file's SHA-256; editing an approved plugin re-prompts.

## Performance / robustness

- **Live-capture storage.** `append_frames` did an O(n) `pd.concat` of the whole
  capture every 50 frames (O(n²) overall, freezing long sessions). `AppState`
  now stores a base frame + appended chunks and concatenates lazily with a
  cache, so append is O(chunk). Live frames also get a real per-ID `Delta`, and
  buffered frames are flushed on disconnect.
- **Threaded log loading.** `Open Log` parses off the GUI thread with a busy
  indicator, so large BLF/pcap/CSV loads no longer freeze the window.
- **Vectorized correlation aligner.** The per-row nearest-timestamp align loop
  (run over hundreds of ID pairs × 64 byte-pairs) is now a `searchsorted`
  vectorization, verified identical to the reference on random inputs.
- **Clean shutdown.** `closeEvent` now stops and joins the live, multi-bus, log,
  and opendbc worker threads instead of leaving them running at teardown.
- **candump CAN FD.** FD frames (`id##flags+data`) were mangled by the classic
  single-`#` parser. `.log` files are now sniffed for FD frames and routed to
  the FD-aware parser.
