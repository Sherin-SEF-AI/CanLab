"""
TIMELINE tab — Logic-analyzer-style stacked signal view + Video-to-Log sync.

Sub-tabs:
  SIGNAL VIEW  — stacked pyqtgraph rows; shared playhead; click to seek.
  VIDEO SYNC   — embedded video player synced to CAN log timestamps.
                 Scrubbing video moves the signal playhead; clicking a
                 signal spike seeks the video to that moment.
                 An offset slider aligns video t=0 with log t=0.
"""
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QSplitter, QAbstractItemView,
    QTabWidget, QFileDialog, QSlider, QDoubleSpinBox,
)
from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

from theme import COLORS, mono_font
from core.state import get_state

BYTE_COLS = [f"B{i}" for i in range(8)]
MAX_ROWS  = 8


class TimelineTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state      = get_state()
        self._plots: list[pg.PlotItem]      = []
        self._curves: list[pg.PlotDataItem] = []
        self._playhead_lines: list[pg.InfiniteLine] = []

        # Video sync state
        self._video_offset   = 0.0   # seconds: log_time = video_time + offset
        self._log_t0         = 0.0   # first timestamp in loaded log
        self._video_seeking  = False # guard against feedback loops
        self._player         = None
        self._audio_out      = None

        self._build_ui()
        self._state.frames_loaded.connect(self._on_frames_loaded)
        self._state.dbc_updated.connect(self._on_frames_loaded)

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        self._tabs.setFont(mono_font(8))
        outer.addWidget(self._tabs)

        self._tabs.addTab(self._build_signal_tab(), "SIGNAL VIEW")
        self._tabs.addTab(self._build_video_tab(),  "VIDEO SYNC")

    # ── SIGNAL VIEW sub-tab ───────────────────────────────────────────────────

    def _build_signal_tab(self) -> QWidget:
        w        = QWidget()
        outer    = QHBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        ll   = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.addWidget(QLabel("SELECT SIGNALS  (up to 8)", font=mono_font(8)))

        self.sig_list = QListWidget()
        self.sig_list.setFont(mono_font(8))
        self.sig_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        ll.addWidget(self.sig_list)

        self.btn_plot = QPushButton("▶  Plot Selected")
        self.btn_plot.setObjectName("btn_green")
        self.btn_plot.clicked.connect(self._plot_selected)
        ll.addWidget(self.btn_plot)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self._clear_plots)
        ll.addWidget(self.btn_clear)

        self.lbl_status = QLabel("Load frames to begin.", font=mono_font(8))
        self.lbl_status.setStyleSheet(f"color:{COLORS['dim']}")
        ll.addWidget(self.lbl_status)

        splitter.addWidget(left)

        right = QWidget()
        rl    = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground(COLORS["bg"])
        rl.addWidget(self.glw)

        splitter.addWidget(right)
        splitter.setSizes([220, 900])
        outer.addWidget(splitter)
        return w

    # ── VIDEO SYNC sub-tab ────────────────────────────────────────────────────

    def _build_video_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        # ── Top toolbar ──
        toolbar = QHBoxLayout()

        self.btn_load_video = QPushButton("Load Video…")
        self.btn_load_video.clicked.connect(self._load_video)
        toolbar.addWidget(self.btn_load_video)

        self.btn_play_pause = QPushButton("▶ Play")
        self.btn_play_pause.setObjectName("btn_green")
        self.btn_play_pause.clicked.connect(self._toggle_play)
        self.btn_play_pause.setEnabled(False)
        toolbar.addWidget(self.btn_play_pause)

        self.btn_stop_vid = QPushButton("■ Stop")
        self.btn_stop_vid.clicked.connect(self._stop_video)
        self.btn_stop_vid.setEnabled(False)
        toolbar.addWidget(self.btn_stop_vid)

        toolbar.addSpacing(20)
        toolbar.addWidget(QLabel("Time offset (s):", font=mono_font(8)))

        self.spin_offset = QDoubleSpinBox()
        self.spin_offset.setFont(mono_font(8))
        self.spin_offset.setRange(-3600.0, 3600.0)
        self.spin_offset.setSingleStep(0.1)
        self.spin_offset.setDecimals(3)
        self.spin_offset.setToolTip(
            "Shift: log_time = video_time + offset\n"
            "Increase if the video starts before the log."
        )
        self.spin_offset.valueChanged.connect(self._on_offset_changed)
        toolbar.addWidget(self.spin_offset)

        self.lbl_vid_time = QLabel("0.000 s  /  log: —", font=mono_font(8))
        self.lbl_vid_time.setStyleSheet(f"color:{COLORS['green']}")
        toolbar.addWidget(self.lbl_vid_time)

        toolbar.addStretch()
        lay.addLayout(toolbar)

        # ── Video widget ──
        self._video_widget = QVideoWidget()
        self._video_widget.setStyleSheet("background:#000;")
        self._video_widget.setMinimumHeight(320)
        lay.addWidget(self._video_widget, stretch=3)

        # ── Video scrubber ──
        self._vid_scrubber = QSlider(Qt.Orientation.Horizontal)
        self._vid_scrubber.setRange(0, 1000)
        self._vid_scrubber.sliderPressed.connect(self._on_scrubber_pressed)
        self._vid_scrubber.sliderReleased.connect(self._on_scrubber_released)
        lay.addWidget(self._vid_scrubber)

        # ── Help label ──
        help_lbl = QLabel(
            "HOW TO SYNC:  1. Load video  2. Play a few seconds  3. Adjust Time Offset "
            "until the video action matches the CAN spike in SIGNAL VIEW  "
            "4. Click any spike in SIGNAL VIEW — video jumps to that moment",
            font=mono_font(7)
        )
        help_lbl.setStyleSheet(f"color:{COLORS['dim']}")
        help_lbl.setWordWrap(True)
        lay.addWidget(help_lbl)

        # Poll timer — updates scrubber + playhead 10×/s
        self._vid_timer = QTimer()
        self._vid_timer.setInterval(100)
        self._vid_timer.timeout.connect(self._on_vid_tick)

        return w

    # ─────────────────────────────────────────────────────────────────────────
    # Signal list
    # ─────────────────────────────────────────────────────────────────────────

    def _on_frames_loaded(self, *_):
        df   = self._state.frames_df
        sigs = self._state.dbc_signals
        self.sig_list.clear()

        if not df.empty:
            self._log_t0 = float(df["Timestamp"].min())

        for can_id in sorted(df["ID"].unique() if not df.empty else []):
            for col in BYTE_COLS:
                item = QListWidgetItem(f"0x{can_id}  {col}")
                item.setData(Qt.ItemDataRole.UserRole, ("raw", can_id, col))
                item.setFont(mono_font(8))
                self.sig_list.addItem(item)

        for sig in sigs:
            mid  = sig.get("message_id", "000")
            name = sig.get("signal_name", "?")
            item = QListWidgetItem(f"0x{mid}  {name}  [decoded]")
            item.setData(Qt.ItemDataRole.UserRole, ("dbc", mid, name))
            item.setFont(mono_font(8))
            item.setForeground(QColor(COLORS["green"]))
            self.sig_list.addItem(item)

    # ─────────────────────────────────────────────────────────────────────────
    # Signal plot
    # ─────────────────────────────────────────────────────────────────────────

    def _clear_plots(self):
        self.glw.clear()
        self._plots.clear()
        self._curves.clear()
        self._playhead_lines.clear()

    def _plot_selected(self):
        selected = self.sig_list.selectedItems()[:MAX_ROWS]
        if not selected:
            self.lbl_status.setText("Select at least one signal.")
            return
        df = self._state.frames_df
        if df.empty:
            self.lbl_status.setText("No frames loaded.")
            return

        self._clear_plots()
        colors = [
            COLORS["green"], "#00BFFF", "#FF8C00", "#DA70D6",
            "#ADFF2F", "#FF6347", "#40E0D0", "#FFD700",
        ]
        n = len(selected)
        for row_idx, item in enumerate(selected):
            kind, mid, name = item.data(Qt.ItemDataRole.UserRole)
            pen_color = colors[row_idx % len(colors)]

            t, y, label = self._extract_series(df, kind, mid, name)
            if t is None or len(t) == 0:
                continue

            p = self.glw.addPlot(row=row_idx, col=0)
            p.setLabel("left", label, color=pen_color)
            p.getAxis("left").setStyle(tickFont=mono_font(7))
            p.getAxis("bottom").setStyle(tickFont=mono_font(7))
            p.setMouseEnabled(x=True, y=False)

            if row_idx < n - 1:
                p.hideAxis("bottom")
            else:
                p.setLabel("bottom", "Time (s)")

            if row_idx > 0:
                p.setXLink(self._plots[0])

            curve = p.plot(t, y, pen=pg.mkPen(pen_color, width=1), stepMode="left")
            self._plots.append(p)
            self._curves.append(curve)

            vline = pg.InfiniteLine(
                angle=90, movable=False,
                pen=pg.mkPen("#FFFFFF", width=1, style=Qt.PenStyle.DashLine)
            )
            p.addItem(vline)
            self._playhead_lines.append(vline)

            p.scene().sigMouseClicked.connect(self._on_click)

        self.glw.ci.layout.setSpacing(2)
        self.lbl_status.setText(f"Plotting {len(self._plots)} signal(s).")
        self.lbl_status.setStyleSheet(f"color:{COLORS['green']}")

    def _extract_series(self, df, kind, mid, name):
        if kind == "raw":
            frames = df[df["ID"] == mid].sort_values("Timestamp")
            if frames.empty or name not in frames.columns:
                return None, None, name
            return (frames["Timestamp"].values.astype(float),
                    frames[name].fillna(0).values.astype(float),
                    f"0x{mid} {name}")

        if kind == "dbc":
            from core.dbc_manager import decode_frame
            sigs   = [s for s in self._state.dbc_signals
                      if s.get("message_id", "").upper() == mid.upper()]
            frames = df[df["ID"] == mid].sort_values("Timestamp")
            if frames.empty or not sigs:
                return None, None, name
            t_vals, y_vals = [], []
            for _, row in frames.iterrows():
                data    = bytes(int(row.get(f"B{i}", 0) or 0) for i in range(8))
                decoded = decode_frame(sigs, mid, data)
                if name in decoded:
                    t_vals.append(float(row["Timestamp"]))
                    y_vals.append(float(decoded[name]))
            if not t_vals:
                return None, None, name
            return np.array(t_vals), np.array(y_vals), f"0x{mid} {name}"

        return None, None, name

    def _on_click(self, event):
        """Move signal playhead and seek video to clicked timestamp."""
        if not self._plots:
            return
        try:
            pos = event.scenePos()
            vb  = self._plots[0].vb
            t   = vb.mapSceneToView(pos).x()
            for line in self._playhead_lines:
                line.setValue(t)
            self._seek_video_to_log_time(t)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Video player
    # ─────────────────────────────────────────────────────────────────────────

    def _load_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video File", "",
            "Video Files (*.mp4 *.avi *.mkv *.mov *.webm *.m4v);;All Files (*)"
        )
        if not path:
            return

        if self._player is None:
            self._player    = QMediaPlayer()
            self._audio_out = QAudioOutput()
            self._player.setAudioOutput(self._audio_out)
            self._player.setVideoOutput(self._video_widget)
            self._player.durationChanged.connect(self._on_duration_changed)

        self._player.setSource(QUrl.fromLocalFile(path))
        self.btn_play_pause.setEnabled(True)
        self.btn_stop_vid.setEnabled(True)
        self._vid_timer.start()
        self.lbl_vid_time.setText("0.000 s  — loaded, press Play")

    def _toggle_play(self):
        if self._player is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self.btn_play_pause.setText("▶ Play")
        else:
            self._player.play()
            self.btn_play_pause.setText("⏸ Pause")

    def _stop_video(self):
        if self._player:
            self._player.stop()
            self.btn_play_pause.setText("▶ Play")

    def _on_duration_changed(self, duration_ms: int):
        self._vid_scrubber.setRange(0, max(duration_ms, 1))

    def _on_offset_changed(self, value: float):
        self._video_offset = value

    # ── Scrubber interaction ──

    def _on_scrubber_pressed(self):
        if self._player:
            self._player.pause()
            self.btn_play_pause.setText("▶ Play")

    def _on_scrubber_released(self):
        if self._player is None:
            return
        self._video_seeking = True
        pos_ms = self._vid_scrubber.value()
        self._player.setPosition(pos_ms)
        # Move signal playhead to matching log time
        vid_sec  = pos_ms / 1000.0
        log_time = self._log_t0 + vid_sec + self._video_offset
        self._move_playhead(log_time)
        self._video_seeking = False

    # ── Polling tick — sync scrubber and playhead while playing ──

    def _on_vid_tick(self):
        if self._player is None or self._video_seeking:
            return
        pos_ms = self._player.position()
        # Update scrubber without feedback
        self._vid_scrubber.blockSignals(True)
        self._vid_scrubber.setValue(pos_ms)
        self._vid_scrubber.blockSignals(False)

        vid_sec  = pos_ms / 1000.0
        log_time = self._log_t0 + vid_sec + self._video_offset
        self.lbl_vid_time.setText(
            f"video: {vid_sec:.3f} s  |  log: {log_time:.3f} s"
        )

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._move_playhead(log_time)

    # ── Seek video to a log-domain timestamp (called from signal click) ──

    def _seek_video_to_log_time(self, log_time: float):
        if self._player is None:
            return
        vid_sec = log_time - self._log_t0 - self._video_offset
        if vid_sec < 0:
            return
        self._video_seeking = True
        self._player.setPosition(int(vid_sec * 1000))
        self._vid_scrubber.blockSignals(True)
        self._vid_scrubber.setValue(int(vid_sec * 1000))
        self._vid_scrubber.blockSignals(False)
        self._video_seeking = False

    # ── Move signal playhead to a log-domain timestamp ──

    def _move_playhead(self, log_time: float):
        for line in self._playhead_lines:
            line.setValue(log_time)
