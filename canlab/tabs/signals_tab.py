import pandas as pd
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QComboBox, QHeaderView, QFileDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont
from theme import COLORS, mono_font
from core.state import get_state
from core.signal_analyzer import analyze_all

BYTE_COLS = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]

COLUMNS = [
    "ID", "Bus", "Freq(Hz)", "Frames",
    "B0_range","B1_range","B2_range","B3_range",
    "B4_range","B5_range","B6_range","B7_range",
    "Entropy", "SuspectedType", "DBC Status",
]

TYPE_COLORS = {
    "SENSOR":      COLORS["green"],
    "COUNTER":     COLORS["accent"],
    "STATUS_FLAG": COLORS["amber"],
    "DIAGNOSTIC":  COLORS["dim"],
    "UNKNOWN":     COLORS["text"],
}


class AnalyzeWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, df, parent=None):
        super().__init__(parent)
        self.df = df

    def run(self):
        result = analyze_all(self.df)
        self.done.emit(result)


class SignalsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state       = get_state()
        self._signals_df  = pd.DataFrame()
        self._filter_type = "ALL"
        self._build_ui()
        self._state.frames_loaded.connect(lambda _: self._auto_analyze())
        self._state.dbc_updated.connect(self._update_dbc_status)
        self._state.id_selected.connect(self._select_row)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        toolbar = QWidget()
        toolbar.setFixedHeight(28)
        toolbar.setStyleSheet(f"background:{COLORS['panel_bg']}; border-bottom:1px solid {COLORS['border']};")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(4, 2, 4, 2)
        tb_lay.setSpacing(6)

        self.btn_classify = QPushButton("Auto-classify All")
        self.btn_classify.clicked.connect(self._run_classify)
        tb_lay.addWidget(self.btn_classify)

        tb_lay.addWidget(QLabel("Filter:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["ALL", "UNKNOWN", "SENSOR", "COUNTER", "STATUS_FLAG", "DIAGNOSTIC"])
        self.filter_combo.setMaximumWidth(120)
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        tb_lay.addWidget(self.filter_combo)

        tb_lay.addStretch()

        btn_export = QPushButton("Export CSV")
        btn_export.clicked.connect(self._export_csv)
        tb_lay.addWidget(btn_export)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("label_dim")
        tb_lay.addWidget(self.lbl_status)

        lay.addWidget(toolbar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setFont(mono_font())
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setShowGrid(False)
        self.table.clicked.connect(self._on_row_clicked)
        lay.addWidget(self.table)

    def _auto_analyze(self):
        self._run_classify()

    def _run_classify(self):
        df = self._state.frames_df
        if df.empty:
            return
        self.lbl_status.setText("Analyzing...")
        self.btn_classify.setEnabled(False)
        self._worker = AnalyzeWorker(df)
        self._worker.done.connect(self._on_analyze_done)
        self._worker.start()

    def _on_analyze_done(self, result_df):
        self._signals_df = result_df
        self._apply_filter(self._filter_type)
        self.lbl_status.setText(f"Classified {len(result_df)} IDs")
        self.btn_classify.setEnabled(True)

    def _apply_filter(self, type_filter: str):
        self._filter_type = type_filter
        df = self._signals_df
        if df.empty:
            return
        if type_filter != "ALL":
            df = df[df["SuspectedType"] == type_filter]
        self._populate_table(df)

    def _populate_table(self, df: pd.DataFrame):
        self.table.setRowCount(len(df))
        for row_idx, (_, row) in enumerate(df.iterrows()):
            stype  = str(row.get("SuspectedType", "UNKNOWN"))
            color  = TYPE_COLORS.get(stype, COLORS["text"])

            dbc_status = ""
            can_id = str(row.get("ID", ""))
            if any(s.get("message_id","").upper() == can_id.upper()
                   for s in self._state.dbc_signals):
                dbc_status = "OK"

            row_vals = [
                can_id,
                str(row.get("Bus", "")),
                str(row.get("Freq_Hz", "")),
                str(row.get("Frames", "")),
                str(row.get("B0_range", "-")),
                str(row.get("B1_range", "-")),
                str(row.get("B2_range", "-")),
                str(row.get("B3_range", "-")),
                str(row.get("B4_range", "-")),
                str(row.get("B5_range", "-")),
                str(row.get("B6_range", "-")),
                str(row.get("B7_range", "-")),
                str(row.get("Entropy", "")),
                stype,
                dbc_status,
            ]
            for col_idx, val in enumerate(row_vals):
                item = QTableWidgetItem(val)
                item.setFont(mono_font())
                item.setData(Qt.ItemDataRole.UserRole, can_id)
                if col_idx == 13:  # SuspectedType
                    item.setForeground(QBrush(QColor(color)))
                if col_idx == 14 and val == "OK":
                    item.setForeground(QBrush(QColor(COLORS["green"])))
                self.table.setItem(row_idx, col_idx, item)

    def _on_row_clicked(self, index):
        item = self.table.item(index.row(), 0)
        if item:
            self._state.select_id(item.data(Qt.ItemDataRole.UserRole) or item.text())

    def _select_row(self, hex_id: str):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text() == hex_id:
                self.table.selectRow(row)
                self.table.scrollToItem(item)
                return

    def _update_dbc_status(self):
        if not self._signals_df.empty:
            self._apply_filter(self._filter_type)

    def _export_csv(self):
        if self._signals_df.empty:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "signals.csv", "CSV (*.csv)")
        if path:
            self._signals_df.to_csv(path, index=False)
