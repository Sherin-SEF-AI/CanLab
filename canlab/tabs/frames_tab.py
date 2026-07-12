import pandas as pd
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLabel, QLineEdit, QComboBox, QPushButton, QCheckBox, QDialog,
    QTextEdit, QHeaderView,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QBrush, QFont
from theme import COLORS, mono_font
from core.state import get_state

BYTE_COLS   = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]
ALL_COLUMNS = ["Timestamp", "ID", "Bus", "DLC"] + BYTE_COLS + ["Delta"]
MAX_DISPLAY = 5000


def _active_byte_cols(df) -> list:
    """Return B0..B7 normally; extend to B0..B{n-1} if CAN FD frames present."""
    try:
        from core.canfd import columns_for_dataframe
        return columns_for_dataframe(df)
    except Exception:
        return BYTE_COLS


class FramesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state      = get_state()
        self._frozen     = False
        self._follow     = True
        self._filter_id  = ""
        self._filter_bus = ""
        self._last_bytes: dict = {}
        self._build_ui()
        # Coalesce rapid live-capture updates (frames_updated fires every ~50
        # frames) into at most one full table rebuild per interval, so capture
        # doesn't thrash the UI rebuilding thousands of QTableWidgetItems.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self._refresh)
        self._state.frames_updated.connect(self._schedule_refresh)
        self._state.id_selected.connect(self._filter_by_id)

    def _schedule_refresh(self):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Filter bar
        filter_bar = QWidget()
        filter_bar.setFixedHeight(28)
        filter_bar.setStyleSheet(f"background:{COLORS['panel_bg']}; border-bottom:1px solid {COLORS['border']};")
        fb_lay = QHBoxLayout(filter_bar)
        fb_lay.setContentsMargins(4, 2, 4, 2)
        fb_lay.setSpacing(6)

        fb_lay.addWidget(QLabel("ID:"))
        self.filter_id = QLineEdit()
        self.filter_id.setPlaceholderText("hex filter...")
        self.filter_id.setMaximumWidth(100)
        self.filter_id.textChanged.connect(self._on_filter_changed)
        fb_lay.addWidget(self.filter_id)

        fb_lay.addWidget(QLabel("Bus:"))
        self.filter_bus = QComboBox()
        self.filter_bus.addItem("All")
        self.filter_bus.setMaximumWidth(80)
        self.filter_bus.currentTextChanged.connect(self._on_filter_changed)
        fb_lay.addWidget(self.filter_bus)

        fb_lay.addStretch()

        self.chk_follow = QCheckBox("Follow")
        self.chk_follow.setChecked(True)
        self.chk_follow.toggled.connect(lambda v: setattr(self, '_follow', v))
        fb_lay.addWidget(self.chk_follow)

        self.btn_freeze = QPushButton("Freeze")
        self.btn_freeze.setCheckable(True)
        self.btn_freeze.setMaximumWidth(60)
        self.btn_freeze.toggled.connect(self._on_freeze)
        fb_lay.addWidget(self.btn_freeze)

        self.lbl_count = QLabel("0 / 0 frames")
        self.lbl_count.setObjectName("label_dim")
        fb_lay.addWidget(self.lbl_count)

        lay.addWidget(filter_bar)

        # Table
        self.table = QTableWidget(0, len(ALL_COLUMNS))
        self.table.setHorizontalHeaderLabels(ALL_COLUMNS)
        self.table.setFont(mono_font())
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setShowGrid(False)
        self.table.doubleClicked.connect(self._show_frame_detail)
        lay.addWidget(self.table)

    def _on_freeze(self, frozen: bool):
        self._frozen = frozen
        self.btn_freeze.setText("Frozen" if frozen else "Freeze")

    def _on_filter_changed(self):
        self._filter_id  = self.filter_id.text().strip().upper()
        self._filter_bus = self.filter_bus.currentText()
        self._refresh()

    def _filter_by_id(self, hex_id: str):
        self.filter_id.setText(hex_id)

    def _refresh(self):
        if self._frozen:
            return
        df = self._state.frames_df
        if df.empty:
            return

        # Update bus combo
        buses = ["All"] + [str(b) for b in sorted(df["Bus"].unique())] if "Bus" in df.columns else ["All"]
        cur = self.filter_bus.currentText()
        self.filter_bus.blockSignals(True)
        self.filter_bus.clear()
        self.filter_bus.addItems(buses)
        idx = self.filter_bus.findText(cur)
        self.filter_bus.setCurrentIndex(max(0, idx))
        self.filter_bus.blockSignals(False)

        # Apply filters
        fdf = df
        if self._filter_id:
            fdf = fdf[fdf["ID"].str.contains(self._filter_id, case=False, na=False)]
        if self._filter_bus and self._filter_bus != "All":
            fdf = fdf[fdf["Bus"].astype(str) == self._filter_bus]

        # Limit display
        total = len(fdf)
        fdf = fdf.tail(MAX_DISPLAY)
        self.lbl_count.setText(f"{len(fdf)} / {total} frames")

        # Dynamic byte columns (CAN FD support)
        active_byte_cols = _active_byte_cols(fdf)
        all_cols = ["Timestamp", "ID", "Bus", "DLC"] + active_byte_cols + ["Delta"]
        if self.table.columnCount() != len(all_cols):
            self.table.setColumnCount(len(all_cols))
            self.table.setHorizontalHeaderLabels(all_cols)

        self.table.setRowCount(len(fdf))
        prev_bytes: dict = {}

        for row_idx, (_, row) in enumerate(fdf.iterrows()):
            cid = str(row.get("ID", ""))
            vals = [
                f"{row.get('Timestamp', 0):.4f}",
                cid,
                str(row.get("Bus", "")),
                str(int(row.get("DLC", 8))),
            ]
            byte_vals = []
            for col in active_byte_cols:
                v = row.get(col)
                if pd.notna(v):
                    byte_vals.append(int(v))
                    vals.append(format(int(v), "02X"))
                else:
                    byte_vals.append(None)
                    vals.append("--")
            delta = row.get("Delta", 0)
            vals.append(f"{delta*1000:.1f}ms" if pd.notna(delta) else "")

            prev = self._last_bytes.get(cid)
            byte_start_col = 4
            byte_end_col   = byte_start_col + len(active_byte_cols) - 1
            for col_idx, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setFont(mono_font())
                # Highlight changed bytes
                if prev and byte_start_col <= col_idx <= byte_end_col:
                    byte_idx = col_idx - byte_start_col
                    if (byte_idx < len(byte_vals) and
                            byte_idx < len(prev) and
                            byte_vals[byte_idx] is not None and
                            prev[byte_idx] is not None and
                            byte_vals[byte_idx] != prev[byte_idx]):
                        item.setForeground(QBrush(QColor(COLORS["green"])))
                        item.setBackground(QBrush(QColor("#001a0d")))
                self.table.setItem(row_idx, col_idx, item)

            self._last_bytes[cid] = byte_vals

        if self._follow and self.table.rowCount() > 0:
            self.table.scrollToBottom()

    def _show_frame_detail(self, index):
        row = index.row()
        if row >= self.table.rowCount():
            return
        dialog = FrameDetailDialog(self.table, row, self)
        dialog.exec()


class FrameDetailDialog(QDialog):
    def __init__(self, table: QTableWidget, row: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Frame Detail")
        self.setMinimumSize(400, 300)
        lay = QVBoxLayout(self)

        can_id = table.item(row, 1).text() if table.item(row, 1) else "?"
        self.setWindowTitle(f"Frame Detail — 0x{can_id}")

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFont(mono_font())

        byte_vals = []
        for i in range(8):
            item = table.item(row, 4 + i)
            if item and item.text() != "--":
                try:
                    byte_vals.append(int(item.text(), 16))
                except ValueError:
                    byte_vals.append(0)
            else:
                byte_vals.append(0)

        lines = [f"ID:        0x{can_id}"]
        ts_item = table.item(row, 0)
        lines.append(f"Timestamp: {ts_item.text() if ts_item else '?'}")
        lines.append("")
        lines.append("Byte  Hex  Dec  Bin")
        lines.append("-" * 30)
        for i, v in enumerate(byte_vals):
            lines.append(f"B{i}    {v:02X}   {v:3d}  {v:08b}")

        lines.append("")
        lines.append(f"Raw: {' '.join(format(v,'02X') for v in byte_vals)}")
        txt.setPlainText("\n".join(lines))
        lay.addWidget(txt)

        btn = QPushButton("Close")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)
