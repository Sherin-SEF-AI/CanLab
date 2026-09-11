"""GUI bridge for uncaught exceptions: show a non-modal error box on the GUI thread."""
from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from canlab.logging_config import LOG_FILE, add_exception_listener


class ErrorBridge(QObject):
    """Lives in the GUI thread; ``error_occurred`` may be emitted from any thread."""
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._boxes: list = []
        self.error_occurred.connect(self._show)

    def _show(self, text: str) -> None:
        box = QMessageBox(QMessageBox.Icon.Critical, "CanLab — unexpected error", text)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.destroyed.connect(lambda *_: self._boxes.remove(box) if box in self._boxes else None)
        self._boxes.append(box)
        box.show()


def install_error_dialog(app) -> ErrorBridge:
    bridge = ErrorBridge(app)

    def _listener(exc_type, exc, tb):
        bridge.error_occurred.emit(
            f"{exc_type.__name__}: {exc}\n\nDetails were written to {LOG_FILE}")

    add_exception_listener(_listener)
    return bridge
