#!/usr/bin/env python3
"""CanLab — CAN Bus Reverse Engineering Workstation."""
import sys
import os

# Ensure canlab/ is on sys.path when running as `python main.py`
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication, QMessageBox, QCheckBox
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont
from theme import QSS, mono_font
from mainwindow import MainWindow

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


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CanLab")
    app.setOrganizationName("CanLab")
    app.setStyleSheet(QSS)
    app.setFont(mono_font())

    _show_safety_disclaimer(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
