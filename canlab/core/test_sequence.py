"""
Automated Test Sequence Runner.

A test sequence is an ordered list of TestStep objects:
  INJECT  — send a specific signal value over CAN
  WAIT    — pause for N milliseconds
  ASSERT  — check that a signal value is within [min, max]
  RECORD  — capture the current value of a signal into the result log

TestSequenceWorker(QThread) executes the sequence and emits:
  step_started(step_idx)
  step_completed(step_idx, ok: bool, message: str)
  sequence_finished(passed: bool, log: list[str])
  error(message: str)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal


class StepType(str, Enum):
    INJECT = "INJECT"
    WAIT   = "WAIT"
    ASSERT = "ASSERT"
    RECORD = "RECORD"


@dataclass
class TestStep:
    step_type:   StepType
    label:       str      = ""

    # INJECT fields
    msg_id:      str      = "000"     # hex
    byte_idx:    int      = 0
    value:       int      = 0         # raw byte value 0-255

    # WAIT fields
    wait_ms:     int      = 100

    # ASSERT / RECORD fields
    assert_msg_id:  str   = "000"
    assert_byte:    int   = 0
    assert_min:     float = 0.0
    assert_max:     float = 255.0

    def to_dict(self) -> dict:
        return {k: (v.value if isinstance(v, Enum) else v)
                for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "TestStep":
        d = dict(d)
        d["step_type"] = StepType(d["step_type"])
        return cls(**d)


class TestSequenceWorker(QThread):
    step_started      = pyqtSignal(int)
    step_completed    = pyqtSignal(int, bool, str)
    sequence_finished = pyqtSignal(bool, list)
    error             = pyqtSignal(str)

    def __init__(self, steps: list[TestStep], bus, parent=None):
        super().__init__(parent)
        self._steps   = steps
        self._bus     = bus
        self._running = True
        self._log: list[str] = []

    def stop(self):
        self._running = False

    def run(self):
        passed = True
        for idx, step in enumerate(self._steps):
            if not self._running:
                self._log.append(f"[{idx}] Aborted.")
                break
            self.step_started.emit(idx)
            ok, msg = self._execute(idx, step)
            if not ok:
                passed = False
            self._log.append(msg)
            self.step_completed.emit(idx, ok, msg)
            if not ok and step.step_type == StepType.ASSERT:
                self._log.append("  → Sequence halted on ASSERT failure.")
                passed = False
                break
        self.sequence_finished.emit(passed, self._log)

    def _execute(self, idx: int, step: TestStep) -> tuple[bool, str]:
        prefix = f"[{idx}] {step.step_type.value}"
        label  = f" ({step.label})" if step.label else ""

        if step.step_type == StepType.WAIT:
            end = time.monotonic() + step.wait_ms / 1000.0
            while time.monotonic() < end and self._running:
                time.sleep(0.01)
            return True, f"{prefix}{label}: waited {step.wait_ms} ms"

        if step.step_type == StepType.INJECT:
            try:
                import can
                data = bytearray(8)
                data[step.byte_idx] = step.value & 0xFF
                msg = can.Message(
                    arbitration_id=int(step.msg_id, 16),
                    data=bytes(data),
                    is_extended_id=False,
                )
                self._bus.send(msg)
                return True, (f"{prefix}{label}: sent 0x{step.msg_id} "
                              f"B{step.byte_idx}=0x{step.value:02X}")
            except Exception as e:
                return False, f"{prefix}{label}: ERROR — {e}"

        if step.step_type in (StepType.ASSERT, StepType.RECORD):
            value = self._sample_value(step.assert_msg_id, step.assert_byte)
            if value is None:
                return False, f"{prefix}{label}: no frames for 0x{step.assert_msg_id}"
            if step.step_type == StepType.RECORD:
                return True, (f"{prefix}{label}: 0x{step.assert_msg_id} "
                              f"B{step.assert_byte} = {value:.2f}")
            ok = step.assert_min <= value <= step.assert_max
            result = "PASS" if ok else "FAIL"
            return ok, (f"{prefix}{label}: {result} — "
                        f"0x{step.assert_msg_id} B{step.assert_byte} = {value:.2f} "
                        f"(expected [{step.assert_min}, {step.assert_max}])")

        return False, f"{prefix}: unknown step type"

    def _sample_value(self, msg_id: str, byte_idx: int) -> Optional[float]:
        """Read up to 5 frames for the given ID and return the last byte value."""
        try:
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                frame = self._bus.recv(timeout=0.05)
                if frame and f"{frame.arbitration_id:03X}" == msg_id.upper():
                    if len(frame.data) > byte_idx:
                        return float(frame.data[byte_idx])
        except Exception:
            pass
        return None
