"""XCP-over-CAN panel: connect, read memory, poll measurements (read-only).

The XCP client deliberately implements no write/programming commands, so this
panel only ever reads. Every frame it sends still goes through the ARM TX gate
like any other transmit.
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from canlab.core.state import get_state
from canlab.theme import mono_font

log = logging.getLogger(__name__)


def _hex_field(text: str, width: int = 90) -> QLineEdit:
    e = QLineEdit(text)
    e.setFixedWidth(width)
    e.setFont(mono_font())
    return e


class XCPPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = get_state()
        self._worker = None
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lay.addWidget(QLabel(
            "XCP over CAN (ASAM MCD-1). Read-only: CONNECT, UPLOAD and "
            "SHORT_UPLOAD only — no memory writes or programming commands.",
            font=mono_font(8), wordWrap=True))

        ids = QGroupBox("SLAVE")
        ids_lay = QHBoxLayout(ids)
        ids_lay.addWidget(QLabel("CRO ID:", font=mono_font(8)))
        self.cro_edit = _hex_field("0x7E0")
        ids_lay.addWidget(self.cro_edit)
        ids_lay.addWidget(QLabel("DTO ID:", font=mono_font(8)))
        self.dto_edit = _hex_field("0x7E8")
        ids_lay.addWidget(self.dto_edit)
        self.chk_ext = QCheckBox("29-bit IDs")
        ids_lay.addWidget(self.chk_ext)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setObjectName("btn_green")
        self.btn_connect.clicked.connect(self._connect)
        ids_lay.addWidget(self.btn_connect)
        ids_lay.addStretch()
        lay.addWidget(ids)

        read = QGroupBox("READ MEMORY")
        read_lay = QHBoxLayout(read)
        read_lay.addWidget(QLabel("Address:", font=mono_font(8)))
        self.addr_edit = _hex_field("0x40000000", 120)
        read_lay.addWidget(self.addr_edit)
        read_lay.addWidget(QLabel("Bytes:", font=mono_font(8)))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(1, 255)
        self.size_spin.setValue(4)
        read_lay.addWidget(self.size_spin)
        self.btn_read = QPushButton("Read")
        self.btn_read.clicked.connect(self._read_once)
        read_lay.addWidget(self.btn_read)
        read_lay.addStretch()
        lay.addWidget(read)

        poll = QGroupBox("MEASUREMENTS")
        poll_lay = QVBoxLayout(poll)
        row = QHBoxLayout()
        row.addWidget(QLabel("Name:", font=mono_font(8)))
        self.meas_name = _hex_field("rpm", 110)
        row.addWidget(self.meas_name)
        row.addWidget(QLabel("Address:", font=mono_font(8)))
        self.meas_addr = _hex_field("0x40000000", 120)
        row.addWidget(self.meas_addr)
        row.addWidget(QLabel("Size:", font=mono_font(8)))
        self.meas_size = QSpinBox()
        self.meas_size.setRange(1, 8)
        self.meas_size.setValue(2)
        row.addWidget(self.meas_size)
        btn_add = QPushButton("Add")
        btn_add.clicked.connect(self._add_measurement)
        row.addWidget(btn_add)
        row.addStretch()
        poll_lay.addLayout(row)

        self.meas_table = QTableWidget(0, 4)
        self.meas_table.setHorizontalHeaderLabels(["Name", "Address", "Size", "Value"])
        self.meas_table.setFont(mono_font())
        self.meas_table.verticalHeader().setVisible(False)
        self.meas_table.verticalHeader().setDefaultSectionSize(20)
        self.meas_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.meas_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.meas_table.setMaximumHeight(160)
        poll_lay.addWidget(self.meas_table)

        btn_row = QHBoxLayout()
        self.btn_poll = QPushButton("Start Polling")
        self.btn_poll.clicked.connect(self._toggle_poll)
        btn_row.addWidget(self.btn_poll)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(lambda: self.meas_table.setRowCount(0))
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()
        poll_lay.addLayout(btn_row)
        lay.addWidget(poll)

        self.xcp_log = QTextEdit()
        self.xcp_log.setReadOnly(True)
        self.xcp_log.setFont(mono_font(8))
        lay.addWidget(self.xcp_log)

    # ── helpers ───────────────────────────────────────────────────────────
    def _ids(self) -> tuple[int, int]:
        return (int(self.cro_edit.text(), 16), int(self.dto_edit.text(), 16))

    def _bus(self):
        """A subscription filtered to this slave's DTO id."""
        try:
            _cro, dto = self._ids()
        except ValueError:
            self.xcp_log.append("ERROR: CRO/DTO must be hex, e.g. 0x7E0")
            return None
        bus = self._state.bus_view(self, {dto})
        if bus is None:
            self.xcp_log.append("ERROR: connect a CAN bus first.")
        return bus

    def _client(self):
        from canlab.core.xcp import XCPClient
        bus = self._bus()
        if bus is None:
            return None
        cro, dto = self._ids()
        return XCPClient(bus, cro_id=cro, dto_id=dto,
                         extended_id=self.chk_ext.isChecked())

    # ── actions ───────────────────────────────────────────────────────────
    def _connect(self):
        client = self._client()
        if client is None:
            return
        try:
            info = client.connect()
            self.xcp_log.append(
                f"Connected: byte_order={info['byte_order']} "
                f"MAX_CTO={info['max_cto']} MAX_DTO={info['max_dto']}")
            client.disconnect()
        except Exception as e:
            self.xcp_log.append(f"ERROR: {e}")

    def _read_once(self):
        client = self._client()
        if client is None:
            return
        try:
            addr = int(self.addr_edit.text(), 16)
        except ValueError:
            self.xcp_log.append("ERROR: address must be hex, e.g. 0x40000000")
            return
        try:
            client.connect()
            data = client.read_memory(addr, self.size_spin.value())
            self.xcp_log.append(f"0x{addr:08X}: {data.hex(' ').upper()}")
            client.disconnect()
        except Exception as e:
            self.xcp_log.append(f"ERROR: {e}")

    def _add_measurement(self):
        try:
            addr = int(self.meas_addr.text(), 16)
        except ValueError:
            self.xcp_log.append("ERROR: measurement address must be hex")
            return
        row = self.meas_table.rowCount()
        self.meas_table.insertRow(row)
        for col, text in enumerate([self.meas_name.text(), f"0x{addr:08X}",
                                    str(self.meas_size.value()), "—"]):
            item = QTableWidgetItem(text)
            item.setFont(mono_font())
            self.meas_table.setItem(row, col, item)

    def _measurements(self) -> list[tuple[str, int, int]]:
        out = []
        for row in range(self.meas_table.rowCount()):
            try:
                out.append((self.meas_table.item(row, 0).text(),
                            int(self.meas_table.item(row, 1).text(), 16),
                            int(self.meas_table.item(row, 2).text())))
            except (AttributeError, ValueError):
                continue
        return out

    def _toggle_poll(self):
        if self._worker is not None:
            self.cleanup()
            self.btn_poll.setText("Start Polling")
            return
        measurements = self._measurements()
        if not measurements:
            self.xcp_log.append("Add at least one measurement first.")
            return
        bus = self._bus()
        if bus is None:
            return
        from canlab.core.xcp import XCPPollWorker
        cro, dto = self._ids()
        self._worker = XCPPollWorker(bus, cro, dto, measurements, interval_ms=200)
        self._worker.sample.connect(self._on_sample)
        self._worker.status.connect(self.xcp_log.append)
        self._worker.error.connect(lambda e: self.xcp_log.append(f"ERROR: {e}"))
        self._worker.finished.connect(lambda: self.btn_poll.setText("Start Polling"))
        self._worker.start()
        self.btn_poll.setText("Stop Polling")

    def _on_sample(self, sweep: dict):
        for row in range(self.meas_table.rowCount()):
            name_item = self.meas_table.item(row, 0)
            if name_item is None or name_item.text() not in sweep:
                continue
            item = QTableWidgetItem(str(sweep[name_item.text()]))
            item.setFont(mono_font())
            self.meas_table.setItem(row, 3, item)

    def cleanup(self):
        if self._worker is not None:
            try:
                self._worker.stop()
            except Exception:
                log.debug("XCP worker stop failed", exc_info=True)
            self._worker = None
