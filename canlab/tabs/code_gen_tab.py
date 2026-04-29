import subprocess
import tempfile
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QPushButton, QLabel,
    QComboBox, QLineEdit, QCheckBox, QGroupBox, QFileDialog,
    QScrollArea, QTextEdit,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QFont, QSyntaxHighlighter, QTextCharFormat, QColor, QTextDocument,
)
import re
from theme import COLORS, mono_font
from core.state import get_state


class PythonHighlighter(QSyntaxHighlighter):
    KEYWORDS = [
        "import", "from", "def", "class", "if", "else", "elif",
        "for", "while", "try", "except", "with", "as", "return",
        "in", "not", "and", "or", "True", "False", "None", "pass",
        "break", "continue", "lambda", "yield", "raise", "global",
    ]

    def __init__(self, doc: QTextDocument):
        super().__init__(doc)
        self._rules = []

        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor(COLORS["keyword"]))
        kw_fmt.setFontWeight(QFont.Weight.Bold)
        for kw in self.KEYWORDS:
            self._rules.append((re.compile(rf"\b{kw}\b"), kw_fmt))

        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor(COLORS["string"]))
        self._rules.append((re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'), str_fmt))
        self._rules.append((re.compile(r"'[^'\\]*(?:\\.[^'\\]*)*'"), str_fmt))
        self._rules.append((re.compile(r'"""[\s\S]*?"""'), str_fmt))

        cmt_fmt = QTextCharFormat()
        cmt_fmt.setForeground(QColor(COLORS["comment"]))
        self._rules.append((re.compile(r"#[^\n]*"), cmt_fmt))

        fn_fmt = QTextCharFormat()
        fn_fmt.setForeground(QColor(COLORS["function"]))
        self._rules.append((re.compile(r"\bdef\s+(\w+)"), fn_fmt))

        dec_fmt = QTextCharFormat()
        dec_fmt.setForeground(QColor(COLORS["amber"]))
        self._rules.append((re.compile(r"\b\d+(?:\.\d+)?\b"), dec_fmt))

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


class CodeGenTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = get_state()
        self._build_ui()
        self._state.dbc_updated.connect(self._refresh_signal_checks)

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: options
        left = QWidget()
        left.setFixedWidth(220)
        scroll = QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(6, 6, 6, 6)
        left_lay.setSpacing(6)

        # Mode
        grp_mode = QGroupBox("MODE")
        grp_m_lay = QVBoxLayout(grp_mode)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["READ", "WRITE", "BOTH"])
        grp_m_lay.addWidget(self.mode_combo)
        left_lay.addWidget(grp_mode)

        # Interface
        grp_iface = QGroupBox("INTERFACE")
        grp_i_lay = QVBoxLayout(grp_iface)
        self.iface_combo = QComboBox()
        self.iface_combo.addItems(["socketcan", "pcan", "kvaser", "virtual"])
        grp_i_lay.addWidget(self.iface_combo)
        self.channel_edit = QLineEdit("can0")
        grp_i_lay.addWidget(QLabel("Channel:"))
        grp_i_lay.addWidget(self.channel_edit)
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["500000", "250000", "1000000"])
        grp_i_lay.addWidget(QLabel("Bitrate:"))
        grp_i_lay.addWidget(self.bitrate_combo)
        left_lay.addWidget(grp_iface)

        # Signals
        grp_sig = QGroupBox("SIGNALS TO INCLUDE")
        self.sig_checks_lay = QVBoxLayout(grp_sig)
        left_lay.addWidget(grp_sig)

        # Hyundai options
        grp_hyu = QGroupBox("HYUNDAI OPTIONS")
        grp_h_lay = QVBoxLayout(grp_hyu)
        self.chk_checksum = QCheckBox("Include checksum handler")
        self.chk_counter  = QCheckBox("Include rolling counter")
        self.chk_keepalive = QCheckBox("Include keepalive loop")
        for c in [self.chk_checksum, self.chk_counter, self.chk_keepalive]:
            c.setChecked(True)
            grp_h_lay.addWidget(c)
        left_lay.addWidget(grp_hyu)

        btn_gen = QPushButton("Generate Code")
        btn_gen.setObjectName("btn_green")
        btn_gen.clicked.connect(self._generate)
        left_lay.addWidget(btn_gen)
        left_lay.addStretch()

        splitter.addWidget(scroll)

        # Right: code output
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        toolbar = QWidget()
        toolbar.setFixedHeight(28)
        toolbar.setStyleSheet(
            f"background:{COLORS['panel_bg']};border-bottom:1px solid {COLORS['border']};"
        )
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(4, 2, 4, 2)
        tb.setSpacing(6)
        lbl = QLabel("GENERATED PYTHON")
        lbl.setObjectName("label_dim")
        lbl.setFont(mono_font(8))
        tb.addWidget(lbl)
        tb.addStretch()
        btn_copy = QPushButton("Copy")
        btn_copy.clicked.connect(self._copy)
        btn_copy.setMaximumWidth(55)
        tb.addWidget(btn_copy)
        btn_save = QPushButton("Save .py")
        btn_save.clicked.connect(self._save)
        btn_save.setMaximumWidth(70)
        tb.addWidget(btn_save)
        btn_open = QPushButton("Open in Editor")
        btn_open.clicked.connect(self._open_in_editor)
        btn_open.setMaximumWidth(110)
        tb.addWidget(btn_open)
        right_lay.addWidget(toolbar)

        self.code_edit = QTextEdit()
        self.code_edit.setFont(mono_font())
        self.code_edit.setReadOnly(True)
        self.highlighter = PythonHighlighter(self.code_edit.document())
        right_lay.addWidget(self.code_edit)

        splitter.addWidget(right)
        splitter.setSizes([220, 780])
        lay.addWidget(splitter)

    def _refresh_signal_checks(self):
        while self.sig_checks_lay.count():
            item = self.sig_checks_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for sig in self._state.dbc_signals:
            name = f"0x{sig.get('message_id','')} {sig.get('signal_name','?')}"
            chk = QCheckBox(name)
            chk.setChecked(True)
            chk.setObjectName(sig.get("signal_name",""))
            self.sig_checks_lay.addWidget(chk)

    def _get_selected_signals(self) -> list:
        result = []
        for i in range(self.sig_checks_lay.count()):
            w = self.sig_checks_lay.itemAt(i).widget()
            if isinstance(w, QCheckBox) and w.isChecked():
                name = w.objectName()
                for sig in self._state.dbc_signals:
                    if sig.get("signal_name") == name:
                        result.append(sig)
                        break
        return result

    def _generate(self):
        mode     = self.mode_combo.currentText()
        iface    = self.iface_combo.currentText()
        channel  = self.channel_edit.text()
        bitrate  = self.bitrate_combo.currentText()
        sigs     = self._get_selected_signals()
        checksum = self.chk_checksum.isChecked()
        counter  = self.chk_counter.isChecked()
        keepalive = self.chk_keepalive.isChecked()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        code = _build_code(mode, iface, channel, bitrate, sigs,
                            checksum, counter, keepalive, ts)
        self.code_edit.setPlainText(code)

    def _copy(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.code_edit.toPlainText())

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Python", "can_reader.py", "Python (*.py)")
        if path:
            with open(path, "w") as f:
                f.write(self.code_edit.toPlainText())

    def _open_in_editor(self):
        code = self.code_edit.toPlainText()
        if not code:
            return
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp = f.name
        try:
            subprocess.Popen(["xdg-open", tmp])
        except Exception:
            pass


def _build_code(mode, iface, channel, bitrate, sigs,
                checksum, counter, keepalive, ts) -> str:
    sig_names = [s.get("signal_name","?") for s in sigs]

    lines = [
        '#!/usr/bin/env python3',
        f'"""',
        f'CANLAB — Generated CAN {"Reader" if mode == "READ" else "Writer" if mode == "WRITE" else "Reader/Writer"}',
        f'Vehicle: Hyundai Kona',
        f'Generated: {ts}',
        f'Signals: {", ".join(sig_names) if sig_names else "all"}',
        f'"""',
        'import can',
        'import cantools',
        'import time',
        'import struct',
        '',
    ]

    if checksum:
        lines += [
            'def hyundai_checksum(data: bytes, msg_id: int) -> int:',
            '    """Hyundai/Kia CAN checksum (byte 7)."""',
            '    checksum = 0',
            '    for b in data[:7]:',
            '        checksum += b',
            '    checksum += (msg_id >> 8) & 0xFF',
            '    checksum += msg_id & 0xFF',
            '    return (~checksum) & 0xFF',
            '',
        ]

    if counter:
        lines += [
            '_rolling_counters = {}',
            '',
            'def next_counter(msg_id: int) -> int:',
            '    """Increment 4-bit rolling counter for message."""',
            '    _rolling_counters[msg_id] = (_rolling_counters.get(msg_id, -1) + 1) & 0x0F',
            '    return _rolling_counters[msg_id]',
            '',
        ]

    lines += [
        'db = cantools.database.load_file("decoded.dbc")',
        f'bus = can.interface.Bus(channel="{channel}", bustype="{iface}", bitrate={bitrate})',
        '',
        f'print("CANLAB — {mode} mode on {channel} @ {bitrate}bps...")',
        '',
    ]

    if mode in ("READ", "BOTH"):
        lines += [
            'def on_message(msg: can.Message):',
            '    try:',
            '        decoded = db.decode_message(msg.arbitration_id, msg.data)',
        ]
        if sig_names:
            for sname in sig_names:
                lines.append(f'        if "{sname}" in decoded:')
                lines.append(f'            print(f"{sname}: {{decoded[\"{sname}\"]}}")')
        else:
            lines.append('        print(f"ID 0x{msg.arbitration_id:03X}: {decoded}")')
        lines += [
            '    except KeyError:',
            '        pass',
            '    except Exception as e:',
            '        print(f"Decode error: {e}")',
            '',
        ]

    if mode in ("WRITE", "BOTH") and sigs:
        for sig in sigs:
            mid  = int(sig.get("message_id","0"), 16) if sig.get("message_id") else 0
            sname = sig.get("signal_name","Signal")
            scale = sig.get("scale", 1.0)
            offset = sig.get("offset", 0.0)
            unit = sig.get("unit","")
            lines += [
                f'def send_{sname.lower()}(value: float):',
                f'    """Send {sname} ({unit}) — scale={scale}, offset={offset}."""',
                f'    raw = int((value - {offset}) / {scale})',
                f'    data = bytearray(8)',
            ]
            sb = int(sig.get("start_bit", 0))
            lb = int(sig.get("length", 8))
            byte_idx = sb // 8
            bit_off  = sb % 8
            lines.append(f'    data[{byte_idx}] = (raw >> {bit_off}) & 0xFF')
            if counter:
                lines.append(f'    data[0] = (data[0] & 0x0F) | (next_counter(0x{mid:03X}) << 4)')
            if checksum:
                lines.append(f'    data[7] = hyundai_checksum(bytes(data), 0x{mid:03X})')
            lines += [
                f'    msg = can.Message(arbitration_id=0x{mid:03X}, data=bytes(data), is_extended_id=False)',
                '    bus.send(msg)',
                '',
            ]

    if keepalive and mode in ("WRITE", "BOTH"):
        lines += [
            'def keepalive_loop():',
            '    """Send periodic keepalive messages."""',
            '    while True:',
        ]
        for sig in sigs:
            sname = sig.get("signal_name","Signal")
            lines.append(f'        send_{sname.lower()}(0)')
        lines += [
            '        time.sleep(0.01)',
            '',
        ]

    if mode in ("READ", "BOTH"):
        lines += [
            'try:',
            '    for msg in bus:',
            '        on_message(msg)',
            'except KeyboardInterrupt:',
            '    print("Stopped.")',
            'finally:',
            '    bus.shutdown()',
        ]
    else:
        lines += [
            'try:',
            '    keepalive_loop()' if keepalive else '    pass  # add your send logic here',
            'except KeyboardInterrupt:',
            '    print("Stopped.")',
            'finally:',
            '    bus.shutdown()',
        ]

    return "\n".join(lines)
