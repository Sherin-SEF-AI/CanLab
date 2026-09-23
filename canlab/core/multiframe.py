"""Multi-frame reassembly: J1939 transport protocol and NMEA 2000 fast packet.

A CAN frame carries eight bytes. Both protocols move larger messages by
splitting them across frames, and until those frames are put back together
the message does not exist: the PGN scan could only label such traffic
"needs reassembly", and the counter detector saw the sequence bytes as
rolling counters, which is exactly what they look like one frame at a time.

Two transports, one accumulator:

- **J1939-21 transport protocol.** A connection-management frame (PGN 60416,
  TP.CM) announces a message: its size, its packet count and the PGN it
  carries. Data frames (PGN 60160, TP.DT) follow, each with a sequence
  number and seven bytes. A broadcast (BAM) sends them unprompted; a
  point-to-point session (RTS/CTS) waits for the receiver to say how many it
  may send. Sessions are keyed by sender and destination.
- **NMEA 2000 fast packet.** No announcement: the first frame's first byte
  carries a sequence id in its top three bits and a counter of 0 in the
  low five, the second byte is the total length, and six data bytes follow.
  Later frames repeat the sequence id, count up, and carry seven bytes.
  Sessions are keyed by PGN, sender and sequence id.

This is an observer. It never sends a CTS or an acknowledgement, so it can
follow a session between two other nodes on a vehicle without becoming a
third party to it. Nothing here imports the transmit gate because nothing
here transmits.

Timing uses the frame clock, never wall time, so a loaded log replays the
same way every time and a live capture is judged by its own timestamps.
"""
from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field

from canlab.core.canid import normalize_id
from canlab.core.j1939 import _N2K_NAMES, decode_pgn, is_nmea2000, pgn_name

TP_CM_PGN = 0xEC00        # 60416, connection management
TP_DT_PGN = 0xEB00        # 60160, data transfer
TRANSPORT_PGNS = frozenset({TP_CM_PGN, TP_DT_PGN})

CM_RTS, CM_CTS, CM_EOM_ACK, CM_BAM, CM_ABORT = 16, 17, 19, 32, 255
_CM_NAMES = {CM_RTS: "RTS", CM_CTS: "CTS", CM_EOM_ACK: "EndOfMsgACK",
             CM_BAM: "BAM", CM_ABORT: "Abort"}

#: J1939-21 timing. T1 is the longest gap allowed between BAM data packets;
#: T2 is how long a point-to-point session may wait for the other side.
BAM_T1_S = 0.75
TP_T2_S = 1.25
#: NMEA 2000 sets no normative gap for fast packet frames. In the recordings
#: this was written against, frames of one message arrive a few milliseconds
#: apart, so three quarters of a second is generous without letting a stale
#: sequence swallow a later one that reuses its id.
FAST_PACKET_GAP_S = 0.75

#: J1939-21 caps a transport message at 255 packets of 7 bytes.
MAX_TP_BYTES = 1785
#: Fast packet length is one byte and 223 is the largest the framing allows.
MAX_FAST_PACKET_BYTES = 223

#: NMEA 2000 PGNs the name table marks as multi-frame.
def _fast_packet_pgns() -> frozenset:
    """Every NMEA 2000 PGN sent as a fast packet: the hand-written table's,
    and every other standard one canboat lists, so a message is reassembled
    whether or not CanLab can decode it."""
    from canlab.core import n2k_db
    hand = {pgn for pgn, (_name, single) in _N2K_NAMES.items() if not single}
    single = {pgn for pgn, (_name, s) in _N2K_NAMES.items() if s}
    return frozenset(hand | (n2k_db.fast_packet_pgns() - single))


FAST_PACKET_PGNS = _fast_packet_pgns()


@dataclass
class Message:
    """One reassembled message, or one that could not be finished."""

    protocol: str            # "J1939" or "NMEA 2000", by the carried PGN
    transport: str           # "BAM", "RTS/CTS" or "fast-packet"
    pgn: int
    sa: int
    da: int                  # 0xFF for broadcasts and fast packets
    data: bytes
    t_start: float
    t_end: float
    frames: int
    bus: int = 0
    complete: bool = True
    reason: str = ""         # for the incomplete: sequence, timeout, abort, log end

    @property
    def pgn_name(self) -> str:
        return pgn_name(self.pgn)

    @property
    def size(self) -> int:
        return len(self.data)

    def as_dict(self) -> dict:
        return {"protocol": self.protocol, "transport": self.transport,
                "pgn": self.pgn, "pgn_name": self.pgn_name, "sa": self.sa,
                "da": self.da, "size": self.size, "frames": self.frames,
                "t_start": self.t_start, "t_end": self.t_end, "bus": self.bus,
                "complete": self.complete, "reason": self.reason,
                "data_hex": self.data.hex(" ").upper()}


# ── frame-level parsing ──────────────────────────────────────────────────────

def split_id(arb_id: int) -> tuple[int, int, int]:
    """(pgn, source address, destination address) of a 29-bit identifier.

    The same split as parse_j1939_id, without building its dict: this runs
    once per frame.
    """
    dp = (arb_id >> 24) & 0x03
    pf = (arb_id >> 16) & 0xFF
    ps = (arb_id >> 8) & 0xFF
    sa = arb_id & 0xFF
    if pf >= 0xF0:
        return (dp << 16) | (pf << 8) | ps, sa, 0xFF
    return (dp << 16) | (pf << 8), sa, ps


def parse_tp_cm(data: bytes) -> dict:
    """The fields of a TP.CM frame: control, size, packets, max per CTS, PGN."""
    b = bytes(data) + b"\xFF" * (8 - len(data))
    return {
        "control": b[0],
        "control_name": _CM_NAMES.get(b[0], f"control {b[0]}"),
        "size": b[1] | (b[2] << 8),
        "packets": b[3],
        "max_per_cts": b[4],
        "pgn": b[5] | (b[6] << 8) | (b[7] << 16),
    }


def parse_fast_packet_header(b0: int) -> tuple[int, int]:
    """(sequence id, frame counter) from a fast packet's first byte."""
    return (b0 >> 5) & 0x07, b0 & 0x1F


def is_transport_id(arb_id: int) -> bool:
    """True for frames this reassembler wants; a predicate for BusHub.subscribe."""
    if arb_id <= 0x7FF:
        return False
    pgn, _sa, _da = split_id(arb_id)
    return pgn in TRANSPORT_PGNS or pgn in FAST_PACKET_PGNS


# ── sessions ─────────────────────────────────────────────────────────────────

@dataclass
class _TpSession:
    sa: int
    da: int
    pgn: int
    size: int
    packets: int
    transport: str
    t_start: float
    t_last: float
    bus: int
    buf: bytearray = field(default_factory=bytearray)
    expected: int = 1
    frames: int = 1          # the TP.CM counts


@dataclass
class _FpSession:
    pgn: int
    sa: int
    seq: int
    total: int
    t_start: float
    t_last: float
    bus: int
    buf: bytearray = field(default_factory=bytearray)
    expected: int = 1
    frames: int = 1


class Reassembler:
    """Feed it frames; take finished messages out.

    Works the same offline over a DataFrame and live off a subscription, the
    way the sniffer does: `update` is the one entry point, and the other
    feeders only unpack their input into it.
    """

    def __init__(self, *, bam_timeout_s: float = BAM_T1_S,
                 cts_timeout_s: float = TP_T2_S,
                 fast_packet_timeout_s: float = FAST_PACKET_GAP_S,
                 keep: int = 10_000):
        self._bam_timeout = bam_timeout_s
        self._cts_timeout = cts_timeout_s
        self._fp_timeout = fast_packet_timeout_s
        self._tp: dict[tuple[int, int], _TpSession] = {}
        self._fp: dict[tuple[int, int, int], _FpSession] = {}
        self.messages: deque[Message] = deque(maxlen=keep)
        self.dropped: deque[Message] = deque(maxlen=1000)
        self._complete = 0
        self._dropped = 0
        self._orphans = 0
        self._observed = 0
        self._reasons: Counter = Counter()

    # -- feeding ---------------------------------------------------------------

    def update(self, arb_id: int, data, ts: float, bus: int = 0) -> list[Message]:
        """One frame. Returns the messages it completed, usually none."""
        if arb_id <= 0x7FF or data is None or len(data) < 2:
            return []
        pgn, sa, da = split_id(int(arb_id))
        if pgn not in TRANSPORT_PGNS and pgn not in FAST_PACKET_PGNS:
            return []
        payload = bytes(int(b) & 0xFF for b in data)
        self._observed += 1
        ts = float(ts)
        out = self.expire(ts)
        if pgn == TP_CM_PGN:
            out.extend(self._on_tp_cm(sa, da, payload, ts, int(bus or 0)))
        elif pgn == TP_DT_PGN:
            out.extend(self._on_tp_dt(sa, da, payload, ts))
        else:
            out.extend(self._on_fast_packet(pgn, sa, payload, ts, int(bus or 0)))
        return out

    def update_message(self, msg) -> list[Message]:
        """A python-can message (or a double shaped like one)."""
        ts = getattr(msg, "timestamp", None)
        if not ts:
            ts = time.time()
        # python-can's channel is often a name ("can0"); only a number is a bus index.
        channel = getattr(msg, "channel", 0)
        bus = channel if isinstance(channel, int) else 0
        return self.update(int(msg.arbitration_id), bytes(msg.data or b""), ts, bus)

    def update_frame_rows(self, rows) -> list[Message]:
        """Hub tuples: (ts, arb_id, extended, bus, dlc, data)."""
        out = []
        for ts, arb_id, _ext, bus, _dlc, data in rows:
            out.extend(self.update(int(arb_id), data, float(ts), bus or 0))
        return out

    def update_dataframe(self, df) -> list[Message]:
        """A canonical frames DataFrame, in its own order.

        Only the transport rows are walked in Python. The mask is built from
        the distinct identifiers, of which a busy log has a few hundred, not
        from its hundreds of thousands of rows.
        """
        if df is None or df.empty or "ID" not in df.columns:
            return []
        wanted = {cid for cid in df["ID"].astype(str).unique()
                  if _wanted_hex_id(cid)}
        if not wanted:
            return []
        sub = df[df["ID"].astype(str).isin(wanted)]
        cols = sorted((c for c in sub.columns if c.startswith("B") and c[1:].isdigit()),
                      key=lambda c: int(c[1:]))
        ts = sub["Timestamp"].to_numpy(dtype=float)
        ids = sub["ID"].astype(str).to_numpy()
        buses = sub["Bus"].to_numpy() if "Bus" in sub.columns else [0] * len(sub)
        bytes_matrix = sub[cols].to_numpy(dtype=float)
        out = []
        for i in range(len(sub)):
            row = bytes_matrix[i]
            payload = []
            for v in row:
                if v != v:                       # NaN: the frame ended here
                    break
                payload.append(int(v) & 0xFF)
            out.extend(self.update(int(ids[i], 16), bytes(payload), ts[i],
                                   int(buses[i]) if buses[i] == buses[i] else 0))
        return out

    # -- session handling ----------------------------------------------------

    def _on_tp_cm(self, sa: int, da: int, b: bytes, ts: float, bus: int) -> list[Message]:
        cm = parse_tp_cm(b)
        control = cm["control"]
        out: list[Message] = []
        if control in (CM_BAM, CM_RTS):
            key = (sa, da)
            stale = self._tp.pop(key, None)
            if stale is not None:
                out.append(self._drop_tp(stale, "sequence", ts))
            size, packets = cm["size"], cm["packets"]
            if not (9 <= size <= MAX_TP_BYTES) or packets != -(-size // 7):
                # A malformed announcement is recorded rather than opened.
                bad = _TpSession(sa, da, cm["pgn"], size, packets,
                                 "BAM" if control == CM_BAM else "RTS/CTS", ts, ts, bus)
                out.append(self._drop_tp(bad, "abort", ts))
                return out
            self._tp[key] = _TpSession(
                sa, da, cm["pgn"], size, packets,
                "BAM" if control == CM_BAM else "RTS/CTS", ts, ts, bus)
            return out
        if control == CM_CTS:
            # The receiver talks back: the session is the reverse pair.
            session = self._tp.get((da, sa))
            if session is not None:
                session.t_last = ts
            return out
        if control == CM_EOM_ACK:
            self._tp.pop((da, sa), None)
            return out
        if control == CM_ABORT:
            for key in ((sa, da), (da, sa)):
                session = self._tp.pop(key, None)
                if session is not None:
                    out.append(self._drop_tp(session, "abort", ts))
            return out
        return out

    def _on_tp_dt(self, sa: int, da: int, b: bytes, ts: float) -> list[Message]:
        session = self._tp.get((sa, da))
        if session is None:
            self._orphans += 1
            return []
        seq = b[0]
        if seq != session.expected:
            del self._tp[(sa, da)]
            return [self._drop_tp(session, "sequence", ts)]
        session.buf.extend(b[1:8])
        session.frames += 1
        session.t_last = ts
        session.expected += 1
        if seq >= session.packets:
            del self._tp[(sa, da)]
            data = bytes(session.buf[:session.size])
            return [self._finish(session.transport, session.pgn, sa, da, data,
                                 session.t_start, ts, session.frames, session.bus)]
        return []

    def _on_fast_packet(self, pgn: int, sa: int, b: bytes, ts: float, bus: int) -> list[Message]:
        seq, counter = parse_fast_packet_header(b[0])
        key = (pgn, sa, seq)
        out: list[Message] = []
        if counter == 0:
            stale = self._fp.pop(key, None)
            if stale is not None:
                out.append(self._drop_fp(stale, "sequence", ts))
            total = b[1]
            if total == 0 or total > MAX_FAST_PACKET_BYTES:
                return out
            session = _FpSession(pgn, sa, seq, total, ts, ts, bus)
            session.buf.extend(b[2:8])
            if len(session.buf) >= total:
                out.append(self._finish("fast-packet", pgn, sa, 0xFF,
                                        bytes(session.buf[:total]), ts, ts, 1, bus))
            else:
                self._fp[key] = session
            return out
        session = self._fp.get(key)
        if session is None:
            self._orphans += 1
            return out
        if counter != session.expected:
            del self._fp[key]
            out.append(self._drop_fp(session, "sequence", ts))
            return out
        session.buf.extend(b[1:8])
        session.frames += 1
        session.t_last = ts
        session.expected += 1
        if len(session.buf) >= session.total:
            del self._fp[key]
            out.append(self._finish("fast-packet", pgn, sa, 0xFF,
                                    bytes(session.buf[:session.total]),
                                    session.t_start, ts, session.frames, session.bus))
        return out

    # -- lifecycle -------------------------------------------------------------

    def expire(self, now: float) -> list[Message]:
        """Give up on sessions that have gone quiet, by the frame clock."""
        out: list[Message] = []
        for key, session in list(self._tp.items()):
            limit = self._bam_timeout if session.transport == "BAM" else self._cts_timeout
            if now - session.t_last > limit:
                del self._tp[key]
                out.append(self._drop_tp(session, "timeout", now))
        for key, session in list(self._fp.items()):
            if now - session.t_last > self._fp_timeout:
                del self._fp[key]
                out.append(self._drop_fp(session, "timeout", now))
        return out

    def finish(self) -> list[Message]:
        """End of a log: whatever is still open did not complete."""
        out: list[Message] = []
        for session in list(self._tp.values()):
            out.append(self._drop_tp(session, "log end", session.t_last))
        for session in list(self._fp.values()):
            out.append(self._drop_fp(session, "log end", session.t_last))
        self._tp.clear()
        self._fp.clear()
        return out

    def clear(self) -> None:
        self._tp.clear()
        self._fp.clear()
        self.messages.clear()
        self.dropped.clear()
        self._complete = self._dropped = self._orphans = self._observed = 0
        self._reasons.clear()

    def stats(self) -> dict:
        return {"observed": self._observed, "complete": self._complete,
                "dropped": self._dropped, "orphans": self._orphans,
                "pending": len(self._tp) + len(self._fp),
                "by_reason": dict(self._reasons)}

    # -- bookkeeping -----------------------------------------------------------

    def _finish(self, transport, pgn, sa, da, data, t_start, t_end, frames, bus) -> Message:
        msg = Message(_protocol_of(pgn), transport, pgn, sa, da, data,
                      t_start, t_end, frames, bus)
        self.messages.append(msg)
        self._complete += 1
        return msg

    def _drop_tp(self, s: _TpSession, reason: str, now: float) -> Message:
        return self._drop(Message(_protocol_of(s.pgn), s.transport, s.pgn, s.sa, s.da,
                                  bytes(s.buf), s.t_start, now, s.frames, s.bus,
                                  complete=False, reason=reason))

    def _drop_fp(self, s: _FpSession, reason: str, now: float) -> Message:
        return self._drop(Message(_protocol_of(s.pgn), "fast-packet", s.pgn, s.sa, 0xFF,
                                  bytes(s.buf), s.t_start, now, s.frames, s.bus,
                                  complete=False, reason=reason))

    def _drop(self, msg: Message) -> Message:
        self.dropped.append(msg)
        self._dropped += 1
        self._reasons[msg.reason] += 1
        return msg


def _protocol_of(pgn: int) -> str:
    return "NMEA 2000" if is_nmea2000(pgn) else "J1939"


def _wanted_hex_id(cid: str) -> bool:
    try:
        return is_transport_id(int(cid, 16))
    except ValueError:
        return False


# ── whole-log helpers ────────────────────────────────────────────────────────

def reassemble_dataframe(df) -> list[Message]:
    """Every complete message in a loaded capture, in the order they finished."""
    r = Reassembler()
    r.update_dataframe(df)
    r.finish()
    return sorted(r.messages, key=lambda m: (m.t_end, m.sa, m.pgn))


def summarize(messages) -> list[dict]:
    """One row per (protocol, transport, PGN, sender, destination).

    The decode is of the most recent message in the group, through the same
    router the single-frame scan uses, so a reassembled DM1 or a GNSS fix
    reads the way its single-frame neighbours do.
    """
    groups: dict[tuple, list[Message]] = {}
    for m in messages:
        if not m.complete:
            continue
        groups.setdefault((m.protocol, m.transport, m.pgn, m.sa, m.da), []).append(m)
    rows = []
    for (protocol, transport, pgn, sa, da), items in groups.items():
        last = items[-1]
        rows.append({
            "protocol": protocol, "transport": transport, "pgn": pgn,
            "pgn_name": pgn_name(pgn), "sa": sa, "da": da,
            "sa_name": f"SA 0x{sa:02X}", "count": len(items),
            "bytes": last.size, "frames": last.frames,
            "first_t": items[0].t_start, "last_t": last.t_end,
            "last_data_hex": last.data.hex(" ").upper(),
            "decoded": decode_pgn(pgn, last.data, reassembled=True),
        })
    rows.sort(key=lambda r: (-r["count"], r["pgn"], r["sa"]))
    return rows


def hex_id(arb_id: int) -> str:
    return normalize_id(f"{arb_id:X}")
