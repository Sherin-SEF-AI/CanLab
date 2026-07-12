"""DASHBOARD tab — 3D correlation heatmap, message timeline, physical overlay."""
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QPushButton, QLabel,
    QTabWidget, QGroupBox, QComboBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from theme import COLORS, mono_font
from core.state import get_state

BYTE_COLS = [f"B{i}" for i in range(8)]


# ── Physical Overlay Widgets ─────────────────────────────────────────────────

class SteeringWheelWidget(QWidget):
    """Simple steering wheel drawn with QPainter; rotates with angle input."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0.0
        self.setMinimumSize(160, 160)

    def set_angle(self, degrees: float):
        self._angle = degrees
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 8

        p.translate(cx, cy)
        p.rotate(self._angle)

        # Outer ring
        p.setPen(QPen(QColor(COLORS["green"]), 4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(-r, -r, 2*r, 2*r)

        # Spokes
        p.setPen(QPen(QColor(COLORS["green"]), 2))
        for angle in [0, 120, 240]:
            import math
            rad = math.radians(angle)
            x1 = int(r * 0.3 * math.cos(rad))
            y1 = int(r * 0.3 * math.sin(rad))
            x2 = int(r * 0.9 * math.cos(rad))
            y2 = int(r * 0.9 * math.sin(rad))
            p.drawLine(x1, y1, x2, y2)

        # Center hub
        p.setBrush(QBrush(QColor(COLORS["amber"])))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(-12, -12, 24, 24)
        p.end()


class SpeedGaugeWidget(QWidget):
    """Simple arc gauge for speed."""
    def __init__(self, max_val: float = 220.0, label: str = "km/h", parent=None):
        super().__init__(parent)
        self._value   = 0.0
        self._max     = max_val
        self._label   = label
        self.setMinimumSize(160, 100)

    def set_value(self, v: float):
        self._value = max(0, min(v, self._max))
        self.update()

    def paintEvent(self, event):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx = w // 2
        cy = int(h * 0.85)
        r  = min(w, h) - 20

        # Background arc
        p.setPen(QPen(QColor(COLORS["border"]), 6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(cx - r, cy - r, 2*r, 2*r, 0 * 16, 180 * 16)

        # Value arc
        span_deg = 180.0 * (self._value / self._max)
        p.setPen(QPen(QColor(COLORS["green"]), 6))
        p.drawArc(cx - r, cy - r, 2*r, 2*r, 0 * 16, int(span_deg * 16))

        # Text
        p.setPen(QColor(COLORS["text"]))
        p.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
        p.drawText(cx - 40, cy - 10, f"{self._value:.1f}")
        p.setFont(QFont("Courier New", 8))
        p.drawText(cx - 15, cy + 5, self._label)
        p.end()


# ── Dashboard Tab ─────────────────────────────────────────────────────────────

class DashboardTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = get_state()
        self._build_ui()
        self._live_timer = QTimer()
        self._live_timer.setInterval(250)
        self._live_timer.timeout.connect(self._update_live_overlays)
        self._state.frames_loaded.connect(self._on_frames_loaded)
        self._state.frames_updated.connect(self._on_frames_updated)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.addTab(self._build_heatmap_tab(),  "3D HEATMAP")
        tabs.addTab(self._build_timeline_tab(), "TIMELINE")
        tabs.addTab(self._build_overlay_tab(),  "PHYSICAL OVERLAY")
        outer.addWidget(tabs)

    # ── Correlation Heatmap ───────────────────────────────────────────────────

    def _build_heatmap_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("BYTE-VALUE HEATMAP  (normalised mean byte value per ID)", font=mono_font(9)))
        hdr.addStretch()
        self.btn_render_heatmap = QPushButton("Render")
        self.btn_render_heatmap.clicked.connect(self._render_heatmap)
        hdr.addWidget(self.btn_render_heatmap)
        lay.addLayout(hdr)

        self._heatmap_fig = Figure(facecolor="#0d0d0d")
        self._heatmap_canvas = FigureCanvas(self._heatmap_fig)
        self._heatmap_canvas.setStyleSheet("background:#0d0d0d;")
        lay.addWidget(self._heatmap_canvas)

        self.lbl_heatmap_info = QLabel("")
        self.lbl_heatmap_info.setFont(mono_font(8))
        self.lbl_heatmap_info.setObjectName("label_dim")
        lay.addWidget(self.lbl_heatmap_info)
        return w

    def _render_heatmap(self):
        if self._state.frames_df.empty:
            return
        ids = self._state.get_unique_ids()
        if not ids:
            return

        # Build matrix: rows=IDs, cols=bytes, value=normalised mean
        matrix = []
        for can_id in ids:
            grp = self._state.get_frames_for_id(can_id)
            row = []
            for col in BYTE_COLS:
                vals = grp[col].dropna() if col in grp.columns else []
                row.append(float(vals.mean()) if len(vals) else 0.0)
            matrix.append(row)

        arr = np.array(matrix, dtype=np.float32)   # (n_ids, 8)
        row_max = arr.max(axis=1, keepdims=True)
        row_max[row_max == 0] = 1
        arr = arr / row_max

        self._heatmap_fig.clear()
        ax = self._heatmap_fig.add_subplot(111)
        ax.set_facecolor("#0d0d0d")
        self._heatmap_fig.patch.set_facecolor("#0d0d0d")

        im = ax.imshow(arr, aspect="auto", cmap="plasma", vmin=0, vmax=1,
                       interpolation="nearest")
        cbar = self._heatmap_fig.colorbar(im, ax=ax)
        cbar.ax.yaxis.set_tick_params(color="#00ff99")
        cbar.outline.set_edgecolor("#00ff99")
        plt_ticks = [t for t in cbar.ax.get_yticks()]
        cbar.ax.set_yticklabels(
            [f"{t:.1f}" for t in plt_ticks],
            color="#00ff99", fontsize=7
        )

        ax.set_xticks(range(8))
        ax.set_xticklabels([f"B{i}" for i in range(8)], color="#00ff99", fontsize=8)
        ax.set_yticks(range(len(ids)))
        ax.set_yticklabels(
            [f"0x{i}" for i in ids],
            color="#00ff99", fontsize=7,
            fontfamily="monospace"
        )
        ax.set_xlabel("Byte", color="#00ff99", fontsize=8)
        ax.set_ylabel("CAN ID", color="#00ff99", fontsize=8)
        ax.tick_params(colors="#00ff99")
        for spine in ax.spines.values():
            spine.set_edgecolor("#333333")

        self._heatmap_canvas.draw()
        self.lbl_heatmap_info.setText(
            f"{len(ids)} IDs × 8 bytes  |  plasma colormap  |  normalised per-row mean"
        )

    # ── Timeline ──────────────────────────────────────────────────────────────

    def _build_timeline_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("MESSAGE TIMELINE  (frame density per ID over time)", font=mono_font(9)))
        hdr.addStretch()
        self.btn_render_timeline = QPushButton("Render")
        self.btn_render_timeline.clicked.connect(self._render_timeline)
        hdr.addWidget(self.btn_render_timeline)
        lay.addLayout(hdr)

        self.timeline_plot = pg.PlotWidget()
        self.timeline_plot.setBackground(COLORS["bg"])
        self.timeline_plot.setLabel("left",   "ID index")
        self.timeline_plot.setLabel("bottom", "Time (s)")
        lay.addWidget(self.timeline_plot)
        return w

    def _render_timeline(self):
        if self._state.frames_df.empty:
            return
        ids = self._state.get_unique_ids()
        if not ids:
            return
        self.timeline_plot.clear()
        id_index = {can_id: i for i, can_id in enumerate(ids)}
        colors = pg.colormap.get("CET-C1").getLookupTable(nPts=len(ids))

        df = self._state.frames_df
        for can_id in ids:
            grp = df[df["ID"] == can_id]
            ts  = grp["Timestamp"].values
            if len(ts) == 0:
                continue
            ts = ts - df["Timestamp"].min()
            y  = np.full(len(ts), id_index[can_id], dtype=float)
            idx = id_index[can_id]
            color = tuple(int(c) for c in colors[idx % len(colors)])
            self.timeline_plot.plot(
                ts, y,
                pen=None,
                symbol="s",
                symbolSize=3,
                symbolBrush=pg.mkBrush(*color[:3]),
            )

    # ── Physical Overlay ──────────────────────────────────────────────────────

    def _build_overlay_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("PHYSICAL OVERLAY  (live signal → gauge/wheel)", font=mono_font(9)))
        hdr.addStretch()
        self.btn_overlay_live = QPushButton("Start Live")
        self.btn_overlay_live.setObjectName("btn_green")
        self.btn_overlay_live.clicked.connect(self._toggle_overlay_live)
        hdr.addWidget(self.btn_overlay_live)
        lay.addLayout(hdr)

        # Mapping combos
        map_grp = QGroupBox("SIGNAL MAPPINGS")
        mg = QHBoxLayout(map_grp)
        mg.addWidget(QLabel("Steering angle ID:"))
        self.overlay_steer_combo = QComboBox()
        mg.addWidget(self.overlay_steer_combo)
        mg.addWidget(QLabel("Speed ID:"))
        self.overlay_speed_combo = QComboBox()
        mg.addWidget(self.overlay_speed_combo)
        mg.addStretch()
        lay.addWidget(map_grp)

        # Gauges
        gauges = QHBoxLayout()
        steer_grp = QGroupBox("STEERING")
        sg = QVBoxLayout(steer_grp)
        self.steering_widget = SteeringWheelWidget()
        self.lbl_steer_val = QLabel("0.0°")
        self.lbl_steer_val.setFont(mono_font(10, bold=True))
        self.lbl_steer_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sg.addWidget(self.steering_widget)
        sg.addWidget(self.lbl_steer_val)
        gauges.addWidget(steer_grp)

        speed_grp = QGroupBox("SPEED")
        spg = QVBoxLayout(speed_grp)
        self.speed_gauge = SpeedGaugeWidget(220.0, "km/h")
        self.lbl_speed_val = QLabel("0.0 km/h")
        self.lbl_speed_val.setFont(mono_font(10, bold=True))
        self.lbl_speed_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spg.addWidget(self.speed_gauge)
        spg.addWidget(self.lbl_speed_val)
        gauges.addWidget(speed_grp)

        lay.addLayout(gauges)
        lay.addStretch()

        self._overlay_live = False
        self._refresh_overlay_combos()
        return w

    def _refresh_overlay_combos(self):
        ids = self._state.get_unique_ids()
        for combo in [self.overlay_steer_combo, self.overlay_speed_combo]:
            combo.clear()
            combo.addItem("(none)", "")
            for can_id in ids:
                combo.addItem(f"0x{can_id}", can_id)
        # Auto-select known IDs
        steer_idx = self.overlay_steer_combo.findData("260")
        if steer_idx >= 0:
            self.overlay_steer_combo.setCurrentIndex(steer_idx)
        speed_idx = self.overlay_speed_combo.findData("544")
        if speed_idx >= 0:
            self.overlay_speed_combo.setCurrentIndex(speed_idx)

    def _toggle_overlay_live(self):
        self._overlay_live = not self._overlay_live
        if self._overlay_live:
            self.btn_overlay_live.setText("Stop Live")
            self._live_timer.start()
        else:
            self.btn_overlay_live.setText("Start Live")
            self._live_timer.stop()

    def _update_live_overlays(self):
        df = self._state.frames_df
        if df.empty:
            return

        # Steering
        steer_id = self.overlay_steer_combo.currentData()
        if steer_id:
            grp = df[df["ID"] == steer_id]
            if not grp.empty:
                last = grp.iloc[-1]
                # SAS11: B0 + B1 = 11-bit signed angle, scale 0.1 deg
                b0 = int(last.get("B0", 0) or 0)
                b1 = int(last.get("B1", 0) or 0)
                raw = (b0 | ((b1 & 0x07) << 8))
                if raw > 1023:
                    raw -= 2048
                angle = raw * 0.1
                self.steering_widget.set_angle(angle)
                self.lbl_steer_val.setText(f"{angle:.1f}°")

        # Speed
        speed_id = self.overlay_speed_combo.currentData()
        if speed_id:
            grp = df[df["ID"] == speed_id]
            if not grp.empty:
                last = grp.iloc[-1]
                b2 = int(last.get("B2", 0) or 0)
                b3 = int(last.get("B3", 0) or 0)
                speed = ((b2 | (b3 << 8)) & 0x1FFF) * 0.03125
                self.speed_gauge.set_value(speed)
                self.lbl_speed_val.setText(f"{speed:.1f} km/h")

    # ── State handlers ────────────────────────────────────────────────────────

    def _on_frames_loaded(self, count: int):
        self._refresh_overlay_combos()

    def _on_frames_updated(self):
        pass
