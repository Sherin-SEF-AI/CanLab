#!/usr/bin/env python3
"""CanLab — CAN Bus Reverse Engineering Workstation."""
import os
import sys

if __package__ in (None, ""):
    # Run as a plain file (python canlab/main.py): make the package importable.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "canlab"

from PyQt6.QtWidgets import QApplication, QMessageBox, QCheckBox
from PyQt6.QtCore import QSettings

from canlab.logging_config import configure_logging, install_excepthook

_DISCLAIMER = """\
SAFETY WARNING — READ BEFORE USE

CanLab can inject frames, replay logs, and fuzz CAN buses.
These features MUST only be used on isolated bench setups
(benchtop ECUs, vcan0, or dedicated lab hardware).

NEVER connect to or inject frames on a vehicle's live CAN bus.
Doing so can interfere with braking, steering, airbags, and
other safety-critical systems, causing injury or death.

By clicking OK you confirm you will use injection features
only on isolated, non-safety-critical hardware.
"""


def _show_safety_disclaimer(app: QApplication) -> None:
    settings = QSettings("CanLab", "CanLab")
    if settings.value("disclaimer_accepted", False, type=bool):
        return

    box = QMessageBox()
    box.setWindowTitle("CanLab — Safety Warning")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(_DISCLAIMER)
    box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Ok)

    cb = QCheckBox("Do not show again")
    box.setCheckBox(cb)

    result = box.exec()
    if result == QMessageBox.StandardButton.Cancel:
        sys.exit(0)

    if cb.isChecked():
        settings.setValue("disclaimer_accepted", True)


def _app_icon():
    """The CanLab mark, for the window, the taskbar and the dock."""
    from PyQt6.QtGui import QIcon
    return QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "canlab.png"))


def main():
    configure_logging()
    install_excepthook()
    from canlab.theme import QSS, mono_font
    from canlab.mainwindow import MainWindow
    from canlab.ui.error_dialog import install_error_dialog

    app = QApplication(sys.argv)
    app.setApplicationName("CanLab")
    app.setOrganizationName("CanLab")
    app.setWindowIcon(_app_icon())
    app.setStyleSheet(QSS)
    app.setFont(mono_font())
    install_error_dialog(app)

    _show_safety_disclaimer(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
