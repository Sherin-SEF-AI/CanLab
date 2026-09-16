"""Dialogs for hardware CAN adapters: edit one, pick from what was detected.

The edit dialog shows what each python-can backend expects for its channel
and which package or driver it needs, and can open the adapter to prove it
works before the user leaves the dialog. Testing listens only; nothing is
transmitted.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout,
)

from canlab.core.adapters import (
    BITRATES, FD_DATA_BITRATES, INTERFACES, Adapter, detect_adapters, probe_adapter,
)
from canlab.theme import mono_font
from canlab.ui.compute_worker import ComputeWorker


class AdapterDialog(QDialog):
    def __init__(self, adapter: Adapter | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CAN adapter")
        self.setMinimumWidth(520)
        self._worker = None
        self._extra_widgets: dict[str, QSpinBox | QLineEdit] = {}
        self._last_hint = ""
        self._build()
        if adapter is not None:
            self._load(adapter)
        else:
            self._on_interface_changed()

    def _build(self):
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("bench PCAN, CANable on the car, ...")
        form.addRow("Name:", self.name_edit)

        self.iface_combo = QComboBox()
        for key, info in INTERFACES.items():
            self.iface_combo.addItem(info.label, key)
        self.iface_combo.setFont(mono_font(9))
        form.addRow("Backend:", self.iface_combo)

        self.channel_combo = QComboBox()
        self.channel_combo.setEditable(True)
        self.channel_combo.setFont(mono_font(9))
        form.addRow("Channel:", self.channel_combo)

        self.bitrate_combo = QComboBox()
        self.bitrate_combo.setEditable(True)
        self.bitrate_combo.addItems([str(b) for b in BITRATES])
        self.bitrate_combo.setCurrentText("500000")
        self.bitrate_combo.setFont(mono_font(9))
        form.addRow("Bitrate (bit/s):", self.bitrate_combo)

        self.chk_fd = QCheckBox("CAN FD")
        self.fd_bitrate_combo = QComboBox()
        self.fd_bitrate_combo.setEditable(True)
        self.fd_bitrate_combo.addItems([str(b) for b in FD_DATA_BITRATES])
        self.fd_bitrate_combo.setFont(mono_font(9))
        self.fd_bitrate_combo.setEnabled(False)
        self.chk_fd.toggled.connect(self.fd_bitrate_combo.setEnabled)
        fd_row = QHBoxLayout()
        fd_row.addWidget(self.chk_fd)
        fd_row.addWidget(QLabel("data bitrate:"))
        fd_row.addWidget(self.fd_bitrate_combo)
        form.addRow("", fd_row)

        self._extra_form = QFormLayout()
        form.addRow(self._extra_form)

        self.lbl_notes = QLabel()
        self.lbl_notes.setWordWrap(True)
        self.lbl_notes.setFont(mono_font(8))
        self.lbl_notes.setObjectName("label_dim")
        form.addRow(self.lbl_notes)
        lay.addLayout(form)

        test_row = QHBoxLayout()
        self.btn_test = QPushButton("Test (listen 1 s)")
        self.btn_test.clicked.connect(self._test)
        self.lbl_test = QLabel("")
        self.lbl_test.setWordWrap(True)
        self.lbl_test.setFont(mono_font(8))
        test_row.addWidget(self.btn_test)
        test_row.addWidget(self.lbl_test, 1)
        lay.addLayout(test_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.iface_combo.currentIndexChanged.connect(self._on_interface_changed)

    # -- backend-specific fields -------------------------------------------
    def _on_interface_changed(self, *_):
        info = INTERFACES[self.iface_combo.currentData()]
        self.channel_combo.lineEdit().setPlaceholderText(info.channel_hint)
        current = self.channel_combo.currentText().strip()
        # A channel the user never typed follows the backend's example.
        if not current or current == self._last_hint:
            self.channel_combo.setCurrentText(info.channel_hint)
        self._last_hint = info.channel_hint
        self.chk_fd.setEnabled(info.fd)
        if not info.fd:
            self.chk_fd.setChecked(False)
        self.bitrate_combo.setEnabled(info.bitrate_in_software)
        while self._extra_form.rowCount():
            self._extra_form.removeRow(0)
        self._extra_widgets.clear()
        for key, label, default in info.extra:
            if isinstance(default, int):
                w = QSpinBox()
                w.setRange(0, 10_000_000)
                w.setValue(default)
            else:
                w = QLineEdit(str(default))
            w.setFont(mono_font(9))
            self._extra_form.addRow(f"{label}:", w)
            self._extra_widgets[key] = w
        parts = []
        if info.requires:
            parts.append(f"Needs: {info.requires}.")
        if info.platforms != "any":
            parts.append(f"Platform: {info.platforms}.")
        if not info.bitrate_in_software:
            parts.append("Bitrate is set on the device by the OS, not here.")
        if info.notes:
            parts.append(info.notes)
        self.lbl_notes.setText("\n".join(parts))

    def _load(self, a: Adapter):
        self.name_edit.setText(a.name)
        idx = self.iface_combo.findData(a.interface)
        self.iface_combo.setCurrentIndex(max(idx, 0))
        self._on_interface_changed()
        self.channel_combo.setCurrentText(a.channel)
        self.bitrate_combo.setCurrentText(str(a.bitrate))
        self.chk_fd.setChecked(a.fd)
        self.fd_bitrate_combo.setCurrentText(str(a.data_bitrate))
        for key, w in self._extra_widgets.items():
            if key in a.extra:
                if isinstance(w, QSpinBox):
                    try:
                        w.setValue(int(a.extra[key]))
                    except (TypeError, ValueError):
                        pass
                else:
                    w.setText(str(a.extra[key]))

    def adapter(self) -> Adapter:
        extra = {}
        for key, w in self._extra_widgets.items():
            extra[key] = w.value() if isinstance(w, QSpinBox) else w.text().strip()
        channel = self.channel_combo.currentText().strip()
        return Adapter(
            name=self.name_edit.text().strip() or f"{self.iface_combo.currentData()} {channel}",
            interface=self.iface_combo.currentData(),
            channel=channel,
            bitrate=_int(self.bitrate_combo.currentText(), 500_000),
            fd=self.chk_fd.isChecked(),
            data_bitrate=_int(self.fd_bitrate_combo.currentText(), 2_000_000),
            extra=extra,
        )

    def _accept(self):
        if not self.channel_combo.currentText().strip():
            self.lbl_test.setText("A channel is required.")
            return
        self.accept()

    # -- test -----------------------------------------------------------------
    def _test(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.btn_test.setEnabled(False)
        self.lbl_test.setText("Opening...")
        self._worker = ComputeWorker(probe_adapter, self.adapter(), listen_s=1.0, parent=self)
        self._worker.done.connect(self._on_tested)
        self._worker.failed.connect(lambda e: self._on_tested({"ok": False, "error": e, "hint": ""}))
        self._worker.start()

    def _on_tested(self, result: dict):
        self.btn_test.setEnabled(True)
        self.lbl_test.setText(format_test_result(result))


def format_test_result(result: dict) -> str:
    if result.get("ok"):
        ids = ", ".join(result.get("ids") or []) or "none"
        text = (f"Opened in {result.get('open_ms', 0)} ms "
                f"({result.get('info') or 'no info'}). "
                f"{result.get('frames', 0)} frame(s) heard from IDs: {ids}.")
        # Whether the bus was truly untouched is the part a user attaching to
        # a vehicle needs, and it is not the same as "we sent nothing".
        if result.get("silent"):
            text += "\nListen-only: the controller acknowledged nothing."
        elif result.get("warning"):
            text += f"\nNote: {result['warning']}"
        return text
    text = f"Failed: {result.get('error', '?')}"
    if result.get("hint"):
        text += f"\nHint: {result['hint']}"
    return text


class DetectDialog(QDialog):
    """Run detection off the GUI thread, let the user tick what to add."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Detected CAN adapters")
        self.setMinimumSize(560, 360)
        self._found: list[Adapter] = []
        lay = QVBoxLayout(self)
        self.lbl = QLabel("Asking each backend and scanning USB serial ports...")
        self.lbl.setFont(mono_font(8))
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self.list = QListWidget()
        self.list.setFont(mono_font(9))
        lay.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._worker = ComputeWorker(detect_adapters, parent=self)
        self._worker.done.connect(self._on_found)
        self._worker.failed.connect(lambda e: self.lbl.setText(f"Detection failed: {e}"))
        self._worker.start()

    def _on_found(self, adapters: list):
        self._found = list(adapters)
        self.list.clear()
        if not adapters:
            self.lbl.setText("Nothing detected. Plug the adapter in, install its driver, or "
                             "add it by hand with its channel name.")
            return
        self.lbl.setText(f"{len(adapters)} found. Tick the ones to add; edit the bitrate "
                         "afterwards if it is not 500 kbit/s.")
        for a in adapters:
            item = QListWidgetItem(f"{a.name}    {a.describe()}\n    {a.detected}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)

    def chosen(self) -> list[Adapter]:
        out = []
        for i in range(self.list.count()):
            if self.list.item(i).checkState() == Qt.CheckState.Checked and i < len(self._found):
                out.append(self._found[i])
        return out


def _int(text: str, default: int) -> int:
    try:
        return int(text.replace("_", "").replace(" ", ""))
    except (TypeError, ValueError):
        return default
