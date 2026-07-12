"""CAN MitM / Gateway — bidirectional frame bridge with filter/modify rules.

Architecture
------------
GatewayWorker opens two independent CAN buses (bus_a, bus_b).
Two daemon threads read from each bus and push (src, msg) onto a shared queue.
The QThread.run() loop drains the queue, applies ordered rules, then forwards
frames to the opposite bus and emits UI signals.

Rule dict schema
----------------
{
    "id":        str   hex ID to match, e.g. "1A0"; "" = match any,
    "direction": str   "A→B" | "B→A" | "Both",
    "action":    str   "Pass" | "Block" | "Modify",
    "mod_byte":  int   0–7  byte index to overwrite (Modify only),
    "mod_val":   int   0–255 new byte value (Modify only),
    "new_id":    str   hex string; non-empty rewrites the arbitration ID,
    "label":     str   human-readable name shown in the rule table,
}

The first matching rule wins (ordered list).  Frames that match no rule are
forwarded unchanged ("implicit pass").
"""
import queue
import threading
import time
from typing import Optional

import can
from PyQt6.QtCore import QThread, pyqtSignal


# ── Helper ────────────────────────────────────────────────────────────────────

def _open_bus(cfg: dict) -> can.BusABC:
    return can.interface.Bus(
        channel=cfg.get("channel", "can0"),
        bustype=cfg.get("interface", "socketcan"),
        bitrate=cfg.get("bitrate", 500_000),
    )


def _rule_matches(rule: dict, arb_id: int, direction: str) -> bool:
    id_filter = rule.get("id", "").strip()
    if id_filter:
        try:
            if int(id_filter, 16) != arb_id:
                return False
        except ValueError:
            return False
    rule_dir = rule.get("direction", "Both")
    if rule_dir != "Both" and rule_dir != direction:
        return False
    return True


def _apply_rule(rule: dict, arb_id: int, data: bytes) -> tuple[Optional[int], Optional[bytes]]:
    """Return (new_arb_id, new_data) after applying a Modify rule, or originals."""
    new_id   = arb_id
    new_data = bytearray(data)

    new_id_str = rule.get("new_id", "").strip()
    if new_id_str:
        try:
            new_id = int(new_id_str, 16)
        except ValueError:
            pass

    if rule.get("action") == "Modify":
        idx = int(rule.get("mod_byte", 0))
        val = int(rule.get("mod_val",  0))
        if 0 <= idx < len(new_data):
            new_data[idx] = val & 0xFF

    return new_id, bytes(new_data)


# ── Bus reader thread ─────────────────────────────────────────────────────────

class _BusReader(threading.Thread):
    """Reads frames from one bus and pushes (src_label, msg) onto q."""

    def __init__(self, bus: can.BusABC, src: str, q: queue.Queue, stop_evt: threading.Event):
        super().__init__(daemon=True)
        self._bus   = bus
        self._src   = src
        self._q     = q
        self._stop  = stop_evt

    def run(self):
        while not self._stop.is_set():
            try:
                msg = self._bus.recv(timeout=0.05)
                if msg:
                    self._q.put((self._src, msg))
            except Exception:
                if not self._stop.is_set():
                    time.sleep(0.01)


# ── Gateway worker ────────────────────────────────────────────────────────────

class GatewayWorker(QThread):
    frame_forwarded = pyqtSignal(str, int, bytes)   # direction, arb_id, data
    frame_blocked   = pyqtSignal(str, int)           # direction, arb_id
    frame_modified  = pyqtSignal(str, int, bytes)    # direction, arb_id, new_data
    stats_updated   = pyqtSignal(dict)               # counters snapshot
    error           = pyqtSignal(str)

    def __init__(self, bus_a_cfg: dict, bus_b_cfg: dict,
                 rules: list | None = None, parent=None):
        super().__init__(parent)
        self._cfg_a   = bus_a_cfg
        self._cfg_b   = bus_b_cfg
        self._rules   = list(rules or [])
        self._running = True

        self._stats = {
            "fwd_a_b":  0,
            "fwd_b_a":  0,
            "blocked":  0,
            "modified": 0,
            "rate_a_b": 0.0,
            "rate_b_a": 0.0,
        }
        self._cnt_a_b = 0   # per-second counters
        self._cnt_b_a = 0
        self._last_tick = time.monotonic()

    def set_rules(self, rules: list):
        self._rules = list(rules)

    def stop(self):
        self._running = False
        self.quit()
        self.wait(3000)

    def run(self):
        from core.safety import require_armed, BusNotArmedError
        try:
            require_armed()
        except BusNotArmedError as e:
            self.error.emit(str(e))
            return
        try:
            bus_a = _open_bus(self._cfg_a)
            bus_b = _open_bus(self._cfg_b)
        except Exception as e:
            self.error.emit(f"Could not open bus: {e}")
            return

        q        = queue.Queue(maxsize=4096)
        stop_evt = threading.Event()
        _BusReader(bus_a, "A→B", q, stop_evt).start()
        _BusReader(bus_b, "B→A", q, stop_evt).start()

        try:
            while self._running:
                try:
                    src, msg = q.get(timeout=0.1)
                except queue.Empty:
                    self._maybe_emit_stats()
                    continue

                arb_id = msg.arbitration_id
                data   = bytes(msg.data)
                rules  = self._rules   # snapshot

                action   = "Pass"
                new_arb  = arb_id
                new_data = data
                matched_rule = None

                for rule in rules:
                    if _rule_matches(rule, arb_id, src):
                        matched_rule = rule
                        action = rule.get("action", "Pass")
                        break

                if action == "Block":
                    self._stats["blocked"] += 1
                    self.frame_blocked.emit(src, arb_id)
                    self._maybe_emit_stats()
                    continue

                # Apply id/byte rewrites for any matched non-Block rule so that a
                # non-empty new_id rewrites the arbitration ID for Pass rules too
                # (per the rule schema), not only for Modify.
                if matched_rule:
                    new_arb, new_data = _apply_rule(matched_rule, arb_id, data)

                if action == "Modify":
                    self._stats["modified"] += 1
                    self.frame_modified.emit(src, new_arb, new_data)
                else:
                    self.frame_forwarded.emit(src, new_arb, new_data)

                # Forward to the opposite bus
                dest_bus = bus_b if src == "A→B" else bus_a
                try:
                    fwd_msg = can.Message(
                        arbitration_id=new_arb,
                        data=new_data,
                        is_extended_id=msg.is_extended_id,
                    )
                    dest_bus.send(fwd_msg)
                except Exception as e:
                    self.error.emit(f"Forward error ({src}): {e}")

                if src == "A→B":
                    self._stats["fwd_a_b"] += 1
                    self._cnt_a_b += 1
                else:
                    self._stats["fwd_b_a"] += 1
                    self._cnt_b_a += 1

                self._maybe_emit_stats()

        finally:
            stop_evt.set()
            try:
                bus_a.shutdown()
            except Exception:
                pass
            try:
                bus_b.shutdown()
            except Exception:
                pass

    def _maybe_emit_stats(self):
        now = time.monotonic()
        dt  = now - self._last_tick
        if dt >= 1.0:
            self._stats["rate_a_b"] = self._cnt_a_b / dt
            self._stats["rate_b_a"] = self._cnt_b_a / dt
            self._cnt_a_b = 0
            self._cnt_b_a = 0
            self._last_tick = now
            self.stats_updated.emit(dict(self._stats))
