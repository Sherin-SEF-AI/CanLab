"""Shared test fixtures / path setup.

Puts the canlab/ source root on sys.path (imports are relative to it) and, if
python-can isn't installed, injects a minimal fake `can` module so protocol
logic (isotp/injection) can be unit-tested without the hardware library.
"""
import os
import sys
import types

CANLAB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "canlab")
if CANLAB_ROOT not in sys.path:
    sys.path.insert(0, CANLAB_ROOT)

try:  # pragma: no cover - exercised only when python-can is absent
    import can  # noqa: F401
except Exception:
    _can = types.ModuleType("can")

    class _Message:
        def __init__(self, arbitration_id=0, data=b"", is_extended_id=False, **kw):
            self.arbitration_id = arbitration_id
            self.data = bytes(data)
            self.dlc = len(self.data)
            self.is_extended_id = is_extended_id

    _can.Message = _Message
    sys.modules["can"] = _can
