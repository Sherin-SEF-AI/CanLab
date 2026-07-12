"""OBD-II Live Gauge Dashboard tab."""
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QSpinBox, QGroupBox, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont

from theme import COLORS, mono_font
from core.state import get_state
from core.obd2_pids import PID_TABLE, DEFAULT_PIDS


class _GaugeWidget(QWidget):
    """Half-arc gauge replicating SpeedGaugeWidget style (avoid cross-tab import)."""
    def __init__(self, pid: int, parent=None):
        super().__init__(parent)
        self._pid   = pid
        meta        = PID_TABLE.get(pid, {})
        self._name  = meta.get("name", f"PID 0x{pid:02X}")
        self._unit  = meta.get("unit", "")
        self._max   = meta.get("max", 100)
        self._min   = meta.get("min", 0)
        self._value = self._min
        self.setMinimumSize(160, 110)

    def set_value(self, v: float):
        self._value = max(self._min, min(v, self._max))
        self.update()

    def paintEvent(self, _event):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h  = self.width(), self.height()
        cx    = w // 2
        cy    = int(h * 0.80)
        r     = min(w // 2 - 10, h - 25)

        # background arc
        p.setPen(QPen(QColor(COLORS["border"]), 6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(cx - r, cy - r, 2 * r, 2 * r, 0, 180 * 16)

        # value arc
        span = max(0.0, (self._value - self._min) / max(1, self._max - self._min)) * 180
        p.setPen(QPen(QColor(COLORS["green"]), 6))
        p.drawArc(cx - r, cy - r, 2 * r, 2 * r, 0, int(span * 16))

        # value text
        p.setPen(QColor(COLORS["text"]))
        p.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        val_str = f"{self._value:.1f}"
        p.drawText(cx - 35, cy - 8, val_str)

        # unit + name
        p.setFont(QFont("Courier New", 7))
        p.setPen(QColor(COLORS["dim"]))
        p.drawText(cx - 20, cy + 6, self._unit)
        p.drawText(4, 14, self._name)
        p.end()


class OBDDashboardTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state   = get_state()
        self._poller  = None
        self._gauges: dict[int, _GaugeWidget] = {}
        self._build_ui()
        self._state.can_connected.connect(self._on_can_status)
        self._state.pid_value_updated.connect(self._on_pid_value)

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        # Left — controls
        left = QWidget()
        left.setFixedWidth(200)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.setSpacing(6)

        ll.addWidget(QLabel("OBD-II PIDs", font=mono_font(9)))

        self.lbl_can = QLabel("CAN: disconnected")
        self.lbl_can.setFont(mono_font(8))
        self.lbl_can.setStyleSheet(f"color:{COLORS['dim']}")
        ll.addWidget(self.lbl_can)

        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("Rate (ms):", font=mono_font(8)))
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(50, 2000)
        self.rate_spin.setSingleStep(50)
        self.rate_spin.setValue(200)
        self.rate_spin.setFont(mono_font(8))
        rate_row.addWidget(self.rate_spin)
        ll.addLayout(rate_row)

        ll.addWidget(QLabel("Select PIDs:", font=mono_font(8)))
        self.pid_list = QListWidget()
        self.pid_list.setFont(mono_font(8))
        self.pid_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for pid, meta in sorted(PID_TABLE.items()):
            item = QListWidgetItem(f"0x{pid:02X}  {meta['name']}")
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self.pid_list.addItem(item)
            if pid in DEFAULT_PIDS:
                item.setSelected(True)
        ll.addWidget(self.pid_list)

        self.btn_discover = QPushButton("Auto-discover…")
        self.btn_discover.setFont(mono_font(8))
        self.btn_discover.clicked.connect(self._discover_pids)
        ll.addWidget(self.btn_discover)

        self.btn_start = QPushButton("START")
        self.btn_start.setObjectName("btn_green")
        self.btn_start.setFont(mono_font(9))
        self.btn_start.clicked.connect(self._start_polling)
        ll.addWidget(self.btn_start)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setFont(mono_font(9))
        self.btn_stop.clicked.connect(self._stop_polling)
        self.btn_stop.setEnabled(False)
        ll.addWidget(self.btn_stop)

        ll.addStretch()
        root.addWidget(left)

        # Right — gauge grid
        self.gauge_area = QWidget()
        self.gauge_grid = QGridLayout(self.gauge_area)
        self.gauge_grid.setSpacing(8)
        root.addWidget(self.gauge_area, stretch=1)

    # ── Polling ─────────────────────────────────────────────────────────────────

    def _selected_pids(self) -> list[int]:
        return [item.data(Qt.ItemDataRole.UserRole)
                for item in self.pid_list.selectedItems()]

    def _start_polling(self):
        bus = self._state.can_bus
        if bus is None:
            QMessageBox.information(self, "No Bus", "Connect CAN bus first.")
            return
        pids = self._selected_pids()
        if not pids:
            QMessageBox.information(self, "No PIDs", "Select at least one PID.")
            return

        self._stop_polling()
        self._rebuild_gauges(pids)

        from core.obd2_poller import OBD2Poller
        self._poller = OBD2Poller(bus=bus, pids=pids,
                                  interval_ms=self.rate_spin.value())
        self._poller.pid_value.connect(self._state.pid_value_updated)
        self._poller.error.connect(lambda e: None)  # silent; individual PID errors non-critical
        self._poller.start()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def _stop_polling(self):
        if self._poller:
            self._poller.stop()
            self._poller = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _discover_pids(self):
        bus = self._state.can_bus
        if bus is None:
            QMessageBox.information(self, "No Bus", "Connect CAN bus first.")
            return
        from core.obd2_poller import OBD2Poller
        # Store on self: a local QThread is garbage-collected when this method
        # returns, crashing with "QThread: Destroyed while thread is running".
        self._discover_worker = OBD2Poller(bus=bus, pids=[], discover_only=True)
        self._discover_worker.pids_discovered.connect(self._on_pids_discovered)
        self._discover_worker.error.connect(lambda e: QMessageBox.warning(self, "Discover Error", e))
        self._discover_worker.start()
        self.btn_discover.setText("Discovering…")
        self.btn_discover.setEnabled(False)
        self._discover_worker.finished.connect(lambda: (
            self.btn_discover.setText("Auto-discover…"),
            self.btn_discover.setEnabled(True),
        ))

    def _on_pids_discovered(self, pids: list):
        for i in range(self.pid_list.count()):
            item = self.pid_list.item(i)
            pid = item.data(Qt.ItemDataRole.UserRole)
            item.setSelected(pid in pids)

    def _rebuild_gauges(self, pids: list[int]):
        # Clear old gauges
        for g in self._gauges.values():
            self.gauge_grid.removeWidget(g)
            g.deleteLater()
        self._gauges.clear()

        cols = 3
        for i, pid in enumerate(pids):
            g = _GaugeWidget(pid)
            self._gauges[pid] = g
            self.gauge_grid.addWidget(g, i // cols, i % cols)

    def _on_pid_value(self, pid: int, value: float, _unit: str):
        if pid in self._gauges:
            self._gauges[pid].set_value(value)

    def _on_can_status(self, connected: bool):
        if connected:
            self.lbl_can.setText("CAN: connected")
            self.lbl_can.setStyleSheet(f"color:{COLORS['green']}")
        else:
            self.lbl_can.setText("CAN: disconnected")
            self.lbl_can.setStyleSheet(f"color:{COLORS['dim']}")
            self._stop_polling()
