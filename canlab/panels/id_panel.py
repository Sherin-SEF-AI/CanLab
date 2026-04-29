from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
    QListWidget, QListWidgetItem, QMenu, QSplitter,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont
import pandas as pd
from theme import COLORS, mono_font
from core.state import get_state


class IDPanel(QWidget):
    analyze_requested = pyqtSignal(str)
    plot_requested    = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self._state = get_state()
        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(2)

        # Sources section
        src_widget = QWidget()
        src_lay = QVBoxLayout(src_widget)
        src_lay.setContentsMargins(4, 4, 4, 4)
        src_lay.setSpacing(2)
        lbl_src = QLabel("LOADED SOURCES")
        lbl_src.setObjectName("label_dim")
        lbl_src.setFont(mono_font(8))
        src_lay.addWidget(lbl_src)
        self.source_list = QListWidget()
        self.source_list.setMaximumHeight(100)
        src_lay.addWidget(self.source_list)
        splitter.addWidget(src_widget)

        # ID tree section
        id_widget = QWidget()
        id_lay = QVBoxLayout(id_widget)
        id_lay.setContentsMargins(4, 4, 4, 4)
        id_lay.setSpacing(2)
        lbl_id = QLabel("CAN ID LIST")
        lbl_id.setObjectName("label_dim")
        lbl_id.setFont(mono_font(8))
        id_lay.addWidget(lbl_id)
        self.id_tree = QTreeWidget()
        self.id_tree.setHeaderLabels(["ID", "Hz", "Cnt"])
        self.id_tree.setColumnWidth(0, 70)
        self.id_tree.setColumnWidth(1, 50)
        self.id_tree.setColumnWidth(2, 50)
        self.id_tree.setFont(mono_font())
        self.id_tree.setRootIsDecorated(True)
        self.id_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.id_tree.customContextMenuRequested.connect(self._show_context_menu)
        id_lay.addWidget(self.id_tree)
        splitter.addWidget(id_widget)

        lay.addWidget(splitter)

    def _connect_signals(self):
        self._state.source_added.connect(self._add_source)
        self._state.frames_updated.connect(self._refresh_ids)
        self._state.signal_analyzed.connect(self._mark_analyzed)
        self.id_tree.itemClicked.connect(self._on_item_clicked)

    def _add_source(self, name: str, count: int):
        item = QListWidgetItem(f"{name}  [{count}]")
        item.setForeground(QBrush(QColor(COLORS["green"])))
        self.source_list.addItem(item)

    def _refresh_ids(self):
        df = self._state.frames_df
        if df.empty:
            return
        self.id_tree.clear()
        buses = df["Bus"].unique() if "Bus" in df.columns else ["0"]
        total_time = df["Timestamp"].iloc[-1] - df["Timestamp"].iloc[0] if len(df) > 1 else 1

        for bus in sorted(buses, key=str):
            bus_item = QTreeWidgetItem([f"BUS {bus}", "", ""])
            bus_item.setForeground(0, QBrush(QColor(COLORS["dim"])))
            bus_item.setFont(0, mono_font(8))
            self.id_tree.addTopLevelItem(bus_item)

            bus_df = df[df["Bus"] == bus] if "Bus" in df.columns else df
            for can_id in sorted(bus_df["ID"].unique()):
                id_frames = bus_df[bus_df["ID"] == can_id]
                count = len(id_frames)
                freq  = count / total_time if total_time > 0 else 0

                child = QTreeWidgetItem([
                    can_id,
                    f"{freq:.1f}",
                    str(count),
                ])
                child.setFont(0, mono_font())
                child.setData(0, Qt.ItemDataRole.UserRole, can_id)
                child.setToolTip(0, f"ID: 0x{can_id}  ({int(can_id,16)})")

                if freq > 50:
                    child.setForeground(0, QBrush(QColor(COLORS["green"])))
                elif freq >= 1:
                    child.setForeground(0, QBrush(QColor(COLORS["text"])))
                else:
                    child.setForeground(0, QBrush(QColor(COLORS["dim"])))

                bus_item.addChild(child)

            bus_item.setExpanded(True)

    def _on_item_clicked(self, item: QTreeWidgetItem, col: int):
        can_id = item.data(0, Qt.ItemDataRole.UserRole)
        if can_id:
            self._state.select_id(can_id)

    def _mark_analyzed(self, hex_id: str):
        root = self.id_tree.invisibleRootItem()
        for i in range(root.childCount()):
            bus_item = root.child(i)
            for j in range(bus_item.childCount()):
                child = bus_item.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == hex_id:
                    child.setForeground(1, QBrush(QColor(COLORS["amber"])))
                    return

    def _show_context_menu(self, pos):
        item = self.id_tree.itemAt(pos)
        if not item:
            return
        can_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not can_id:
            return

        menu = QMenu(self)
        menu.addAction("Analyze with AI",   lambda: self.analyze_requested.emit(can_id))
        menu.addAction("Plot Signal",        lambda: self.plot_requested.emit(can_id))
        menu.addAction("Select ID",          lambda: self._state.select_id(can_id))
        menu.addSeparator()
        menu.addAction("Copy ID",            lambda: self._copy_id(can_id))
        menu.exec(self.id_tree.mapToGlobal(pos))

    def _copy_id(self, can_id: str):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(f"0x{can_id}")
