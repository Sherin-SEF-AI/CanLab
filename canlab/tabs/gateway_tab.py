"""GATEWAY tab — bidirectional CAN MitM bridge with filter/modify rules."""
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QPushButton, QLabel,
    QLineEdit, QComboBox, QSpinBox, QGroupBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QTextEdit, QGridLayout, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QBrush, QFont

from theme import COLORS, mono_font
from core.state import get_state
from core.canid import normalize_id


_DIR_OPTIONS    = ["A→B", "B→A", "Both"]
_ACTION_OPTIONS = ["Pass", "Block", "Modify"]
_IFACE_OPTIONS  = ["socketcan", "pcan", "kvaser", "virtual", "usb2can", "serial"]


class GatewayTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state   = get_state()
        self._worker  = None
        self._rules: list[dict] = []
        self._log_lines: list[str] = []
        self._build_ui()

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left panel: config + rules ─────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(340)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.setSpacing(6)

        ll.addWidget(QLabel("CAN GATEWAY  (MitM bridge)", font=mono_font(9)))

        # Bus A config
        ll.addWidget(self._bus_group("A", "a"))
        # Bus B config
        ll.addWidget(self._bus_group("B", "b"))

        # Control buttons
        ctl = QHBoxLayout()
        self.btn_start = QPushButton("START GATEWAY")
        self.btn_start.setObjectName("btn_green")
        self.btn_start.setFont(mono_font(9))
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setFont(mono_font(9))
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setEnabled(False)
        ctl.addWidget(self.btn_start)
        ctl.addWidget(self.btn_stop)
        ll.addLayout(ctl)

        # Stats
        stats_grp = QGroupBox("STATISTICS")
        sg = QGridLayout(stats_grp)
        sg.setSpacing(4)
        self.lbl_fwd_ab  = QLabel("0")
        self.lbl_fwd_ba  = QLabel("0")
        self.lbl_blocked = QLabel("0")
        self.lbl_mod     = QLabel("0")
        self.lbl_rate_ab = QLabel("0 fps")
        self.lbl_rate_ba = QLabel("0 fps")
        for lbl in [self.lbl_fwd_ab, self.lbl_fwd_ba, self.lbl_blocked,
                    self.lbl_mod, self.lbl_rate_ab, self.lbl_rate_ba]:
            lbl.setFont(mono_font(9, bold=True))
            lbl.setStyleSheet(f"color:{COLORS['green']}")
        sg.addWidget(QLabel("A→B fwd:", font=mono_font(8)), 0, 0)
        sg.addWidget(self.lbl_fwd_ab, 0, 1)
        sg.addWidget(QLabel("B→A fwd:", font=mono_font(8)), 1, 0)
        sg.addWidget(self.lbl_fwd_ba, 1, 1)
        sg.addWidget(QLabel("Blocked:", font=mono_font(8)), 2, 0)
        sg.addWidget(self.lbl_blocked, 2, 1)
        sg.addWidget(QLabel("Modified:", font=mono_font(8)), 3, 0)
        sg.addWidget(self.lbl_mod, 3, 1)
        sg.addWidget(QLabel("A→B rate:", font=mono_font(8)), 0, 2)
        sg.addWidget(self.lbl_rate_ab, 0, 3)
        sg.addWidget(QLabel("B→A rate:", font=mono_font(8)), 1, 2)
        sg.addWidget(self.lbl_rate_ba, 1, 3)
        ll.addWidget(stats_grp)

        # Rule editor
        rule_grp = QGroupBox("FILTER / MODIFY RULE")
        rg = QGridLayout(rule_grp)
        rg.setSpacing(4)

        rg.addWidget(QLabel("ID (hex, blank=any):", font=mono_font(8)), 0, 0)
        self.rule_id = QLineEdit()
        self.rule_id.setPlaceholderText("1A0")
        self.rule_id.setFont(mono_font())
        rg.addWidget(self.rule_id, 0, 1)

        rg.addWidget(QLabel("Direction:", font=mono_font(8)), 1, 0)
        self.rule_dir = QComboBox()
        self.rule_dir.addItems(_DIR_OPTIONS)
        self.rule_dir.setFont(mono_font(8))
        rg.addWidget(self.rule_dir, 1, 1)

        rg.addWidget(QLabel("Action:", font=mono_font(8)), 2, 0)
        self.rule_action = QComboBox()
        self.rule_action.addItems(_ACTION_OPTIONS)
        self.rule_action.setFont(mono_font(8))
        self.rule_action.currentTextChanged.connect(self._on_action_changed)
        rg.addWidget(self.rule_action, 2, 1)

        rg.addWidget(QLabel("Byte idx (Modify):", font=mono_font(8)), 3, 0)
        self.rule_byte = QSpinBox()
        self.rule_byte.setRange(0, 7)
        self.rule_byte.setFont(mono_font(8))
        self.rule_byte.setEnabled(False)
        rg.addWidget(self.rule_byte, 3, 1)

        rg.addWidget(QLabel("New value (hex):", font=mono_font(8)), 4, 0)
        self.rule_val = QLineEdit("00")
        self.rule_val.setFont(mono_font())
        self.rule_val.setEnabled(False)
        rg.addWidget(self.rule_val, 4, 1)

        rg.addWidget(QLabel("Rewrite ID (optional):", font=mono_font(8)), 5, 0)
        self.rule_new_id = QLineEdit()
        self.rule_new_id.setPlaceholderText("leave blank to keep")
        self.rule_new_id.setFont(mono_font())
        rg.addWidget(self.rule_new_id, 5, 1)

        rg.addWidget(QLabel("Label:", font=mono_font(8)), 6, 0)
        self.rule_label = QLineEdit()
        self.rule_label.setFont(mono_font())
        rg.addWidget(self.rule_label, 6, 1)

        btn_add = QPushButton("Add Rule")
        btn_add.setObjectName("btn_green")
        btn_add.clicked.connect(self._add_rule)
        rg.addWidget(btn_add, 7, 0, 1, 2)
        ll.addWidget(rule_grp)

        # Rules table
        ll.addWidget(QLabel("ACTIVE RULES  (first match wins)", font=mono_font(8)))
        self.rules_table = QTableWidget(0, 6)
        self.rules_table.setHorizontalHeaderLabels(["ID", "Dir", "Action", "Byte", "Val", "Del"])
        self.rules_table.setFont(mono_font(8))
        self.rules_table.verticalHeader().setVisible(False)
        self.rules_table.verticalHeader().setDefaultSectionSize(20)
        self.rules_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.rules_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.rules_table.setMaximumHeight(160)
        self.rules_table.cellClicked.connect(self._on_rules_table_click)
        ll.addWidget(self.rules_table)

        splitter.addWidget(left)

        # ── Right panel: live frame log ────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("FRAME LOG", font=mono_font(9)))
        hdr.addStretch()
        self.chk_log_fwd   = QCheckBox("Forwarded")
        self.chk_log_fwd.setChecked(True)
        self.chk_log_fwd.setFont(mono_font(8))
        self.chk_log_block = QCheckBox("Blocked")
        self.chk_log_block.setChecked(True)
        self.chk_log_block.setFont(mono_font(8))
        self.chk_log_mod   = QCheckBox("Modified")
        self.chk_log_mod.setChecked(True)
        self.chk_log_mod.setFont(mono_font(8))
        btn_clear = QPushButton("Clear")
        btn_clear.setFont(mono_font(8))
        btn_clear.clicked.connect(self._clear_log)
        for w in [self.chk_log_fwd, self.chk_log_block, self.chk_log_mod, btn_clear]:
            hdr.addWidget(w)
        rl.addLayout(hdr)

        self.frame_log = QTextEdit()
        self.frame_log.setReadOnly(True)
        self.frame_log.setFont(QFont("Courier New", 8))
        self.frame_log.setStyleSheet(
            f"background:{COLORS['bg']}; color:{COLORS['text']}; "
            f"border:1px solid {COLORS['border']};"
        )
        rl.addWidget(self.frame_log)

        splitter.addWidget(right)
        splitter.setSizes([340, 700])
        root.addWidget(splitter)

    def _bus_group(self, label: str, suffix: str) -> QGroupBox:
        grp = QGroupBox(f"BUS {label}")
        g   = QGridLayout(grp)
        g.setSpacing(4)

        g.addWidget(QLabel("Interface:", font=mono_font(8)), 0, 0)
        combo = QComboBox()
        combo.addItems(_IFACE_OPTIONS)
        combo.setFont(mono_font(8))
        setattr(self, f"iface_{suffix}", combo)
        g.addWidget(combo, 0, 1)

        g.addWidget(QLabel("Channel:", font=mono_font(8)), 1, 0)
        chan = QLineEdit("can0" if suffix == "a" else "can1")
        chan.setFont(mono_font(8))
        setattr(self, f"chan_{suffix}", chan)
        g.addWidget(chan, 1, 1)

        g.addWidget(QLabel("Bitrate:", font=mono_font(8)), 2, 0)
        br = QSpinBox()
        br.setRange(10_000, 5_000_000)
        br.setSingleStep(50_000)
        br.setValue(500_000)
        br.setFont(mono_font(8))
        setattr(self, f"bitrate_{suffix}", br)
        g.addWidget(br, 2, 1)

        return grp

    # ── Rule management ────────────────────────────────────────────────────────

    def _on_action_changed(self, action: str):
        is_mod = action == "Modify"
        self.rule_byte.setEnabled(is_mod)
        self.rule_val.setEnabled(is_mod)

    def _add_rule(self):
        try:
            mod_val = int(self.rule_val.text().strip() or "0", 16)
        except ValueError:
            mod_val = 0
        rule = {
            "id":        normalize_id(self.rule_id.text()),
            "direction": self.rule_dir.currentText(),
            "action":    self.rule_action.currentText(),
            "mod_byte":  self.rule_byte.value(),
            "mod_val":   mod_val,
            "new_id":    normalize_id(self.rule_new_id.text()) if self.rule_new_id.text().strip() else "",
            "label":     self.rule_label.text().strip() or f"Rule {len(self._rules)+1}",
        }
        self._rules.append(rule)
        self._refresh_rules_table()
        if self._worker:
            self._worker.set_rules(self._rules)

    def _on_rules_table_click(self, row: int, col: int):
        if col == 5 and 0 <= row < len(self._rules):   # Del column
            self._rules.pop(row)
            self._refresh_rules_table()
            if self._worker:
                self._worker.set_rules(self._rules)

    def _refresh_rules_table(self):
        self.rules_table.setRowCount(len(self._rules))
        for i, r in enumerate(self._rules):
            cells = [
                f"0x{r['id']}" if r["id"] else "*",
                r["direction"],
                r["action"],
                str(r["mod_byte"]) if r["action"] == "Modify" else "—",
                f"0x{r['mod_val']:02X}" if r["action"] == "Modify" else "—",
                "✕",
            ]
            colors = {
                "Pass":   COLORS["green"],
                "Block":  COLORS["error"],
                "Modify": COLORS["amber"],
            }
            for ci, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font(8))
                if ci == 2:
                    item.setForeground(QBrush(QColor(colors.get(r["action"], COLORS["text"]))))
                self.rules_table.setItem(i, ci, item)

    # ── Gateway control ────────────────────────────────────────────────────────

    def _bus_cfg(self, suffix: str) -> dict:
        return {
            "interface": getattr(self, f"iface_{suffix}").currentText(),
            "channel":   getattr(self, f"chan_{suffix}").text().strip(),
            "bitrate":   getattr(self, f"bitrate_{suffix}").value(),
        }

    def _start(self):
        from core.gateway import GatewayWorker
        self._worker = GatewayWorker(
            bus_a_cfg=self._bus_cfg("a"),
            bus_b_cfg=self._bus_cfg("b"),
            rules=list(self._rules),
        )
        self._worker.frame_forwarded.connect(self._on_forwarded)
        self._worker.frame_blocked.connect(self._on_blocked)
        self._worker.frame_modified.connect(self._on_modified)
        self._worker.stats_updated.connect(self._on_stats)
        self._worker.error.connect(self._on_error)
        self._worker.start()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        amber = COLORS["amber"]
        ch_a  = self._bus_cfg("a")["channel"]
        ch_b  = self._bus_cfg("b")["channel"]
        self._log(f"<span style='color:{amber}'>Gateway started: {ch_a} ↔ {ch_b}</span>")

    def _stop(self):
        if self._worker:
            self._worker.stop()
            self._worker = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        dim = COLORS["dim"]
        self._log(f"<span style='color:{dim}'>Gateway stopped.</span>")

    # ── Signal handlers ────────────────────────────────────────────────────────

    def _on_forwarded(self, direction: str, arb_id: int, data: bytes):
        if not self.chk_log_fwd.isChecked():
            return
        hex_data = " ".join(f"{b:02X}" for b in data)
        c = COLORS["green"]
        self._log(f"<span style='color:{c}'>FWD {direction}  0x{arb_id:03X}  [{hex_data}]</span>")

    def _on_blocked(self, direction: str, arb_id: int):
        if not self.chk_log_block.isChecked():
            return
        c = COLORS["error"]
        self._log(f"<span style='color:{c}'>BLK {direction}  0x{arb_id:03X}</span>")

    def _on_modified(self, direction: str, arb_id: int, data: bytes):
        if not self.chk_log_mod.isChecked():
            return
        hex_data = " ".join(f"{b:02X}" for b in data)
        c = COLORS["amber"]
        self._log(f"<span style='color:{c}'>MOD {direction}  0x{arb_id:03X}  [{hex_data}]</span>")

    def _on_stats(self, stats: dict):
        self.lbl_fwd_ab.setText(str(stats["fwd_a_b"]))
        self.lbl_fwd_ba.setText(str(stats["fwd_b_a"]))
        self.lbl_blocked.setText(str(stats["blocked"]))
        self.lbl_mod.setText(str(stats["modified"]))
        self.lbl_rate_ab.setText(f"{stats['rate_a_b']:.0f} fps")
        self.lbl_rate_ba.setText(f"{stats['rate_b_a']:.0f} fps")

    def _on_error(self, msg: str):
        c = COLORS["error"]
        self._log(f"<span style='color:{c}'>ERROR: {msg}</span>")
        self._stop()

    # ── Log helpers ────────────────────────────────────────────────────────────

    def _log(self, html: str):
        self._log_lines.append(html)
        if len(self._log_lines) > 2000:
            self._log_lines = self._log_lines[-1500:]
        self.frame_log.setHtml("<br>".join(self._log_lines[-500:]))
        self.frame_log.verticalScrollBar().setValue(
            self.frame_log.verticalScrollBar().maximum()
        )

    def _clear_log(self):
        self._log_lines.clear()
        self.frame_log.clear()
