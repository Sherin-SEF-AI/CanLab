"""DIAGNOSTICS tab — UDS/OBD-II scanner, DTC reader, bus-load gauge, bus health."""
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QProgressBar,
    QTextEdit, QTabWidget, QComboBox, QSpinBox, QLineEdit, QFileDialog,
    QDoubleSpinBox, QCheckBox, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QBrush

from theme import COLORS, mono_font
from core.state import get_state


class DiagnosticsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state        = get_state()
        self._uds_worker   = None
        self._health_meter = None
        self._health_timer = QTimer()
        self._health_timer.setInterval(1000)
        self._health_timer.timeout.connect(self._health_tick)
        self._build_ui()
        self._state.can_connected.connect(self._on_can_status)
        self._state.bus_load_update.connect(self._on_bus_load)

    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.addTab(self._build_obd_tab(),     "OBD-II / UDS")
        tabs.addTab(self._build_deep_tab(),   "UDS DEEP SCAN")
        tabs.addTab(self._build_svc_tab(),    "UDS SERVICES")
        tabs.addTab(self._build_secacc_tab(), "SECURITY ACCESS")
        tabs.addTab(self._build_load_tab(),   "BUS LOAD")
        tabs.addTab(self._build_health_tab(), "BUS HEALTH")
        outer.addWidget(tabs)

    # ── OBD-II tab ────────────────────────────────────────────────────────────

    def _build_obd_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        self.lbl_diag_status = QLabel("CAN: disconnected")
        self.lbl_diag_status.setFont(mono_font(8))
        self.lbl_diag_status.setStyleSheet(f"color:{COLORS['error']}")
        lay.addWidget(self.lbl_diag_status)

        # Controls
        ctl = QHBoxLayout()
        self.btn_scan_pids = QPushButton("Scan OBD-II PIDs")
        self.btn_scan_pids.setObjectName("btn_green")
        self.btn_scan_pids.clicked.connect(self._scan_pids)
        self.btn_read_dtc  = QPushButton("Read DTCs (19 02)")
        self.btn_read_dtc.clicked.connect(self._read_dtc)
        self.btn_clear_dtc = QPushButton("Clear DTCs (14)")
        self.btn_clear_dtc.clicked.connect(self._clear_dtc)
        for b in [self.btn_scan_pids, self.btn_read_dtc, self.btn_clear_dtc]:
            ctl.addWidget(b)
        ctl.addStretch()
        lay.addLayout(ctl)

        # PID table
        lay.addWidget(QLabel("OBD-II LIVE DATA", font=mono_font(8)))
        self.pid_table = QTableWidget(0, 4)
        self.pid_table.setHorizontalHeaderLabels(["PID", "Name", "Value", "Unit"])
        self.pid_table.setFont(mono_font())
        self.pid_table.verticalHeader().setVisible(False)
        self.pid_table.verticalHeader().setDefaultSectionSize(20)
        self.pid_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pid_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.pid_table)

        # DTC output
        lay.addWidget(QLabel("DTCs", font=mono_font(8)))
        self.dtc_text = QTextEdit()
        self.dtc_text.setReadOnly(True)
        self.dtc_text.setMaximumHeight(80)
        self.dtc_text.setFont(mono_font())
        lay.addWidget(self.dtc_text)

        # UDS raw
        lay.addWidget(QLabel("UDS RAW RESPONSE LOG", font=mono_font(8)))
        self.uds_log = QTextEdit()
        self.uds_log.setReadOnly(True)
        self.uds_log.setFont(mono_font(8))
        lay.addWidget(self.uds_log)
        self._state.uds_response.connect(self._on_uds_response)
        return w

    # ── Security Access tab ───────────────────────────────────────────────────

    def _build_secacc_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lay.addWidget(QLabel(
            "UDS Security Access (service 0x27) — seed-key brute-force, "
            "built-in algorithms, and pluggable Python scripts.",
            font=mono_font(8),
        ))

        # ── Config ────────────────────────────────────────────────────────────
        cfg_grp = QGroupBox("CONFIGURATION")
        cg = QHBoxLayout(cfg_grp)

        cg.addWidget(QLabel("ECU addr:", font=mono_font(8)))
        self.sa_ecu_combo = QComboBox()
        self.sa_ecu_combo.setFont(mono_font(8))
        self.sa_ecu_combo.setEditable(True)
        for addr in [f"0x{a:03X}" for a in range(0x7E0, 0x7E8)]:
            self.sa_ecu_combo.addItem(addr)
        cg.addWidget(self.sa_ecu_combo)

        cg.addWidget(QLabel("Session:", font=mono_font(8)))
        self.sa_session_combo = QComboBox()
        self.sa_session_combo.setFont(mono_font(8))
        self.sa_session_combo.addItem("Extended (0x03)", 0x03)
        self.sa_session_combo.addItem("Programming (0x02)", 0x02)
        self.sa_session_combo.addItem("Default (0x01)", 0x01)
        cg.addWidget(self.sa_session_combo)

        cg.addWidget(QLabel("Access level:", font=mono_font(8)))
        self.sa_level_spin = QSpinBox()
        self.sa_level_spin.setRange(1, 0x7F)
        self.sa_level_spin.setSingleStep(2)
        self.sa_level_spin.setValue(1)
        self.sa_level_spin.setFont(mono_font(8))
        cg.addWidget(self.sa_level_spin)

        cg.addWidget(QLabel("Mode:", font=mono_font(8)))
        self.sa_mode_combo = QComboBox()
        self.sa_mode_combo.setFont(mono_font(8))
        self.sa_mode_combo.addItems(["AUTO", "SCRIPT", "BRUTEFORCE"])
        self.sa_mode_combo.currentTextChanged.connect(self._sa_on_mode_changed)
        cg.addWidget(self.sa_mode_combo)

        lay.addWidget(cfg_grp)

        # ── Script / expression / brute-force options ─────────────────────────
        opt_grp = QGroupBox("ALGORITHM OPTIONS")
        og = QVBoxLayout(opt_grp)

        script_row = QHBoxLayout()
        script_row.addWidget(QLabel("Script (.py):", font=mono_font(8)))
        self.sa_script_edit = QLineEdit()
        self.sa_script_edit.setFont(mono_font(8))
        self.sa_script_edit.setPlaceholderText("path/to/compute_key.py  (define compute_key(seed, level) -> bytes)")
        script_row.addWidget(self.sa_script_edit, 1)
        btn_browse = QPushButton("Browse…")
        btn_browse.setFont(mono_font(8))
        btn_browse.clicked.connect(self._sa_browse_script)
        script_row.addWidget(btn_browse)
        og.addLayout(script_row)

        expr_row = QHBoxLayout()
        expr_row.addWidget(QLabel("Custom expression:", font=mono_font(8)))
        self.sa_expr_edit = QLineEdit()
        self.sa_expr_edit.setFont(mono_font(8))
        self.sa_expr_edit.setPlaceholderText("e.g.  _to_bytes(_to_int(seed) ^ 0xDEAD, len(seed))")
        expr_row.addWidget(self.sa_expr_edit, 1)
        og.addLayout(expr_row)

        bf_row = QHBoxLayout()
        bf_row.addWidget(QLabel("BF key length (bytes):", font=mono_font(8)))
        self.sa_bf_len_spin = QSpinBox()
        self.sa_bf_len_spin.setRange(1, 2)
        self.sa_bf_len_spin.setValue(2)
        self.sa_bf_len_spin.setFont(mono_font(8))
        bf_row.addWidget(self.sa_bf_len_spin)
        bf_row.addWidget(QLabel("BF delay (ms):", font=mono_font(8)))
        self.sa_bf_delay_spin = QSpinBox()
        self.sa_bf_delay_spin.setRange(10, 5000)
        self.sa_bf_delay_spin.setValue(50)
        self.sa_bf_delay_spin.setFont(mono_font(8))
        bf_row.addWidget(self.sa_bf_delay_spin)
        bf_row.addStretch()
        og.addLayout(bf_row)
        lay.addWidget(opt_grp)

        # ── Brute-force progress ──────────────────────────────────────────────
        self.sa_bf_progress = QProgressBar()
        self.sa_bf_progress.setValue(0)
        self.sa_bf_progress.setVisible(False)
        lay.addWidget(self.sa_bf_progress)

        # ── Controls ──────────────────────────────────────────────────────────
        ctl = QHBoxLayout()
        self.sa_btn_start = QPushButton("▶  Start")
        self.sa_btn_start.setObjectName("btn_green")
        self.sa_btn_start.clicked.connect(self._sa_start)
        self.sa_btn_stop = QPushButton("■  Stop")
        self.sa_btn_stop.clicked.connect(self._sa_stop)
        self.sa_btn_stop.setEnabled(False)
        self.sa_btn_history = QPushButton("Session History…")
        self.sa_btn_history.clicked.connect(self._sa_show_history)
        for b in [self.sa_btn_start, self.sa_btn_stop, self.sa_btn_history]:
            ctl.addWidget(b)
        ctl.addStretch()
        lay.addLayout(ctl)

        # ── Result banner ─────────────────────────────────────────────────────
        self.sa_lbl_result = QLabel("")
        self.sa_lbl_result.setFont(mono_font(10))
        lay.addWidget(self.sa_lbl_result)

        # ── Log ───────────────────────────────────────────────────────────────
        lay.addWidget(QLabel("LOG", font=mono_font(8)))
        self.sa_log = QTextEdit()
        self.sa_log.setReadOnly(True)
        self.sa_log.setFont(mono_font(8))
        lay.addWidget(self.sa_log)

        self._sa_worker = None
        return w

    def _sa_on_mode_changed(self, mode: str):
        self.sa_bf_progress.setVisible(mode == "BRUTEFORCE")

    def _sa_browse_script(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select seed-key script", "", "Python (*.py);;All (*)"
        )
        if path:
            self.sa_script_edit.setText(path)

    def _sa_start(self):
        bus = self._state.can_bus
        if bus is None:
            self.sa_log.append("ERROR: CAN bus not connected.")
            return
        try:
            ecu_text = self.sa_ecu_combo.currentText().strip()
            ecu_addr = int(ecu_text, 16)
        except ValueError:
            self.sa_log.append("ERROR: invalid ECU address.")
            return

        from core.security_access import SecurityAccessWorker
        self._sa_worker = SecurityAccessWorker(
            bus          = bus,
            ecu_addr     = ecu_addr,
            session_type = self.sa_session_combo.currentData(),
            access_level = self.sa_level_spin.value(),
            mode         = self.sa_mode_combo.currentText(),
            script_path  = self.sa_script_edit.text().strip(),
            custom_expr  = self.sa_expr_edit.text().strip(),
            bf_key_len   = self.sa_bf_len_spin.value(),
            bf_delay_ms  = self.sa_bf_delay_spin.value(),
        )
        self._sa_worker.step_done.connect(lambda s: self.sa_log.append(s))
        self._sa_worker.seed_received.connect(self._sa_on_seed)
        self._sa_worker.key_accepted.connect(self._sa_on_accepted)
        self._sa_worker.key_rejected.connect(self._sa_on_rejected)
        self._sa_worker.lockout_detected.connect(self._sa_on_lockout)
        self._sa_worker.bruteforce_progress.connect(self._sa_on_bf_progress)
        self._sa_worker.error.connect(lambda e: self.sa_log.append(f"ERROR: {e}"))
        self._sa_worker.finished.connect(self._sa_finished)

        self.sa_lbl_result.setText("")
        self.sa_log.clear()
        self.sa_bf_progress.setValue(0)
        self.sa_btn_start.setEnabled(False)
        self.sa_btn_stop.setEnabled(True)
        self._sa_worker.start()

    def _sa_stop(self):
        if self._sa_worker:
            self._sa_worker.stop()
            self._sa_worker = None
        self.sa_btn_start.setEnabled(True)
        self.sa_btn_stop.setEnabled(False)

    def _sa_finished(self):
        self.sa_btn_start.setEnabled(True)
        self.sa_btn_stop.setEnabled(False)

    def _sa_on_seed(self, ecu: int, level: int, seed: bytes):
        amber = COLORS["amber"]
        self.sa_log.append(
            f"<b>Seed received</b>  ECU=0x{ecu:03X}  level={level}  "
            f"seed=<span style='color:{amber}'>{seed.hex().upper()}</span>"
        )

    def _sa_on_accepted(self, ecu: int, level: int, seed: bytes, key: bytes, algo: str):
        c = COLORS["green"]
        self.sa_lbl_result.setText(
            f"✓  KEY ACCEPTED   seed={seed.hex().upper()}   "
            f"key={key.hex().upper()}   algo={algo}"
        )
        self.sa_lbl_result.setStyleSheet(f"color:{c}; font-weight:bold;")
        self.sa_log.append(
            f"<span style='color:{c}'><b>✓ KEY ACCEPTED</b>  "
            f"algo={algo}  key={key.hex().upper()}</span>"
        )

    def _sa_on_rejected(self, ecu: int, level: int, nrc: int, desc: str):
        c = COLORS["dim"]
        self.sa_log.append(
            f"<span style='color:{c}'>✗ key rejected  NRC=0x{nrc:02X} ({desc})</span>"
        )

    def _sa_on_lockout(self, ecu: int):
        c = COLORS["error"]
        self.sa_lbl_result.setText(f"⚠  LOCKOUT DETECTED on 0x{ecu:03X} — stop injecting")
        self.sa_lbl_result.setStyleSheet(f"color:{c}; font-weight:bold;")
        self.sa_log.append(f"<span style='color:{c}'><b>LOCKOUT 0x{ecu:03X}</b></span>")

    def _sa_on_bf_progress(self, current: int, total: int):
        self.sa_bf_progress.setMaximum(total)
        self.sa_bf_progress.setValue(current)

    def _sa_show_history(self):
        from core.security_access import load_history
        history = load_history()
        if not history:
            self.sa_log.append("No session history yet.")
            return
        self.sa_log.append(f"=== Session history ({len(history)} entries) ===")
        for e in history[-20:]:
            ts    = e.get("timestamp", "?")[:19]
            ecu   = e.get("ecu_addr", "?")
            level = e.get("level", "?")
            algo  = e.get("algorithm", "?")
            seed  = e.get("seed_hex", "?")
            key   = e.get("key_hex", "?")
            self.sa_log.append(
                f"  {ts}  {ecu}  lvl={level}  {algo}  "
                f"seed={seed}  key={key}"
            )

    # ── Bus Load tab ──────────────────────────────────────────────────────────

    def _build_load_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        lay.addWidget(QLabel("BUS LOAD  (% of 500kbps capacity)", font=mono_font(9)))

        self.load_bar = QProgressBar()
        self.load_bar.setRange(0, 100)
        self.load_bar.setValue(0)
        self.load_bar.setTextVisible(True)
        self.load_bar.setFormat("%p%")
        lay.addWidget(self.load_bar)

        self.lbl_load_detail = QLabel("0 fps  |  —% utilization")
        self.lbl_load_detail.setFont(mono_font(9))
        lay.addWidget(self.lbl_load_detail)

        # History plot
        self.load_plot = __import__("pyqtgraph").PlotWidget()
        self.load_plot.setBackground(COLORS["bg"])
        self.load_plot.setLabel("left",   "Load %")
        self.load_plot.setLabel("bottom", "Seconds ago")
        self.load_plot.setYRange(0, 100)
        self._load_history = []
        self._load_curve   = self.load_plot.plot(pen=COLORS["green"])
        lay.addWidget(self.load_plot)
        return w

    # ── Bus-load handler ──────────────────────────────────────────────────────

    def _on_bus_load(self, load: float):
        pct = int(load * 100)
        self.load_bar.setValue(pct)
        self._load_history.append(pct)
        if len(self._load_history) > 120:
            self._load_history.pop(0)
        n = len(self._load_history)
        x = list(range(-n + 1, 1))
        self._load_curve.setData(x, self._load_history)
        fps = len(self._state.frames_df)
        self.lbl_load_detail.setText(f"{fps} total frames  |  {pct}% utilization")

    # ── CAN status ────────────────────────────────────────────────────────────

    def _on_can_status(self, connected: bool):
        if connected:
            self.lbl_diag_status.setText("CAN: connected")
            self.lbl_diag_status.setStyleSheet(f"color:{COLORS['green']}")
        else:
            self.lbl_diag_status.setText("CAN: disconnected")
            self.lbl_diag_status.setStyleSheet(f"color:{COLORS['error']}")

    # ── UDS / OBD actions ─────────────────────────────────────────────────────

    def _get_bus(self):
        return self._state.can_bus

    def _scan_pids(self):
        bus = self._get_bus()
        if bus is None:
            self.uds_log.append("ERROR: CAN bus not connected.")
            return
        from core.uds import UDSScanner
        self._uds_worker = UDSScanner(bus, mode="PID")
        self._uds_worker.pid_result.connect(self._on_pid_result)
        self._uds_worker.status.connect(lambda s: self.uds_log.append(s))
        self._uds_worker.finished.connect(lambda: self.uds_log.append("Scan complete."))
        self._uds_worker.error.connect(lambda e: self.uds_log.append(f"ERROR: {e}"))
        self.pid_table.setRowCount(0)
        self._uds_worker.start()

    def _on_pid_result(self, pid: int, name: str, value: float, unit: str):
        row = self.pid_table.rowCount()
        self.pid_table.insertRow(row)
        cells = [f"0x{pid:02X}", name, str(value), unit]
        for ci, txt in enumerate(cells):
            item = QTableWidgetItem(txt)
            item.setFont(mono_font())
            if ci == 2:
                item.setForeground(QBrush(QColor(COLORS["green"])))
            self.pid_table.setItem(row, ci, item)

    def _read_dtc(self):
        bus = self._get_bus()
        if bus is None:
            self.uds_log.append("ERROR: CAN bus not connected.")
            return
        from core.uds import UDSScanner
        # Store on self: a local QThread is garbage-collected the moment this
        # method returns, aborting with "QThread: Destroyed while thread is
        # still running".
        self._dtc_worker = UDSScanner(bus, mode="DTC")
        self._dtc_worker.dtc_result.connect(self._on_dtc_result)
        self._dtc_worker.status.connect(lambda s: self.uds_log.append(s))
        self._dtc_worker.error.connect(lambda e: self.uds_log.append(f"ERROR: {e}"))
        self._dtc_worker.start()

    def _on_dtc_result(self, dtcs: list):
        if not dtcs:
            self.dtc_text.setPlainText("No DTCs found.")
        else:
            self.dtc_text.setPlainText("  ".join(dtcs))
        self.uds_log.append(f"DTCs: {dtcs}")

    def _clear_dtc(self):
        import can
        bus = self._get_bus()
        if bus is None:
            self.uds_log.append("ERROR: CAN bus not connected.")
            return
        try:
            data = bytes([0x04, 0x14, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00])
            msg  = can.Message(arbitration_id=0x7DF, data=data, is_extended_id=False)
            bus.send(msg)
            self.uds_log.append("Sent: Clear DTC (14 FF FF FF)")
            self.dtc_text.setPlainText("Cleared.")
        except Exception as e:
            self.uds_log.append(f"ERROR: {e}")

    def _on_uds_response(self, arb_id: int, data: bytes):
        hex_data = " ".join(f"{b:02X}" for b in data)
        self.uds_log.append(f"RX 0x{arb_id:03X}: {hex_data}")

    # ── UDS Deep Scan tab ─────────────────────────────────────────────────────

    def _build_deep_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lbl = QLabel("Probes ECUs 0x7E0–0x7E7, opens extended session, reads all known DataIdentifiers (VIN, serial, SW version…).")
        lbl.setFont(mono_font(8))
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

        ctl = QHBoxLayout()
        self.btn_deep_scan = QPushButton("▶  Start Deep Scan")
        self.btn_deep_scan.setObjectName("btn_green")
        self.btn_deep_scan.clicked.connect(self._start_deep_scan)
        self.btn_deep_stop = QPushButton("■  Stop")
        self.btn_deep_stop.clicked.connect(self._stop_deep_scan)
        self.btn_deep_stop.setEnabled(False)
        ctl.addWidget(self.btn_deep_scan)
        ctl.addWidget(self.btn_deep_stop)
        ctl.addStretch()
        lay.addLayout(ctl)

        self.deep_status = QLabel("Idle")
        self.deep_status.setFont(mono_font(8))
        lay.addWidget(self.deep_status)

        self.deep_table = QTableWidget(0, 4)
        self.deep_table.setHorizontalHeaderLabels(["ECU", "DataID", "Hex", "Decoded"])
        self.deep_table.setFont(mono_font(8))
        self.deep_table.verticalHeader().setVisible(False)
        self.deep_table.verticalHeader().setDefaultSectionSize(20)
        self.deep_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.deep_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.deep_table)

        self.deep_log = QTextEdit()
        self.deep_log.setReadOnly(True)
        self.deep_log.setFont(mono_font(8))
        self.deep_log.setMaximumHeight(100)
        lay.addWidget(self.deep_log)
        return w

    def _start_deep_scan(self):
        bus = self._get_bus()
        if bus is None:
            self.deep_log.append("ERROR: CAN bus not connected.")
            return
        from core.uds import UDSScanner
        self._deep_worker = UDSScanner(bus, mode="DEEP")
        self._deep_worker.ecu_result.connect(self._on_ecu_result)
        self._deep_worker.status.connect(lambda s: (self.deep_status.setText(s), self.deep_log.append(s)))
        self._deep_worker.error.connect(lambda e: self.deep_log.append(f"ERROR: {e}"))
        self._deep_worker.finished.connect(self._deep_finished)
        self.deep_table.setRowCount(0)
        self.btn_deep_scan.setEnabled(False)
        self.btn_deep_stop.setEnabled(True)
        self._deep_worker.start()

    def _stop_deep_scan(self):
        if hasattr(self, "_deep_worker") and self._deep_worker:
            self._deep_worker.stop()

    def _deep_finished(self):
        self.btn_deep_scan.setEnabled(True)
        self.btn_deep_stop.setEnabled(False)
        self.deep_status.setText("Deep scan complete.")

    def _on_ecu_result(self, ecu_addr: int, did_name: str, hex_val: str, decoded: str):
        row = self.deep_table.rowCount()
        self.deep_table.insertRow(row)
        cells = [f"0x{ecu_addr:03X}", did_name, hex_val, decoded]
        for ci, txt in enumerate(cells):
            item = QTableWidgetItem(txt)
            item.setFont(mono_font(8))
            if ci == 3 and decoded:
                item.setForeground(QBrush(QColor(COLORS["green"])))
            self.deep_table.setItem(row, ci, item)

    # ── UDS Services tab ──────────────────────────────────────────────────────

    def _build_svc_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lbl = QLabel("Probes UDS service IDs 0x10–0x3E via functional address 0x7DF and reports which are supported.")
        lbl.setFont(mono_font(8))
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

        ctl = QHBoxLayout()
        self.btn_svc_scan = QPushButton("▶  Scan Services")
        self.btn_svc_scan.setObjectName("btn_green")
        self.btn_svc_scan.clicked.connect(self._start_svc_scan)
        self.btn_svc_stop = QPushButton("■  Stop")
        self.btn_svc_stop.clicked.connect(self._stop_svc_scan)
        self.btn_svc_stop.setEnabled(False)
        ctl.addWidget(self.btn_svc_scan)
        ctl.addWidget(self.btn_svc_stop)
        self.svc_unsafe = QCheckBox("Include destructive services (unsafe)")
        self.svc_unsafe.setToolTip(
            "Off: only read-only services are probed. On: also probes ECUReset, "
            "ClearDTC, RoutineControl, RequestDownload, etc. — never do this on a "
            "vehicle you are driving."
        )
        ctl.addWidget(self.svc_unsafe)
        ctl.addStretch()
        lay.addLayout(ctl)

        self.svc_status = QLabel("Idle")
        self.svc_status.setFont(mono_font(8))
        lay.addWidget(self.svc_status)

        self.svc_table = QTableWidget(0, 4)
        self.svc_table.setHorizontalHeaderLabels(["Service ID", "Name", "Supported", "Response (hex)"])
        self.svc_table.setFont(mono_font(8))
        self.svc_table.verticalHeader().setVisible(False)
        self.svc_table.verticalHeader().setDefaultSectionSize(20)
        self.svc_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.svc_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.svc_table)
        return w

    def _start_svc_scan(self):
        bus = self._get_bus()
        if bus is None:
            self.svc_status.setText("ERROR: CAN bus not connected.")
            return
        from core.uds import UDSScanner, UDS_SERVICES
        unsafe = self.svc_unsafe.isChecked()
        if unsafe:
            ok = QMessageBox.warning(
                self, "Unsafe UDS Scan",
                "This will actively invoke destructive UDS services (ECUReset, "
                "ClearDTC, RoutineControl, RequestDownload, …) on the bus.\n\n"
                "Only continue on an isolated bench setup. Proceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ok != QMessageBox.StandardButton.Yes:
                return
        self._svc_worker = UDSScanner(bus, mode="SERVICES", allow_unsafe=unsafe)
        self._svc_worker.service_result.connect(self._on_svc_result)
        self._svc_worker.status.connect(lambda s: self.svc_status.setText(s))
        self._svc_worker.error.connect(lambda e: self.svc_status.setText(f"ERROR: {e}"))
        self._svc_worker.finished.connect(self._svc_finished)
        self.svc_table.setRowCount(0)
        self.btn_svc_scan.setEnabled(False)
        self.btn_svc_stop.setEnabled(True)
        self._svc_worker.start()

    def _stop_svc_scan(self):
        if hasattr(self, "_svc_worker") and self._svc_worker:
            self._svc_worker.stop()

    def _svc_finished(self):
        self.btn_svc_scan.setEnabled(True)
        self.btn_svc_stop.setEnabled(False)
        self.svc_status.setText("Service scan complete.")

    def _on_svc_result(self, ecu_addr: int, svc_id: int, supported: bool, resp_data: bytes):
        from core.uds import UDS_SERVICES
        row = self.svc_table.rowCount()
        self.svc_table.insertRow(row)
        svc_name  = UDS_SERVICES.get(svc_id, f"0x{svc_id:02X}")
        resp_hex  = resp_data.hex().upper() if resp_data else ""
        sup_text  = "✓  YES" if supported else "✗  no"
        cells     = [f"0x{svc_id:02X}", svc_name, sup_text, resp_hex]
        for ci, txt in enumerate(cells):
            item = QTableWidgetItem(txt)
            item.setFont(mono_font(8))
            if ci == 2:
                color = COLORS["green"] if supported else COLORS["dim"]
                item.setForeground(QBrush(QColor(color)))
            self.svc_table.setItem(row, ci, item)

    # ── BUS HEALTH tab ────────────────────────────────────────────────────────

    def _build_health_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lay.addWidget(QLabel(
            "Live CAN bus health: error frames, bus-off events, load statistics, "
            "and IDs that go silent.",
            font=mono_font(8),
        ))

        ctl = QHBoxLayout()
        self.btn_health_start = QPushButton("▶  Start Monitoring")
        self.btn_health_start.setObjectName("btn_green")
        self.btn_health_start.clicked.connect(self._health_start)
        self.btn_health_reset = QPushButton("Reset Counters")
        self.btn_health_reset.clicked.connect(self._health_reset)
        ctl.addWidget(self.btn_health_start)
        ctl.addWidget(self.btn_health_reset)
        ctl.addStretch()
        lay.addLayout(ctl)

        # Stats grid
        grid_widget = QWidget()
        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout(grid_widget)
        grid.setSpacing(4)

        def stat_row(label, row):
            lbl = QLabel(label, font=mono_font(8))
            lbl.setStyleSheet(f"color:{COLORS['dim']}")
            val = QLabel("—", font=mono_font(9))
            val.setStyleSheet(f"color:{COLORS['text']}")
            grid.addWidget(lbl, row, 0)
            grid.addWidget(val, row, 1)
            return val

        self.lbl_h_errors   = stat_row("Error Frames:",   0)
        self.lbl_h_busoff   = stat_row("Bus-Off Events:", 1)
        self.lbl_h_cur_load = stat_row("Current Load:",   2)
        self.lbl_h_peak     = stat_row("Peak Load:",      3)
        self.lbl_h_avg      = stat_row("Avg Load:",       4)
        self.lbl_h_ids      = stat_row("IDs Seen:",       5)
        lay.addWidget(grid_widget)

        # Silent IDs
        lay.addWidget(QLabel("SILENT IDs (> 3× expected period)", font=mono_font(8)))
        self.health_silent = QTextEdit()
        self.health_silent.setReadOnly(True)
        self.health_silent.setFont(mono_font(8))
        self.health_silent.setMaximumHeight(80)
        lay.addWidget(self.health_silent)

        # Load history chart
        self.health_plot = __import__("pyqtgraph").PlotWidget()
        self.health_plot.setBackground(COLORS["bg"])
        self.health_plot.setLabel("left", "Load %")
        self.health_plot.setLabel("bottom", "Seconds")
        self.health_plot.setYRange(0, 100)
        self._health_curve = self.health_plot.plot(pen=COLORS["green"])
        self._health_history: list = []
        lay.addWidget(self.health_plot)

        return w

    def _health_start(self):
        from core.bus_health import BusHealthMeter
        self._health_meter = BusHealthMeter()
        self._health_timer.start()
        self.btn_health_start.setEnabled(False)
        self.btn_health_start.setText("Monitoring…")
        self._state.bus_health_update.connect(self._on_health_update)

    def _health_reset(self):
        if self._health_meter:
            self._health_meter.reset()
        self._health_history.clear()

    def _health_tick(self):
        if self._health_meter is None:
            return
        # Feed live frames from live CAN worker if connected
        bus = self._state.can_bus
        if bus:
            import time
            now = time.monotonic()
            # Non-blocking check
            frame = None
            try:
                frame = bus.recv(timeout=0.0)
            except Exception:
                pass
            if frame:
                is_err = getattr(frame, "is_error_frame", False)
                can_id = f"{frame.arbitration_id:03X}"
                self._health_meter.add_frame(
                    getattr(frame, "dlc", 8), now, can_id, is_err
                )
        snap = self._health_meter.snapshot()
        self._state.bus_health = snap
        self._state.bus_health_update.emit(snap)

    def _on_health_update(self, snap: dict):
        self.lbl_h_errors.setText(str(snap["error_frames"]))
        self.lbl_h_busoff.setText(str(snap["bus_off"]))
        self.lbl_h_cur_load.setText(f"{snap['current_load']:.1f}%")
        self.lbl_h_peak.setText(f"{snap['peak_load']:.1f}%")
        self.lbl_h_avg.setText(f"{snap['avg_load']:.1f}%")
        self.lbl_h_ids.setText(str(snap["total_ids_seen"]))
        silent = snap.get("silent_ids", [])
        self.health_silent.setPlainText(", ".join(silent) if silent else "None")
        self._health_history.append(snap["current_load"])
        if len(self._health_history) > 120:
            self._health_history.pop(0)
        self._health_curve.setData(self._health_history)
        if snap["error_frames"] > 0:
            self.lbl_h_errors.setStyleSheet(f"color:{COLORS['error']}")
        if snap["bus_off"] > 0:
            self.lbl_h_busoff.setStyleSheet(f"color:{COLORS['error']}")
