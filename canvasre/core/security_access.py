"""UDS Security Access (ISO 14229 service 0x27).

Modes
-----
AUTO        Try all built-in seed→key algorithms in order, then user script.
SCRIPT      Use only the loaded Python script's compute_key() function.
BRUTEFORCE  Iterate all possible keys for the given byte length (1–2 bytes only;
            ECU lockout NRC 0x36 is detected and stops the attempt immediately).

Built-in algorithms (AUTO mode, tried in order)
------------------------------------------------
1. identity      key = seed
2. xor_ff        key = seed ^ 0xFF…FF
3. not_seed      key = ~seed & mask
4. add_const     key = (seed_int + 0xC541) & mask
5. xor_secret    key = seed ^ 0xA1B2C3D4  (truncated to seed length)
6. rotate_left3  key = rol(seed, 3)
7. formula_h     key = ((seed_int + 0x9557) ^ 0xFEED) & mask  (common Korean ECU)
8. sub_const     key = (seed_int - 0x1234) & mask

Session history is persisted to ~/.canlab/security_sessions.json.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

# ── UDS constants ─────────────────────────────────────────────────────────────

SVC_SESSION = 0x10
SVC_SECACC  = 0x27
SVC_TP      = 0x3E

NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQ_ERROR      = 0x24
NRC_EXCEEDED_ATTEMPTS      = 0x36
NRC_TIME_DELAY_NOT_EXPIRED = 0x37

SESSION_NAMES = {0x01: "Default", 0x02: "Programming", 0x03: "Extended"}

_HISTORY_PATH = Path.home() / ".canlab" / "security_sessions.json"


# ── Seed → Key algorithms ─────────────────────────────────────────────────────

def _to_int(seed: bytes) -> int:
    return int.from_bytes(seed, "big")

def _to_bytes(val: int, length: int) -> bytes:
    mask = (1 << (length * 8)) - 1
    return (val & mask).to_bytes(length, "big")

def _rol(seed: bytes, n: int) -> bytes:
    bits  = len(seed) * 8
    val   = _to_int(seed)
    n    &= (bits - 1)
    return _to_bytes(((val << n) | (val >> (bits - n))), len(seed))

BUILTIN_ALGORITHMS: list[tuple[str, callable]] = [
    ("identity",     lambda s: s),
    ("xor_ff",       lambda s: bytes(b ^ 0xFF for b in s)),
    ("not_seed",     lambda s: bytes(~b & 0xFF for b in s)),
    ("add_const",    lambda s: _to_bytes(_to_int(s) + 0xC541, len(s))),
    ("xor_secret",   lambda s: _to_bytes(_to_int(s) ^ (0xA1B2C3D4 & ((1 << len(s)*8)-1)), len(s))),
    ("rotate_left3", lambda s: _rol(s, 3)),
    ("formula_h",    lambda s: _to_bytes((_to_int(s) + 0x9557) ^ 0xFEED, len(s))),
    ("sub_const",    lambda s: _to_bytes(_to_int(s) - 0x1234, len(s))),
]


def load_script_algorithm(script_path: str) -> Optional[callable]:
    """Load compute_key(seed: bytes, level: int) -> bytes from a .py file."""
    ns: dict = {}
    with open(script_path, "r") as f:
        exec(compile(f.read(), script_path, "exec"), ns)  # noqa: S102
    fn = ns.get("compute_key")
    if not callable(fn):
        raise ValueError("Script must define compute_key(seed: bytes, level: int) -> bytes")
    return fn


# ── Session history ────────────────────────────────────────────────────────────

def load_history() -> list[dict]:
    if not _HISTORY_PATH.exists():
        return []
    try:
        return json.loads(_HISTORY_PATH.read_text())
    except Exception:
        return []


def save_history(history: list[dict]):
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_PATH.write_text(json.dumps(history, indent=2))


def append_history(entry: dict):
    h = load_history()
    h.append(entry)
    if len(h) > 500:
        h = h[-500:]
    save_history(h)


# ── Worker ─────────────────────────────────────────────────────────────────────

class SecurityAccessWorker(QThread):
    step_done        = pyqtSignal(str)                    # status line
    seed_received    = pyqtSignal(int, int, bytes)        # ecu_addr, level, seed
    key_accepted     = pyqtSignal(int, int, bytes, bytes, str)  # ecu, level, seed, key, algo
    key_rejected     = pyqtSignal(int, int, int, str)     # ecu, level, nrc, nrc_desc
    lockout_detected = pyqtSignal(int)                    # ecu_addr
    bruteforce_progress = pyqtSignal(int, int)            # current, total
    finished         = pyqtSignal()
    error            = pyqtSignal(str)

    def __init__(self, bus,
                 ecu_addr:      int  = 0x7E0,
                 session_type:  int  = 0x03,
                 access_level:  int  = 0x01,
                 mode:          str  = "AUTO",
                 script_path:   str  = "",
                 custom_expr:   str  = "",
                 bf_key_len:    int  = 2,
                 bf_delay_ms:   int  = 50,
                 parent=None):
        super().__init__(parent)
        self._bus          = bus
        self._ecu_addr     = ecu_addr
        self._session_type = session_type
        self._access_level = access_level
        self._mode         = mode
        self._script_path  = script_path
        self._custom_expr  = custom_expr.strip()
        self._bf_key_len   = bf_key_len
        self._bf_delay_ms  = bf_delay_ms
        self._running      = True

    def stop(self):
        self._running = False
        self.quit()
        self.wait(3000)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _isotp_send(self, data: bytes, timeout: float = 1.0) -> Optional[bytes]:
        if not self._running:
            return None
        try:
            from core.isotp import ISOTPSession
            session = ISOTPSession(self._bus, self._ecu_addr, self._ecu_addr + 0x08)
            return session.send(data, timeout=timeout)
        except Exception as e:
            self.error.emit(str(e))
            return None

    def _open_session(self) -> bool:
        self.step_done.emit(f"Opening {SESSION_NAMES.get(self._session_type,'?')} session (0x{self._session_type:02X})…")
        resp = self._isotp_send(bytes([0x02, SVC_SESSION, self._session_type, 0, 0, 0, 0, 0]))
        if resp and len(resp) >= 2 and resp[1] == 0x50:
            self.step_done.emit(f"  Session opened: 0x{resp[1]:02X}")
            return True
        if resp:
            self.step_done.emit(f"  Session failed: {resp.hex().upper()}")
        else:
            self.step_done.emit("  No response to DiagnosticSessionControl")
        return False

    def _send_tester_present(self):
        self._isotp_send(bytes([0x02, SVC_TP, 0x00, 0, 0, 0, 0, 0]), timeout=0.3)

    def _request_seed(self) -> Optional[bytes]:
        subfunc = self._access_level | 0x01 if (self._access_level & 1 == 0) else self._access_level
        self.step_done.emit(f"Requesting seed — SecurityAccess 0x27 subfunction 0x{subfunc:02X}…")
        resp = self._isotp_send(bytes([0x02, SVC_SECACC, subfunc, 0, 0, 0, 0, 0]))
        if resp is None:
            self.step_done.emit("  No response to seed request")
            return None
        if len(resp) < 3:
            self.step_done.emit(f"  Short response: {resp.hex().upper()}")
            return None
        if resp[1] == 0x7F:
            nrc = resp[3] if len(resp) > 3 else 0
            desc = _nrc_name(nrc)
            self.step_done.emit(f"  Negative response: NRC 0x{nrc:02X} ({desc})")
            if nrc == NRC_EXCEEDED_ATTEMPTS:
                self.lockout_detected.emit(self._ecu_addr)
            return None
        if resp[1] == 0x67:
            seed = bytes(resp[2:])
            self.step_done.emit(f"  Seed received: {seed.hex().upper()}")
            self.seed_received.emit(self._ecu_addr, self._access_level, seed)
            if all(b == 0 for b in seed):
                self.step_done.emit("  Zero seed — ECU already unlocked at this level")
                return b""
            return seed
        self.step_done.emit(f"  Unexpected response: {resp.hex().upper()}")
        return None

    def _send_key(self, key: bytes) -> tuple[bool, int]:
        subfunc = (self._access_level | 0x01) + 1  # even subfunc = send key
        if self._access_level & 1 == 0:
            subfunc = self._access_level
        else:
            subfunc = self._access_level + 1
        payload = bytes([len(key) + 2, SVC_SECACC, subfunc]) + key
        resp    = self._isotp_send(payload)
        if resp is None:
            return False, 0
        if resp[1] == 0x67:
            return True, 0
        if resp[1] == 0x7F:
            nrc = resp[3] if len(resp) > 3 else 0
            return False, nrc
        return False, 0

    # ── Modes ─────────────────────────────────────────────────────────────────

    def run(self):
        try:
            if not self._open_session():
                self.finished.emit()
                return
            time.sleep(0.1)

            if self._mode == "AUTO":
                self._run_auto()
            elif self._mode == "SCRIPT":
                self._run_script()
            elif self._mode == "BRUTEFORCE":
                self._run_bruteforce()
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()

    def _run_auto(self):
        seed = self._request_seed()
        if seed is None:
            return
        if seed == b"":
            self.step_done.emit("Already unlocked — nothing to do.")
            return

        algos = list(BUILTIN_ALGORITHMS)

        # Prepend user script if provided
        if self._script_path:
            try:
                fn = load_script_algorithm(self._script_path)
                algos.insert(0, ("user_script", fn))
                self.step_done.emit(f"Loaded script: {os.path.basename(self._script_path)}")
            except Exception as e:
                self.step_done.emit(f"Script load error: {e}")

        # Prepend custom expression if provided
        if self._custom_expr:
            try:
                expr = self._custom_expr
                fn   = eval(f"lambda seed, level: ({expr})", {"__builtins__": {}},  # noqa: S307
                            {"_to_int": _to_int, "_to_bytes": _to_bytes})
                algos.insert(0, ("custom_expr", lambda s, f=fn: f(s, self._access_level)))
                self.step_done.emit(f"Custom expression: {expr}")
            except Exception as e:
                self.step_done.emit(f"Custom expression error: {e}")

        for algo_name, fn in algos:
            if not self._running:
                return
            try:
                key = fn(seed)
            except Exception as exc:
                self.step_done.emit(f"  {algo_name}: compute error — {exc}")
                continue

            self.step_done.emit(f"  Trying {algo_name}: key={key.hex().upper()} …")
            accepted, nrc = self._send_key(key)

            if accepted:
                self.step_done.emit(f"  ✓ KEY ACCEPTED  algo={algo_name}")
                self.key_accepted.emit(self._ecu_addr, self._access_level, seed, key, algo_name)
                _record_success(self._ecu_addr, self._access_level,
                                self._session_type, seed, key, algo_name)
                return

            desc = _nrc_name(nrc)
            self.step_done.emit(f"  ✗ Rejected  NRC=0x{nrc:02X} ({desc})")
            self.key_rejected.emit(self._ecu_addr, self._access_level, nrc, desc)

            if nrc == NRC_EXCEEDED_ATTEMPTS:
                self.lockout_detected.emit(self._ecu_addr)
                self.step_done.emit("  LOCKOUT DETECTED — stopping")
                return

            # Re-request seed for next attempt (ECU resets it after each send)
            time.sleep(0.1)
            self._send_tester_present()
            seed = self._request_seed()
            if seed is None or seed == b"":
                return

        self.step_done.emit("All algorithms exhausted — key not found.")

    def _run_script(self):
        if not self._script_path:
            self.error.emit("SCRIPT mode requires a script file.")
            return
        try:
            fn = load_script_algorithm(self._script_path)
        except Exception as e:
            self.error.emit(f"Script load error: {e}")
            return

        seed = self._request_seed()
        if seed is None or seed == b"":
            return

        try:
            key = fn(seed, self._access_level)
        except Exception as e:
            self.error.emit(f"compute_key() raised: {e}")
            return

        self.step_done.emit(f"Script key: {key.hex().upper()} — sending…")
        accepted, nrc = self._send_key(key)
        if accepted:
            self.step_done.emit("✓ KEY ACCEPTED")
            self.key_accepted.emit(self._ecu_addr, self._access_level, seed, key, "user_script")
            _record_success(self._ecu_addr, self._access_level,
                            self._session_type, seed, key, "user_script")
        else:
            self.step_done.emit(f"✗ Rejected  NRC=0x{nrc:02X} ({_nrc_name(nrc)})")
            self.key_rejected.emit(self._ecu_addr, self._access_level, nrc, _nrc_name(nrc))
            if nrc == NRC_EXCEEDED_ATTEMPTS:
                self.lockout_detected.emit(self._ecu_addr)

    def _run_bruteforce(self):
        if self._bf_key_len > 2:
            self.step_done.emit("Brute-force limited to ≤2 bytes (65536 max).")
            return

        seed = self._request_seed()
        if seed is None or seed == b"":
            return

        total   = 256 ** self._bf_key_len
        delay_s = self._bf_delay_ms / 1000.0
        self.step_done.emit(f"Brute-force: {total} keys, {self._bf_delay_ms} ms delay…")

        for i in range(total):
            if not self._running:
                return
            key = i.to_bytes(self._bf_key_len, "big")
            accepted, nrc = self._send_key(key)

            if i % 64 == 0:
                self.bruteforce_progress.emit(i, total)

            if accepted:
                self.step_done.emit(f"✓ KEY FOUND  key={key.hex().upper()}")
                self.key_accepted.emit(self._ecu_addr, self._access_level, seed, key, "bruteforce")
                _record_success(self._ecu_addr, self._access_level,
                                self._session_type, seed, key, "bruteforce")
                self.bruteforce_progress.emit(total, total)
                return

            if nrc == NRC_EXCEEDED_ATTEMPTS:
                self.step_done.emit(f"LOCKOUT after {i+1} attempts — stopping.")
                self.lockout_detected.emit(self._ecu_addr)
                self.bruteforce_progress.emit(i, total)
                return

            # Re-request seed periodically — many ECUs change it each round
            if i % 1 == 0:  # every attempt
                time.sleep(delay_s)
                self._send_tester_present()
                seed = self._request_seed()
                if seed is None:
                    return

        self.step_done.emit(f"Brute-force exhausted {total} keys — not found.")
        self.bruteforce_progress.emit(total, total)


# ── NRC table ─────────────────────────────────────────────────────────────────

_NRC_NAMES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x25: "noResponseFromSubnetComponent",
    0x26: "failurePreventsExecutionOfRequestedAction",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceededNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x70: "uploadDownloadNotAccepted",
    0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure",
    0x73: "wrongBlockSequenceCounter",
    0x78: "requestCorrectlyReceivedResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}

def _nrc_name(nrc: int) -> str:
    return _NRC_NAMES.get(nrc, f"unknown_0x{nrc:02X}")


# ── History helpers ────────────────────────────────────────────────────────────

def _record_success(ecu: int, level: int, session: int, seed: bytes, key: bytes, algo: str):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ecu_addr":  f"0x{ecu:03X}",
        "level":     level,
        "session":   SESSION_NAMES.get(session, f"0x{session:02X}"),
        "seed_hex":  seed.hex().upper(),
        "key_hex":   key.hex().upper(),
        "algorithm": algo,
        "success":   True,
    }
    try:
        append_history(entry)
    except Exception:
        pass
