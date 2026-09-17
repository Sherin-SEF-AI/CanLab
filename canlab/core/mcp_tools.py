"""The tools an assistant can call on CanLab over MCP.

One tool set, two places to run it. Headless, ``canlab-mcp`` loads a capture
into its own process and an assistant drives the analysis without the window
open. Inside the application the same tools run against whatever is loaded or
being captured right now, so the assistant sees the live bus, and a signal it
adds appears in the DBC Builder at once.

Everything here is Qt-free. The ``Backend`` protocol is the only thing that
differs between the two: where the frames come from, where a new signal goes.
The application supplies one that marshals writes onto the GUI thread.

Nothing here transmits. The tools read the capture, run the detectors, and
edit the signal list; putting frames on a bus stays behind ARM TX in the
window, where a person is watching.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from canlab.core.annotations import AnnotationSet

BYTE_COLS = [f"B{i}" for i in range(8)]

# Assistants pay for every token they read; a listing that runs to thousands
# of rows helps nobody. Each list tool has a cap the caller can raise.
DEFAULT_LIMIT = 100
MAX_LIMIT = 2000


# ── backends ─────────────────────────────────────────────────────────────────

class Backend(Protocol):
    def frames(self) -> pd.DataFrame: ...
    def describe(self) -> dict: ...
    def load(self, path: str) -> pd.DataFrame: ...
    def signals(self) -> list[dict]: ...
    def add_signals(self, sigs: list[dict]) -> int: ...
    def remove_signal(self, message_id: str, signal_name: str) -> bool: ...
    def annotations(self) -> AnnotationSet: ...
    def add_annotation(self, label: str, start: float, end: float) -> None: ...
    def watch_events(self, limit: int, since_ts: float | None = None) -> list[dict]: ...
    def watch_stats(self) -> dict: ...


class HeadlessBackend:
    """A capture loaded into this process, plus the signals drafted against it."""

    def __init__(self):
        self.df: pd.DataFrame | None = None
        self.path: str | None = None
        self._signals: list[dict] = []
        self._annotations = AnnotationSet()

    def frames(self) -> pd.DataFrame:
        if self.df is None or self.df.empty:
            raise ValueError("No capture loaded. Call load_log(path) first.")
        return self.df

    def describe(self) -> dict:
        return {"mode": "headless", "path": self.path, "connected": False}

    def load(self, path: str) -> pd.DataFrame:
        from canlab.core.log_parser import parse_log_file
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"no such file: {path}")
        df = parse_log_file(str(p))
        if df.empty:
            raise ValueError(f"no frames found in {path}")
        self.df, self.path = df, str(p)
        return df

    def signals(self) -> list[dict]:
        return list(self._signals)

    def add_signals(self, sigs: list[dict]) -> int:
        self._signals.extend(sigs)
        return len(sigs)

    def remove_signal(self, message_id: str, signal_name: str) -> bool:
        before = len(self._signals)
        self._signals = [s for s in self._signals
                         if not (s.get("message_id") == message_id
                                 and s.get("signal_name") == signal_name)]
        return len(self._signals) < before

    def annotations(self) -> AnnotationSet:
        return self._annotations

    def add_annotation(self, label: str, start: float, end: float) -> None:
        self._annotations.add(label, start, end)

    def watch_events(self, limit: int, since_ts: float | None = None) -> list[dict]:
        return []

    def watch_stats(self) -> dict:
        return {"running": False, "fitted": False,
                "note": "the live watch runs inside the CanLab window"}


# ── helpers ──────────────────────────────────────────────────────────────────

def clean(obj: Any) -> Any:
    """Plain JSON types only. numpy scalars would otherwise be sent as their
    repr, and NaN is not JSON."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [clean(v) for v in obj.tolist()]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        obj = float(obj)
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if hasattr(obj, "as_dict"):
        return clean(obj.as_dict())
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    return str(obj)


def _limit(n: int | None) -> int:
    return max(1, min(int(n or DEFAULT_LIMIT), MAX_LIMIT))


def _hex_row(row) -> str:
    return " ".join(f"{int(row[c]):02X}" if c in row and pd.notna(row[c]) else "--"
                    for c in BYTE_COLS)


# ── the tools ────────────────────────────────────────────────────────────────

class CanLabTools:
    """Every tool as a method; ``register`` hands them to a FastMCP server.

    Methods are plain so they can be called from tests and from the proxy
    without a transport in between. Docstrings are what the assistant reads
    to decide which tool to call, so they say what comes back.
    """

    def __init__(self, backend: Backend | None = None):
        self.backend = backend or HeadlessBackend()

    # -- session -------------------------------------------------------------
    def status(self) -> dict:
        """What CanLab has right now: where the frames come from, how many, how
        many IDs, the capture span in seconds, whether a live bus is connected,
        and how many DBC signals and annotations exist. Call this first."""
        info = dict(self.backend.describe())
        try:
            df = self.backend.frames()
        except ValueError:
            info.update(frames=0, ids=0, span_s=0.0)
        else:
            span = float(df["Timestamp"].max() - df["Timestamp"].min()) if len(df) else 0.0
            info.update(frames=int(len(df)), ids=int(df["ID"].nunique()),
                        span_s=round(span, 3))
        info["dbc_signals"] = len(self.backend.signals())
        info["annotations"] = len(self.backend.annotations().items)
        return clean(info)

    def load_log(self, path: str) -> dict:
        """Load a CAN capture file (SavvyCAN CSV, candump .log, BLF, ASC, pcap,
        MDF4, openpilot rlog) and make it the current capture. Returns frame
        count, ID count and span."""
        df = self.backend.load(path)
        span = float(df["Timestamp"].max() - df["Timestamp"].min())
        return clean({"path": path, "frames": len(df), "ids": df["ID"].nunique(),
                      "span_s": round(span, 3)})

    # -- looking at the capture ----------------------------------------------
    def list_ids(self, limit: int = DEFAULT_LIMIT) -> list[dict]:
        """Arbitration IDs in the capture, busiest first: id (hex), frame count,
        rate in Hz, mean period in ms, DLC, and which bytes ever change."""
        df = self.backend.frames()
        span = float(df["Timestamp"].max() - df["Timestamp"].min()) or 1.0
        out = []
        for cid, g in df.groupby("ID", sort=False):
            moving = [c for c in BYTE_COLS if c in g.columns and g[c].nunique() > 1]
            dlc = int(g["DLC"].mode().iloc[0]) if "DLC" in g.columns and len(g) else 8
            n = len(g)
            out.append({"id": str(cid), "frames": n, "rate_hz": round(n / span, 2),
                        "period_ms": round(1000 * span / n, 2) if n > 1 else None,
                        "dlc": dlc, "changing_bytes": moving})
        out.sort(key=lambda d: -d["frames"])
        return clean(out[:_limit(limit)])

    def byte_stats(self, can_id: str) -> dict:
        """Per-byte min, max, mean, distinct-value count and entropy (bits) for
        one ID. High entropy with all 256 values is a checksum or noise; a few
        distinct values held for runs is a mode or flag byte."""
        from canlab.core.canid import normalize_id
        cid = normalize_id(can_id)
        df = self.backend.frames()
        g = df[df["ID"] == cid]
        if g.empty:
            return {"id": cid, "frames": 0, "bytes": {}}
        stats = {}
        for c in BYTE_COLS:
            if c not in g.columns:
                continue
            s = g[c].dropna()
            if s.empty:
                continue
            counts = np.bincount(s.astype(int).to_numpy(), minlength=256)
            p = counts[counts > 0] / counts.sum()
            stats[c] = {"min": int(s.min()), "max": int(s.max()),
                        "mean": round(float(s.mean()), 2), "distinct": int(s.nunique()),
                        "entropy_bits": round(float(-(p * np.log2(p)).sum()), 3)}
        return clean({"id": cid, "frames": len(g), "bytes": stats})

    def frames(self, can_id: str, n: int = 20, from_end: bool = True,
               start_s: float | None = None) -> list[dict]:
        """Raw frames of one ID as hex: timestamp, DLC and the data bytes. The
        last n by default; from_end=False gives the first n; start_s gives the
        n frames from that capture time on."""
        from canlab.core.canid import normalize_id
        cid = normalize_id(can_id)
        df = self.backend.frames()
        g = df[df["ID"] == cid]
        n = _limit(n)
        if start_s is not None:
            g = g[g["Timestamp"] >= float(start_s)].head(n)
        else:
            g = g.tail(n) if from_end else g.head(n)
        return clean([{"t": round(float(r["Timestamp"]), 4),
                       "dlc": int(r["DLC"]) if "DLC" in r and pd.notna(r["DLC"]) else 8,
                       "data": _hex_row(r)} for _, r in g.iterrows()])

    # -- detectors -----------------------------------------------------------
    def detect_counters_checksums(self) -> dict:
        """Rolling counters and checksum bytes in every message, with the
        algorithm that reproduced the checksum. Only messages with a finding
        are returned."""
        from canlab.core.counter_checksum_detector import detect_counters_and_checksums
        res = detect_counters_and_checksums(self.backend.frames())
        return clean({k: v for k, v in res.items()
                      if v.get("counters") or v.get("checksums")})

    def detect_flags(self, can_id: str | None = None, limit: int = DEFAULT_LIMIT) -> dict:
        """Single-bit switches and small packed fields (indicators, doors,
        gear selectors): the bit, its width, how often it changed and how long
        it held each state. One ID, or every ID when can_id is omitted."""
        from canlab.core.bit_flags import detect_flags
        df = self._frames_for(can_id)
        found = detect_flags(df)
        return clean({k: v[:_limit(limit)] for k, v in found.items()})

    def infer_value_tables(self, can_id: str | None = None) -> dict:
        """Bytes that behave as enumerations (few distinct values, each held
        for runs of frames), with every observed state, its share of frames
        and when it was first seen. Name the states, then add_dbc_signal with
        a value_table."""
        from canlab.core.value_tables import infer_enums
        return clean(infer_enums(self._frames_for(can_id)))

    def detect_boundaries(self, can_id: str | None = None) -> dict:
        """Likely field boundaries from bit-level entropy: contiguous runs of
        active bits per message, as start_bit and length with a confidence."""
        from canlab.core.entropy_boundary import detect_signal_boundaries
        return clean(detect_signal_boundaries(self._frames_for(can_id)))

    def detect_multiplexers(self) -> dict:
        """Messages whose layout depends on a selector byte (multiplexed), with
        the selector and the bytes each mode uses."""
        from canlab.core.mux_detector import detect_all_multiplexers
        return clean(detect_all_multiplexers(self.backend.frames()))

    def correlate(self, id1: str, id2: str, min_r: float = 0.75) -> list[dict]:
        """Byte pairs across two IDs whose values move together (Pearson r at
        the best lag). Use it to find the same quantity sent by two modules."""
        from canlab.core.canid import normalize_id
        from canlab.core.correlation_engine import correlate_id_pair
        return clean(correlate_id_pair(self.backend.frames(), normalize_id(id1),
                                       normalize_id(id2), min_r=min_r))

    def match_opendbc(self, top_k: int = 5) -> list[dict]:
        """Rank commaai/opendbc files by how many of the capture's IDs they
        define. Needs network access the first time (the index is cached)."""
        from canlab.core.opendbc_matcher import match_capture
        ids = set(self.backend.frames()["ID"].unique().tolist())
        return clean(match_capture(ids, top_k=top_k))

    def calibrate(self, reference_csv: str, top_k: int = 8) -> list[dict]:
        """Find the field that linearly explains a physical reference. The CSV
        has columns timestamp,value (GPS speed, OBD RPM). Returns candidates
        with scale, offset and R squared."""
        from canlab.core.reference_calibrate import calibrate_against_reference
        ref = pd.read_csv(reference_csv)
        cols = [c.lower() for c in ref.columns]
        tcol = ref.columns[cols.index("timestamp")] if "timestamp" in cols else ref.columns[0]
        vcol = ref.columns[cols.index("value")] if "value" in cols else ref.columns[1]
        return clean(calibrate_against_reference(self.backend.frames(), ref[tcol].to_numpy(),
                                                 ref[vcol].to_numpy(), top_k=top_k))

    def run_all_detectors(self) -> dict:
        """Every offline detector at once: counters, checksums, flags, value
        tables, entropy boundaries and multiplexers, as one report with totals.
        Slow on big captures; the single detectors return the same detail."""
        from canlab.cli import run_detectors
        report = run_detectors(self.backend.frames(), quiet=True)
        cc = report["counters_checksums"]
        report["totals"] = {
            "counters": sum(len(v["counters"]) for v in cc.values()),
            "checksums": sum(len(v["checksums"]) for v in cc.values()),
            "flags": sum(len(v) for v in report["flags"].values()),
            "enumerations": sum(len(v) for v in report["enums"].values()),
            "boundaries": sum(len(v) for v in report["boundaries"].values()),
            "multiplexed": len(report["multiplexers"]),
        }
        report["counters_checksums"] = {k: v for k, v in cc.items()
                                        if v.get("counters") or v.get("checksums")}
        return clean(report)

    def draft_dbc(self, write_to: str | None = None) -> dict:
        """A DBC drafted from what the detectors found (checksums, counters,
        flags, enumerations), without overlapping signals. Returns the DBC
        text and the signal count; write_to saves it to a file as well."""
        from canlab.cli import draft_signals, run_detectors
        from canlab.core.dbc_manager import signals_to_dbc_string
        sigs = draft_signals(run_detectors(self.backend.frames(), quiet=True))
        text = signals_to_dbc_string(sigs)
        if write_to:
            Path(write_to).expanduser().write_text(text)
        return {"signals": len(sigs), "path": write_to, "dbc": text}

    # -- the DBC ---------------------------------------------------------------
    def list_dbc_signals(self) -> list[dict]:
        """The signals defined so far: message id, name, start_bit, length,
        byte order, sign, scale, offset, unit, and value table if any."""
        return clean(self.backend.signals())

    def add_dbc_signal(self, message_id: str, signal_name: str, start_bit: int,
                       length: int, byte_order: str = "little",
                       value_type: str = "unsigned", scale: float = 1.0,
                       offset: float = 0.0, unit: str = "", description: str = "",
                       min_val: float | None = None, max_val: float | None = None,
                       value_table: dict[str, str] | None = None,
                       message_name: str = "", msg_length: int | None = None) -> dict:
        """Define a signal. start_bit uses DBC numbering (bit 0 is the LSB of
        byte 0; for big-endian give the MSB position). value_table maps raw
        values to state names, e.g. {"0": "PARK", "1": "DRIVE"}. The signal
        is validated and refused if it does not fit the message."""
        from canlab.core.canid import normalize_id
        from canlab.core.dbc_manager import validate_signals
        mid = normalize_id(message_id)
        sig = {
            "message_id": mid, "message_name": message_name or f"MSG_{mid}",
            "signal_name": signal_name, "start_bit": int(start_bit), "length": int(length),
            "byte_order": byte_order, "value_type": value_type,
            "scale": float(scale), "offset": float(offset),
            "min_val": 0 if min_val is None else min_val,
            "max_val": ((1 << int(length)) - 1) if max_val is None else max_val,
            "unit": unit, "description": description,
        }
        if msg_length:
            sig["msg_length"] = int(msg_length)
        if value_table:
            sig["value_table"] = {int(k): str(v) for k, v in value_table.items()}
        errors = validate_signals([sig])
        if errors:
            return {"added": False, "errors": errors}
        for existing in self.backend.signals():
            if existing.get("message_id") == mid and existing.get("signal_name") == signal_name:
                return {"added": False, "errors": [f"{signal_name} already exists on {mid}"]}
        self.backend.add_signals([sig])
        return clean({"added": True, "signal": sig, "total_signals": len(self.backend.signals())})

    def remove_dbc_signal(self, message_id: str, signal_name: str) -> dict:
        """Remove one signal by message id and name."""
        from canlab.core.canid import normalize_id
        ok = self.backend.remove_signal(normalize_id(message_id), signal_name)
        return {"removed": ok, "total_signals": len(self.backend.signals())}

    def export_dbc(self, path: str) -> dict:
        """Write the current signals as a DBC file that cantools and every CAN
        tool can load."""
        from canlab.core.dbc_manager import signals_to_dbc_string
        sigs = self.backend.signals()
        if not sigs:
            return {"written": False, "error": "no signals defined"}
        p = Path(path).expanduser()
        p.write_text(signals_to_dbc_string(sigs))
        return {"written": True, "path": str(p), "signals": len(sigs)}

    def decode(self, can_id: str, data_hex: str | None = None, n: int = 5) -> dict:
        """Decode frames through the current signals. With data_hex ("01 A0 FF")
        that one frame is decoded; otherwise the last n frames of the ID are."""
        from canlab.core.canid import normalize_id
        from canlab.core.dbc_manager import decode_frame, frame_bytes_from_row
        cid = normalize_id(can_id)
        sigs = [s for s in self.backend.signals() if s.get("message_id") == cid]
        if not sigs:
            return {"id": cid, "error": "no signals defined for this id"}
        if data_hex is not None:
            data = bytes.fromhex(data_hex.replace(" ", ""))
            return clean({"id": cid, "data": data.hex(" ").upper(),
                          "values": decode_frame(sigs, cid, data)})
        df = self.backend.frames()
        g = df[df["ID"] == cid].tail(_limit(n))
        rows = []
        for _, r in g.iterrows():
            data = frame_bytes_from_row(r)
            rows.append({"t": round(float(r["Timestamp"]), 4),
                         "data": data.hex(" ").upper(),
                         "values": decode_frame(sigs, cid, data)})
        return clean({"id": cid, "frames": rows})

    # -- the live watch ------------------------------------------------------
    def list_watch_events(self, limit: int = DEFAULT_LIMIT,
                          since_s: float | None = None) -> dict:
        """What the live anomaly watch has reported: bytes out of band, an ID
        gone silent, a burst, an ID the baseline never saw. Events carry the
        frame clock. ``since_s`` returns only events after that time. Also
        says whether the watch is running and what it was fitted on."""
        stats = dict(self.backend.watch_stats())
        events = self.backend.watch_events(_limit(limit), since_s)
        return clean({"running": bool(stats.get("running", False)), "stats": stats,
                      "events": events})

    # -- annotations ---------------------------------------------------------
    def list_annotations(self) -> list[dict]:
        """Marks on the capture timeline ("brake", 12.4 s to 14.1 s) that
        rank_annotations scores every byte and bit against."""
        return clean([{"label": a.label, "start": a.start, "end": a.end}
                      for a in self.backend.annotations().items])

    def add_annotation(self, label: str, start_s: float, end_s: float) -> dict:
        """Mark that something was happening between two capture times, in
        seconds on the capture's own clock (see frames for timestamps)."""
        if end_s <= start_s:
            return {"added": False, "error": "end_s must be after start_s"}
        self.backend.add_annotation(label, float(start_s), float(end_s))
        return {"added": True, "annotations": len(self.backend.annotations().items)}

    def rank_annotations(self, label: str | None = None, top: int = 20) -> list[dict]:
        """Bytes and bits ranked by how well they tracked the annotated
        intervals: correlation for whole bytes, agreement for bits. The top
        entries are the candidates for the signal you annotated."""
        from canlab.core.annotations import rank_candidates
        cands = rank_candidates(self.backend.frames(), self.backend.annotations(),
                                label=label, top=_limit(top))
        return clean([dict(c.as_dict(), summary=c.describe()) for c in cands])

    # -- search and fetch (the connector contract ChatGPT expects) -------------
    def search(self, query: str) -> dict:
        """Search the capture and the signal list. Matches arbitration IDs (hex),
        signal names and descriptions; an empty query lists the busiest IDs.
        Returns {"results": [{"id", "title", "url"}]}; pass an id to fetch."""
        q = (query or "").strip().lower()
        terms = [t for t in q.replace(",", " ").split() if t]
        results = []
        try:
            df = self.backend.frames()
        except ValueError:
            df = None
        if df is not None:
            span = float(df["Timestamp"].max() - df["Timestamp"].min()) or 1.0
            counts = df["ID"].value_counts()
            for cid, n in counts.items():
                cid = str(cid)
                if terms and not any(t.upper().lstrip("0X") in cid or cid.lower() in t
                                     for t in terms):
                    continue
                results.append({"id": f"id:{cid}",
                                "title": f"CAN ID {cid}: {n} frames, {n / span:.1f} Hz",
                                "url": f"canlab://id/{cid}"})
        for s in self.backend.signals():
            hay = f"{s.get('signal_name', '')} {s.get('description', '')} " \
                  f"{s.get('message_id', '')}".lower()
            if terms and not any(t in hay for t in terms):
                continue
            results.append({"id": f"signal:{s.get('message_id')}/{s.get('signal_name')}",
                            "title": f"Signal {s.get('signal_name')} on {s.get('message_id')}",
                            "url": f"canlab://signal/{s.get('message_id')}/{s.get('signal_name')}"})
        if not terms or "status" in terms or "capture" in terms:
            results.insert(0, {"id": "status", "title": "Capture status",
                               "url": "canlab://status"})
        return {"results": results[:50]}

    def fetch(self, id: str) -> dict:
        """Full detail for a search result id: for "id:<hex>" the byte
        statistics, detected flags, value tables and the last frames; for
        "signal:<msg>/<name>" the definition; "status" the capture summary.
        Returns {"id", "title", "text", "url", "metadata"}."""
        key = (id or "").strip()
        if key == "status" or key == "canlab://status":
            st = self.status()
            return {"id": "status", "title": "Capture status",
                    "text": json.dumps(st, indent=1), "url": "canlab://status",
                    "metadata": st}
        if key.startswith("canlab://"):
            key = key[len("canlab://"):].replace("/", ":", 1)
        if key.startswith("id:"):
            cid = key[3:]
            stats = self.byte_stats(cid)
            flags = self.detect_flags(cid).get(stats["id"], [])
            enums = self.infer_value_tables(cid).get(stats["id"], [])
            last = self.frames(cid, n=10)
            lines = [f"CAN ID {stats['id']}: {stats['frames']} frames", "", "Byte statistics:"]
            for b, s in stats["bytes"].items():
                lines.append(f"  {b}: min {s['min']} max {s['max']} mean {s['mean']} "
                             f"distinct {s['distinct']} entropy {s['entropy_bits']} bits")
            if flags:
                lines += ["", "Flags and small fields:"] + [f"  {f['label']}" for f in flags]
            if enums:
                lines += ["", "Enumerated bytes:"] + [
                    f"  B{e['byte']}: {len(e['states'])} states "
                    f"{[s['value'] for s in e['states']]}" for e in enums]
            lines += ["", "Last frames:"] + [f"  [{f['t']}] {f['data']}" for f in last]
            return {"id": f"id:{stats['id']}", "title": f"CAN ID {stats['id']}",
                    "text": "\n".join(lines), "url": f"canlab://id/{stats['id']}",
                    "metadata": {"bytes": stats["bytes"], "flags": flags, "enums": enums}}
        if key.startswith("signal:"):
            mid, _, name = key[7:].partition("/")
            for s in self.backend.signals():
                if s.get("message_id") == mid and s.get("signal_name") == name:
                    return {"id": key, "title": f"Signal {name} on {mid}",
                            "text": json.dumps(clean(s), indent=1),
                            "url": f"canlab://signal/{mid}/{name}", "metadata": clean(s)}
            raise ValueError(f"no signal {name} on {mid}")
        raise ValueError(f"unknown id {id!r}; use search first")

    def list_pgns(self, limit: int = DEFAULT_LIMIT) -> list[dict]:
        """Every 29-bit identifier as a J1939 or NMEA 2000 parameter group:
        the PGN, its name where known, the protocol worked out from the
        identifier, the source address and how many frames it sent. A quick
        way to learn what kind of bus a capture came from."""
        from canlab.core.j1939 import scan_for_j1939
        return clean(scan_for_j1939(self.backend.frames())[:_limit(limit)])

    def list_transport_messages(self, pgn: int | None = None,
                                limit: int = DEFAULT_LIMIT) -> list[dict]:
        """Messages larger than one frame, put back together: J1939 transport
        protocol broadcasts and sessions, and NMEA 2000 fast packets. One row
        per PGN and sender with the count, the size, the last payload as hex
        and its decode where a layout is known (a GNSS fix, a fault-code
        list). Reading these frames one at a time gives confident nonsense,
        so this is the only honest way to see them."""
        from canlab.core.multiframe import reassemble_dataframe, summarize
        rows = summarize(reassemble_dataframe(self.backend.frames()))
        if pgn is not None:
            rows = [r for r in rows if r["pgn"] == int(pgn)]
        return clean(rows[:_limit(limit)])

    # -- registration --------------------------------------------------------
    TOOL_NAMES = (
        "status", "load_log", "list_ids", "byte_stats", "frames",
        "detect_counters_checksums", "detect_flags", "infer_value_tables",
        "detect_boundaries", "detect_multiplexers", "correlate", "match_opendbc",
        "calibrate", "run_all_detectors", "draft_dbc",
        "list_dbc_signals", "add_dbc_signal", "remove_dbc_signal", "export_dbc", "decode",
        "list_annotations", "add_annotation", "rank_annotations",
        "search", "fetch",
        "list_pgns", "list_transport_messages",
        "list_watch_events",
    )

    def register(self, mcp) -> None:
        """Register every tool on a FastMCP server."""
        for name in self.TOOL_NAMES:
            mcp.tool()(getattr(self, name))

    def _frames_for(self, can_id: str | None) -> pd.DataFrame:
        df = self.backend.frames()
        if not can_id:
            return df
        from canlab.core.canid import normalize_id
        g = df[df["ID"] == normalize_id(can_id)]
        if g.empty:
            raise ValueError(f"no frames for id {can_id}")
        return g
