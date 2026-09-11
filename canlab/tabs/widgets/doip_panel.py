"""DoIP panel (ISO 13400): discover entities, activate routing, send UDS over IP.

DoIP talks over TCP/UDP rather than CAN, but it still transmits, so discovery
and every request go through the same ARM TX gate as a CAN send.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from canlab.theme import mono_font

log = logging.getLogger(__name__)


class _DoIPWorker(QThread):
    """Runs one blocking DoIP exchange off the GUI thread."""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:
            self.failed.emit(str(e))

    def stop(self):
        self.requestInterruption()
        self.quit()
        self.wait(3000)


class DoIPPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lay.addWidget(QLabel(
            "Diagnostics over IP (ISO 13400). Discovery broadcasts a vehicle "
            "identification request; requests need ARM TX like any transmit.",
            font=mono_font(8), wordWrap=True))

        disc = QGroupBox("DISCOVERY")
        disc_lay = QHBoxLayout(disc)
        self.btn_discover = QPushButton("Discover Entities")
        self.btn_discover.setObjectName("btn_green")
        self.btn_discover.clicked.connect(self._discover)
        disc_lay.addWidget(self.btn_discover)
        disc_lay.addStretch()
        lay.addWidget(disc)

        self.entity_table = QTableWidget(0, 4)
        self.entity_table.setHorizontalHeaderLabels(["Address", "VIN", "EID", "Logical"])
        self.entity_table.setFont(mono_font(8))
        self.entity_table.verticalHeader().setVisible(False)
        self.entity_table.verticalHeader().setDefaultSectionSize(20)
        self.entity_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.entity_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.entity_table.setMaximumHeight(120)
        self.entity_table.itemSelectionChanged.connect(self._use_selected_entity)
        lay.addWidget(self.entity_table)

        target = QGroupBox("TARGET")
        t_lay = QHBoxLayout(target)
        t_lay.addWidget(QLabel("Host:", font=mono_font(8)))
        self.host_edit = QLineEdit("192.168.0.10")
        self.host_edit.setFont(mono_font())
        t_lay.addWidget(self.host_edit)
        t_lay.addWidget(QLabel("Port:", font=mono_font(8)))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(13400)
        t_lay.addWidget(self.port_spin)
        t_lay.addWidget(QLabel("Logical addr:", font=mono_font(8)))
        self.addr_edit = QLineEdit("0x0E80")
        self.addr_edit.setFixedWidth(90)
        self.addr_edit.setFont(mono_font())
        t_lay.addWidget(self.addr_edit)
        lay.addWidget(target)

        req = QGroupBox("UDS REQUEST")
        r_lay = QHBoxLayout(req)
        r_lay.addWidget(QLabel("Bytes (hex):", font=mono_font(8)))
        self.req_edit = QLineEdit("22 F1 90")
        self.req_edit.setFont(mono_font())
        r_lay.addWidget(self.req_edit)
        self.btn_send = QPushButton("Send")
        self.btn_send.clicked.connect(self._send_request)
        r_lay.addWidget(self.btn_send)
        lay.addWidget(req)

        self.doip_log = QTextEdit()
        self.doip_log.setReadOnly(True)
        self.doip_log.setFont(mono_font(8))
        lay.addWidget(self.doip_log)

    # ── actions ───────────────────────────────────────────────────────────
    def _run(self, fn, on_done):
        if self._worker is not None and self._worker.isRunning():
            self.doip_log.append("A DoIP request is already running.")
            return
        self._worker = _DoIPWorker(fn, self)
        self._worker.done.connect(on_done)
        self._worker.failed.connect(lambda e: self.doip_log.append(f"ERROR: {e}"))
        self._worker.start()

    def _discover(self):
        from canlab.core.doip import discover
        self.doip_log.append("Discovering DoIP entities…")
        self._run(lambda: discover(timeout=2.0), self._on_entities)

    def _on_entities(self, entities: list):
        self.entity_table.setRowCount(0)
        for ent in entities or []:
            row = self.entity_table.rowCount()
            self.entity_table.insertRow(row)
            cells = [str(ent.get("address", "")), str(ent.get("vin", "")),
                     str(ent.get("eid", "")), f"0x{ent.get('logical_address', 0):04X}"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFont(mono_font(8))
                self.entity_table.setItem(row, col, item)
        self.doip_log.append(f"{len(entities or [])} entity(ies) found.")

    def _use_selected_entity(self):
        row = self.entity_table.currentRow()
        if row < 0:
            return
        addr = self.entity_table.item(row, 0)
        logical = self.entity_table.item(row, 3)
        if addr is not None:
            self.host_edit.setText(addr.text())
        if logical is not None:
            self.addr_edit.setText(logical.text())

    def _send_request(self):
        from canlab.core.doip import DoIPClient
        try:
            target = int(self.addr_edit.text(), 16)
            payload = bytes(int(b, 16) for b in self.req_edit.text().split())
        except ValueError:
            self.doip_log.append("ERROR: address and request must be hex bytes.")
            return
        if not payload:
            self.doip_log.append("ERROR: empty request.")
            return
        host, port = self.host_edit.text().strip(), self.port_spin.value()

        def _exchange():
            with DoIPClient(host, target, port=port) as client:
                return client.request(payload)

        self.doip_log.append(f"→ {payload.hex(' ').upper()}  ({host}:{port})")
        self._run(_exchange,
                  lambda resp: self.doip_log.append(f"← {bytes(resp).hex(' ').upper()}"))

    def cleanup(self):
        if self._worker is not None:
            try:
                self._worker.stop()
            except Exception:
                log.debug("DoIP worker stop failed", exc_info=True)
            self._worker = None
