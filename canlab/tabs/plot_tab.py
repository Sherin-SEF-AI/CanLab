import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
    QPushButton, QFileDialog, QLabel, QMessageBox,
)
from PyQt6.QtCore import Qt
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
        # key -> (PlotItem, DataItem, color, label)
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

        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground(COLORS["bg"])
        self.glw.scene().sigMouseMoved.connect(self._on_mouse_moved)
        right_lay.addWidget(self.glw)

        splitter.addWidget(right)
        splitter.setSizes([180, 820])
        lay.addWidget(splitter)

    # ── Tree ──────────────────────────────────────────────────────────────────

    def _refresh_tree(self):
        df = self._state.frames_df
        if df.empty:
            return

        # During live capture frames_updated fires constantly but the set of IDs
        # rarely changes. Rebuilding the tree every time discarded the user's
        # checkbox selections and thrashed the UI. Only rebuild when the ID set
        # (or the DBC signal set) actually changes, and preserve checks across it.
        current_ids = tuple(sorted(df["ID"].unique()))
        dbc_sig_count = len(self._state.dbc_signals)
        signature = (current_ids, dbc_sig_count)
        if signature == getattr(self, "_tree_signature", None):
            return
        self._tree_signature = signature

        checked = self._checked_keys()

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
            dbc_sigs = [s for s in self._state.dbc_signals
                        if s.get("message_id", "").upper() == can_id.upper()]
            for sig in dbc_sigs:
                sname = sig.get("signal_name", "?")
                child = QTreeWidgetItem([f"[DBC] {sname}"])
                child.setData(0, Qt.ItemDataRole.UserRole, ("dbc", can_id, sig))
                child.setCheckState(0, Qt.CheckState.Unchecked)
                child.setFont(0, mono_font())
                parent.addChild(child)
            self.sig_tree.addTopLevelItem(parent)

        # Restore previously-checked items that still exist after the rebuild.
        if checked:
            self._restore_checks(checked)
        self.sig_tree.blockSignals(False)

    def _iter_tree_items(self):
        root = self.sig_tree.invisibleRootItem()
        for i in range(root.childCount()):
            parent = root.child(i)
            yield parent
            for j in range(parent.childCount()):
                yield parent.child(j)

    def _checked_keys(self) -> set:
        keys = set()
        for item in self._iter_tree_items():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and item.checkState(0) == Qt.CheckState.Checked:
                kind, can_id, detail = data
                keys.add((kind, can_id, str(detail)))
        return keys

    def _restore_checks(self, checked: set):
        for item in self._iter_tree_items():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data:
                continue
            kind, can_id, detail = data
            if (kind, can_id, str(detail)) in checked:
                item.setCheckState(0, Qt.CheckState.Checked)

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

    def _highlight_id(self, hex_id: str):
        root = self.sig_tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if item.text(0) == hex_id:
                self.sig_tree.scrollToItem(item)
                self.sig_tree.setCurrentItem(item)
                return

    # ── Plot management ───────────────────────────────────────────────────────

    def _add_signal(self, key: str, kind: str, can_id: str, detail):
        if key in self._plot_items:
            return
        df = self._state.get_frames_for_id(can_id)
        if df.empty:
            return

        color = SIGNAL_COLORS[self._color_idx % len(SIGNAL_COLORS)]
        self._color_idx += 1
        pen = pg.mkPen(color=color, width=2)

        if kind == "byte" and detail:
            s = df[detail].dropna()
            if s.empty:
                return
            t = df.loc[s.index, "Timestamp"].values.astype(float)
            y = s.values.astype(float)
            label = f"{can_id} {detail}"
        elif kind == "dbc" and detail:
            from core.dbc_manager import decode_frame
            vals, times = [], []
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
            t = np.array(times, dtype=float)
            y = np.array(vals, dtype=float)
            label = f"{can_id} {detail.get('signal_name', '?')}"
        else:
            return

        pi = pg.PlotItem()
        pi.setLabel("left", label, color=color, size="7pt")
        pi.getAxis("left").setTextPen(pg.mkPen(color=COLORS["dim"]))
        pi.getAxis("left").setWidth(55)
        pi.showGrid(x=True, y=True, alpha=0.2)
        pi.hideAxis("bottom")
        pi.setMenuEnabled(False)

        curve = pi.plot(t, y, pen=pen)
        pi.autoRange()

        self._plot_items[key] = (pi, curve, color, label)
        self._rebuild_layout()

    def _remove_signal(self, key: str):
        if key in self._plot_items:
            del self._plot_items[key]
            self._rebuild_layout()

    def _clear_plot(self):
        self._plot_items.clear()
        self.glw.clear()
        self._color_idx = 0
        self.sig_tree.blockSignals(True)
        root = self.sig_tree.invisibleRootItem()
        for i in range(root.childCount()):
            p = root.child(i)
            p.setCheckState(0, Qt.CheckState.Unchecked)
            for j in range(p.childCount()):
                p.child(j).setCheckState(0, Qt.CheckState.Unchecked)
        self.sig_tree.blockSignals(False)

    def _rebuild_layout(self):
        self.glw.clear()
        items = list(self._plot_items.items())
        first_pi = None
        for i, (key, (pi, curve, color, label)) in enumerate(items):
            self.glw.addItem(pi, row=i, col=0)
            if first_pi is None:
                first_pi = pi
                pi.showAxis("bottom")
                pi.setLabel("bottom", "Time (s)", color=COLORS["dim"], size="7pt")
                pi.getAxis("bottom").setTextPen(pg.mkPen(color=COLORS["dim"]))
            else:
                pi.hideAxis("bottom")
                pi.setXLink(first_pi)
            self.glw.ci.layout.setRowStretchFactor(i, 1)

    # ── Live update ───────────────────────────────────────────────────────────

    def _on_live_toggled(self, checked: bool):
        self._live_enabled = checked
        self.btn_live.setText("LIVE: ON" if checked else "LIVE: OFF")

    def _on_dbc_db_updated(self):
        self._refresh_tree()

    def _live_update(self):
        if not self._live_enabled or not self._plot_items:
            return
        db = self._state.dbc_db
        for key, (pi, curve, color, label) in list(self._plot_items.items()):
            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            can_id, detail = parts[0], parts[1]
            df = self._state.get_frames_for_id(can_id)
            if df.empty:
                continue
            df = df.tail(500)
            if detail.startswith("B") and detail[1:].isdigit():
                s = df[detail].dropna() if detail in df.columns else None
                if s is None or s.empty:
                    continue
                t = df.loc[s.index, "Timestamp"].values.astype(float)
                y = s.values.astype(float)
                curve.setData(t, y)
                pi.autoRange()
            elif db is not None:
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
                    pi.autoRange()

    # ── Mouse / export ────────────────────────────────────────────────────────

    def _on_mouse_moved(self, pos):
        # Report data coordinates for whichever sub-plot the cursor is over.
        for entry in self._plot_items.values():
            pi = entry[0]
            if pi.sceneBoundingRect().contains(pos):
                pt = pi.getViewBox().mapSceneToView(pos)
                self.lbl_cursor.setText(f"x: {pt.x():.3f}  y: {pt.y():.3f}")
                return
        self.lbl_cursor.setText("x: — y: —")

    def _screenshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Screenshot", "plot.png", "PNG (*.png)")
        if not path:
            return
        try:
            import pyqtgraph.exporters as pg_exporters   # not auto-imported by pyqtgraph
            exporter = pg_exporters.ImageExporter(self.glw.scene())
            exporter.export(path)
        except Exception as e:
            QMessageBox.warning(self, "Screenshot", f"Could not save screenshot: {e}")

    def add_event_markers(self, events: list[dict]):
        for pi, curve, color, label in self._plot_items.values():
            for evt in events:
                ts = evt.get("timestamp", 0)
                line = pg.InfiniteLine(
                    pos=ts, angle=90, movable=False,
                    pen=pg.mkPen(color=COLORS["amber"], width=1, style=Qt.PenStyle.DashLine),
                    label=evt.get("event", ""),
                    labelOpts={"color": COLORS["amber"], "position": 0.9},
                )
                pi.addItem(line)
