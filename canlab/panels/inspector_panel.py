import numpy as np
import pandas as pd
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTextEdit, QPushButton,
    QGridLayout, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont
import pyqtgraph as pg
from theme import COLORS, mono_font
from core.state import get_state

BYTE_COLS = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]


class InspectorPanel(QWidget):
    send_to_ai = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(280)
        self._state = get_state()
        self._current_id = ""
        self._build_ui()
        self._state.id_selected.connect(self._update_for_id)
        self._state.frames_updated.connect(lambda: self._update_for_id(self._current_id))

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self.lbl_id = QLabel("SELECT AN ID")
        self.lbl_id.setObjectName("label_green")
        self.lbl_id.setFont(mono_font(10, bold=True))
        lay.addWidget(self.lbl_id)

        # Hex dump
        lbl_hex = QLabel("LAST 10 FRAMES")
        lbl_hex.setObjectName("label_dim")
        lbl_hex.setFont(mono_font(8))
        lay.addWidget(lbl_hex)

        self.hex_dump = QTextEdit()
        self.hex_dump.setReadOnly(True)
        self.hex_dump.setMaximumHeight(150)
        self.hex_dump.setFont(mono_font(8))
        lay.addWidget(self.hex_dump)

        # Byte heatmap
        lbl_heat = QLabel("BYTE ACTIVITY")
        lbl_heat.setObjectName("label_dim")
        lbl_heat.setFont(mono_font(8))
        lay.addWidget(lbl_heat)

        self.heatmap_widget = _ByteHeatmap()
        self.heatmap_widget.setFixedHeight(28)
        lay.addWidget(self.heatmap_widget)

        # Stats grid
        lbl_stats = QLabel("BYTE STATS (min/max/mean)")
        lbl_stats.setObjectName("label_dim")
        lbl_stats.setFont(mono_font(8))
        lay.addWidget(lbl_stats)

        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setMaximumHeight(120)
        self.stats_text.setFont(mono_font(8))
        lay.addWidget(self.stats_text)

        # Type badge
        self.type_badge = QLabel("TYPE: UNKNOWN")
        self.type_badge.setFont(mono_font(9, bold=True))
        self.type_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.type_badge.setFixedHeight(22)
        lay.addWidget(self.type_badge)

        # Send to AI
        self.btn_ai = QPushButton("Send to AI Engine")
        self.btn_ai.setObjectName("btn_amber")
        self.btn_ai.clicked.connect(lambda: self.send_to_ai.emit(self._current_id))
        lay.addWidget(self.btn_ai)

        lay.addStretch()

    def _update_for_id(self, hex_id: str):
        if not hex_id:
            return
        self._current_id = hex_id
        frames = self._state.get_frames_for_id(hex_id)
        self.lbl_id.setText(f"0x{hex_id}")

        if frames.empty:
            self.hex_dump.setPlainText("No frames")
            return

        # Hex dump
        last10 = frames.tail(10)
        lines = []
        for _, row in last10.iterrows():
            byte_str = " ".join(
                format(int(row[c]), "02X") if pd.notna(row.get(c)) else "--"
                for c in BYTE_COLS
            )
            lines.append(byte_str)
        self.hex_dump.setPlainText("\n".join(lines))

        # Change rates for heatmap
        change_rates = []
        for col in BYTE_COLS:
            if col not in frames.columns:
                change_rates.append(0.0)
                continue
            s = frames[col].dropna()
            if len(s) < 2:
                change_rates.append(0.0)
            else:
                change_rates.append(float((s.diff() != 0).sum() / (len(s) - 1)))
        self.heatmap_widget.set_values(change_rates)

        # Stats
        stat_lines = []
        for col in BYTE_COLS:
            if col not in frames.columns:
                continue
            s = frames[col].dropna()
            if s.empty:
                continue
            stat_lines.append(
                f"{col}: {int(s.min()):3d} / {int(s.max()):3d} / {s.mean():5.1f}"
            )
        self.stats_text.setPlainText("\n".join(stat_lines))

        # Type
        from core.signal_analyzer import analyze_id
        stats = analyze_id(frames)
        stype = stats.get("suspected_type", "UNKNOWN")
        type_colors = {
            "SENSOR":      COLORS["green"],
            "COUNTER":     COLORS["accent"],
            "STATUS_FLAG": COLORS["amber"],
            "DIAGNOSTIC":  COLORS["dim"],
            "UNKNOWN":     COLORS["text"],
        }
        color = type_colors.get(stype, COLORS["text"])
        self.type_badge.setText(f"TYPE: {stype}")
        self.type_badge.setStyleSheet(
            f"color: {color}; border: 1px solid {color}; "
            f"background: {COLORS['panel_bg']}; font-family: 'Courier New'; font-size: 9pt;"
        )


class _ByteHeatmap(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = [0.0] * 8

    def set_values(self, values: list):
        self._values = values[:8]
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor
        painter = QPainter(self)
        w = self.width() / 8
        h = self.height()
        for i, val in enumerate(self._values):
            intensity = int(val * 255)
            r = min(255, intensity * 2)
            g = min(255, intensity)
            b = 0
            color = QColor(r, g, b)
            painter.fillRect(int(i * w), 0, int(w) - 1, h, color)
            painter.setPen(QColor(COLORS["border"]))
            from PyQt6.QtCore import QRect
            painter.drawText(
                int(i * w), 0, int(w), h,
                Qt.AlignmentFlag.AlignCenter,
                f"B{i}"
            )
