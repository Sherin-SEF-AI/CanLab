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


@pytest.fixture(scope="session", autouse=True)
def isolated_qsettings(tmp_path_factory):
    """Every QSettings("CanLab", "CanLab") in the test process lands in a
    temporary directory, never in the user's real configuration."""
    from PyQt6.QtCore import QSettings
    d = str(tmp_path_factory.mktemp("qsettings"))
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        QSettings.setPath(fmt, QSettings.Scope.UserScope, d)
    yield d


# Held for the life of the process: if the application object is collected
# before the widgets that reference it, Qt crashes during interpreter teardown.
_QT_APP = None


@pytest.fixture(scope="session")
def qcore():
    """An application object so QThreads and widgets work in tests.

    A QApplication when the widget layer is available (widgets segfault under a
    bare QCoreApplication), otherwise a QCoreApplication.
    """
    global _QT_APP
    if _QT_APP is None:
        # The application imports matplotlib (through the dashboard tab)
        # before the QApplication exists. Keep that order here: if Qt loads
        # its font plugins first, matplotlib's FreeType raises "raster
        # overflow" the first time it renders text.
        try:
            from matplotlib.figure import Figure  # noqa: F401
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: F401
        except ImportError:
            pass
        try:
            from PyQt6.QtWidgets import QApplication
            _QT_APP = QApplication.instance() or QApplication([])
        except ImportError:
            from PyQt6.QtCore import QCoreApplication
            _QT_APP = QCoreApplication.instance() or QCoreApplication([])
    yield _QT_APP


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
