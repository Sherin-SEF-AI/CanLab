import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
    QPushButton, QFileDialog, QLabel,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from theme import COLORS, mono_font
from core.state import get_state

pg.setConfigOption("background", COLORS["bg"])
pg.setConfigOption("foreground", COLORS["text"])

SIGNAL_COLORS = ["#00ff88", "#ffb300", "#00aaff", "#ff6b6b", "#cc88ff",
                 "#ff9944", "#44ffcc", "#ff44aa"]
BYTE_COLS = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]


class PlotTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state        = get_state()
        self._color_idx    = 0
        self._plot_items: dict = {}
        self._live_enabled = False
        self._build_ui()
        self._state.frames_updated.connect(self._refresh_tree)
        self._state.frames_updated.connect(self._live_update)
        self._state.id_selected.connect(self._highlight_id)
        self._state.dbc_db_updated.connect(self._on_dbc_db_updated)

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Signal selector
        left = QWidget()
        left.setFixedWidth(180)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(4, 4, 4, 4)
        left_lay.setSpacing(4)

        lbl = QLabel("SIGNALS")
        lbl.setObjectName("label_dim")
        lbl.setFont(mono_font(8))
        left_lay.addWidget(lbl)

        self.sig_tree = QTreeWidget()
        self.sig_tree.setHeaderHidden(True)
        self.sig_tree.setFont(mono_font())
        self.sig_tree.itemChanged.connect(self._on_item_checked)
        left_lay.addWidget(self.sig_tree)

        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self._clear_plot)
        left_lay.addWidget(btn_clear)

        self.btn_live = QPushButton("LIVE: OFF")
        self.btn_live.setCheckable(True)
        self.btn_live.setFont(mono_font(8))
        self.btn_live.setStyleSheet(
            f"QPushButton {{ color:{COLORS['dim']}; border:1px solid {COLORS['border']}; }}"
            f"QPushButton:checked {{ color:{COLORS['green']}; border:1px solid {COLORS['green']}; }}"
        )
        self.btn_live.toggled.connect(self._on_live_toggled)
        left_lay.addWidget(self.btn_live)

        splitter.addWidget(left)

        # Plot area
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        toolbar = QWidget()
        toolbar.setFixedHeight(28)
        toolbar.setStyleSheet(f"background:{COLORS['panel_bg']};border-bottom:1px solid {COLORS['border']};")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(4, 2, 4, 2)
        tb.setSpacing(6)
        self.lbl_cursor = QLabel("x: — y: —")
        self.lbl_cursor.setObjectName("label_dim")
        self.lbl_cursor.setFont(mono_font(8))
        tb.addWidget(self.lbl_cursor)
        tb.addStretch()
        btn_shot = QPushButton("Screenshot")
        btn_shot.clicked.connect(self._screenshot)
        btn_shot.setMaximumWidth(90)
        tb.addWidget(btn_shot)
        right_lay.addWidget(toolbar)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground(COLORS["bg"])
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.addLegend(offset=(10, 10))
        self.plot_widget.setLabel("bottom", "Time (s)",
                                  color=COLORS["dim"], size="8pt")
        self.plot_widget.setLabel("left", "Value",
                                  color=COLORS["dim"], size="8pt")
        self.plot_widget.getAxis("bottom").setTextPen(pg.mkPen(color=COLORS["dim"]))
        self.plot_widget.getAxis("left").setTextPen(pg.mkPen(color=COLORS["dim"]))
        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

        # Crosshair
        self._vline = pg.InfiniteLine(angle=90, movable=False,
                                       pen=pg.mkPen(color=COLORS["dim"], width=1, style=Qt.PenStyle.DotLine))
        self._hline = pg.InfiniteLine(angle=0, movable=False,
                                       pen=pg.mkPen(color=COLORS["dim"], width=1, style=Qt.PenStyle.DotLine))
        self.plot_widget.addItem(self._vline, ignoreBounds=True)
        self.plot_widget.addItem(self._hline, ignoreBounds=True)

        right_lay.addWidget(self.plot_widget)
        splitter.addWidget(right)
        splitter.setSizes([180, 820])

        lay.addWidget(splitter)

    def _refresh_tree(self):
        df = self._state.frames_df
        if df.empty:
            return
        self.sig_tree.blockSignals(True)
        self.sig_tree.clear()
        for can_id in sorted(df["ID"].unique()):
            parent = QTreeWidgetItem([can_id])
            parent.setData(0, Qt.ItemDataRole.UserRole, ("id", can_id, None))
            parent.setCheckState(0, Qt.CheckState.Unchecked)
            parent.setFont(0, mono_font())
            for col in BYTE_COLS:
                id_df = df[df["ID"] == can_id]
                if col not in id_df.columns or id_df[col].dropna().empty:
                    continue
                child = QTreeWidgetItem([col])
                child.setData(0, Qt.ItemDataRole.UserRole, ("byte", can_id, col))
                child.setCheckState(0, Qt.CheckState.Unchecked)
                child.setFont(0, mono_font())
                parent.addChild(child)
            # DBC signals
            dbc_sigs = [s for s in self._state.dbc_signals
                        if s.get("message_id","").upper() == can_id.upper()]
            for sig in dbc_sigs:
                sname = sig.get("signal_name", "?")
                child = QTreeWidgetItem([f"[DBC] {sname}"])
                child.setData(0, Qt.ItemDataRole.UserRole, ("dbc", can_id, sig))
                child.setCheckState(0, Qt.CheckState.Unchecked)
                child.setFont(0, mono_font())
                parent.addChild(child)
            self.sig_tree.addTopLevelItem(parent)
        self.sig_tree.blockSignals(False)

    def _on_item_checked(self, item: QTreeWidgetItem, col: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, can_id, detail = data
        key = f"{can_id}:{detail}"
        if item.checkState(0) == Qt.CheckState.Checked:
            self._add_signal(key, kind, can_id, detail)
        else:
            self._remove_signal(key)

    def _add_signal(self, key: str, kind: str, can_id: str, detail):
        if key in self._plot_items:
            return
        df = self._state.get_frames_for_id(can_id)
        if df.empty:
            return

        color = SIGNAL_COLORS[self._color_idx % len(SIGNAL_COLORS)]
        self._color_idx += 1
        pen = pg.mkPen(color=color, width=1.5)

        if kind == "byte" and detail:
            s = df[detail].dropna()
            t = df.loc[s.index, "Timestamp"].values
            y = s.values
            label = f"{can_id}.{detail}"
        elif kind == "dbc" and detail:
            from core.dbc_manager import decode_frame
            vals = []
            times = []
            for _, row in df.iterrows():
                byte_data = bytes(
                    int(row[f"B{i}"]) if pd.notna(row.get(f"B{i}")) else 0
                    for i in range(8)
                )
                decoded = decode_frame([detail], can_id, byte_data)
                sname = detail.get("signal_name", "")
                if sname in decoded:
                    vals.append(float(decoded[sname]))
                    times.append(row["Timestamp"])
            if not vals:
                return
            t = np.array(times)
            y = np.array(vals)
            label = f"{can_id}.{detail.get('signal_name','?')}"
        else:
            return

        curve = self.plot_widget.plot(t, y, pen=pen, name=label)
        self._plot_items[key] = curve

    def _remove_signal(self, key: str):
        if key in self._plot_items:
            self.plot_widget.removeItem(self._plot_items.pop(key))

    def _clear_plot(self):
        for key in list(self._plot_items.keys()):
            self._remove_signal(key)
        self.sig_tree.blockSignals(True)
        root = self.sig_tree.invisibleRootItem()
        for i in range(root.childCount()):
            p = root.child(i)
            p.setCheckState(0, Qt.CheckState.Unchecked)
            for j in range(p.childCount()):
                p.child(j).setCheckState(0, Qt.CheckState.Unchecked)
        self.sig_tree.blockSignals(False)
        self._color_idx = 0

    def _highlight_id(self, hex_id: str):
        root = self.sig_tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if item.text(0) == hex_id:
                self.sig_tree.scrollToItem(item)
                self.sig_tree.setCurrentItem(item)
                return

    def _on_mouse_moved(self, pos):
        vb = self.plot_widget.getViewBox()
        if self.plot_widget.sceneBoundingRect().contains(pos):
            mp = vb.mapSceneToView(pos)
            self._vline.setPos(mp.x())
            self._hline.setPos(mp.y())
            self.lbl_cursor.setText(f"x: {mp.x():.3f}s  y: {mp.y():.2f}")

    def _screenshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Screenshot", "plot.png", "PNG (*.png)")
        if path:
            exporter = pg.exporters.ImageExporter(self.plot_widget.plotItem)
            exporter.export(path)

    # ── LIVE mode ─────────────────────────────────────────────────────────────

    def _on_live_toggled(self, checked: bool):
        self._live_enabled = checked
        self.btn_live.setText("LIVE: ON" if checked else "LIVE: OFF")

    def _on_dbc_db_updated(self):
        """Rebuild DBC signal children when DB cache is refreshed."""
        self._refresh_tree()

    def _live_update(self):
        """
        Called on every frames_updated. Refreshes all currently plotted items
        using the cached dbc_db for speed. Only active in LIVE mode.
        """
        if not self._live_enabled or not self._plot_items:
            return
        db = self._state.dbc_db
        for key, curve in list(self._plot_items.items()):
            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            can_id, detail = parts[0], parts[1]
            df = self._state.get_frames_for_id(can_id)
            if df.empty:
                continue
            # Keep only the last 500 frames for live display
            df = df.tail(500)
            if detail.startswith("B") and detail[1:].isdigit():
                s = df[detail].dropna() if detail in df.columns else None
                if s is None or s.empty:
                    continue
                t = df.loc[s.index, "Timestamp"].values.astype(float)
                y = s.values.astype(float)
                curve.setData(t, y)
            elif db is not None:
                # DBC decoded — use cached DB
                t_vals, y_vals = [], []
                msg_id_int = int(can_id, 16)
                for _, row in df.iterrows():
                    try:
                        raw = bytes(int(row.get(f"B{i}", 0) or 0) for i in range(8))
                        decoded = db.decode_message(msg_id_int, raw)
                        if detail in decoded:
                            t_vals.append(float(row["Timestamp"]))
                            y_vals.append(float(decoded[detail]))
                    except Exception:
                        pass
                if t_vals:
                    curve.setData(np.array(t_vals), np.array(y_vals))

    def add_event_markers(self, events: list[dict]):
        for evt in events:
            ts = evt.get("timestamp", 0)
            line = pg.InfiniteLine(
                pos=ts, angle=90, movable=False,
                pen=pg.mkPen(color=COLORS["amber"], width=1, style=Qt.PenStyle.DashLine),
                label=evt.get("event", ""),
                labelOpts={"color": COLORS["amber"], "position": 0.9},
            )
            self.plot_widget.addItem(line)
