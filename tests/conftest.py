"""Shared test setup.

Runs Qt headless by default and, if python-can isn't installed, injects a
minimal fake `can` module so protocol logic can be unit-tested without the
hardware library. The `canlab` package itself must be installed (pip install -e .).
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qcore():
    """A QCoreApplication so QThread-based workers can run in tests."""
    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


@pytest.fixture
def armed():
    """Arm transmit for the duration of a test (tests must opt in explicitly)."""
    from canlab.core import safety
    safety.set_armed(True)
    yield
    safety.set_armed(False)

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
