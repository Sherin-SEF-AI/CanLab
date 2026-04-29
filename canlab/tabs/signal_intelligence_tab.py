"""
Signal Intelligence Tab — 6 sub-tabs of ML-powered CAN analysis.

  BYTE ROLES   — classify each byte's role (COUNTER/CHECKSUM/BOOLEAN/PHYSICAL/PADDING)
  CHECKSUM RE  — brute-force checksum algorithm identification
  CORRELATION  — cross-ID Pearson correlation sweep
  CHANGE DETECT— find which bytes changed near a given timestamp
  ANOMALY      — baseline fitting + live frame anomaly scoring
  FIND SIMILAR — cosine-similarity search across all IDs in the log

All heavy computation runs in QThread workers so the UI stays responsive.
"""

import pandas as pd
import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QTabWidget,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QDoubleSpinBox, QSpinBox, QSlider, QCheckBox, QLineEdit,
    QGroupBox, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QBrush, QFont

from theme import COLORS, mono_font
from core.state import get_state
from core.signal_classifier import ROLE_COLORS


# ── Background workers ────────────────────────────────────────────────────────

class ClassifyWorker(QThread):
    """Classify all bytes for a single CAN ID."""
    result_ready = pyqtSignal(str, dict, dict)   # id, roles, msg_type
    error        = pyqtSignal(str)

    def __init__(self, can_id: str, frames_df: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._id       = can_id
        self._frames   = frames_df

    def run(self):
        try:
            from core.signal_classifier import classify_frame, classify_message_type
            roles    = classify_frame(self._frames, self._id)
            msg_type = classify_message_type(self._frames)
            self.result_ready.emit(self._id, roles, msg_type)
        except Exception as e:
            self.error.emit(str(e))


class ChecksumWorker(QThread):
    """Run high-accuracy checksum guesser for a single CAN ID."""
    result_ready = pyqtSignal(str, dict)   # id, {byte_idx: [matches]}
    error        = pyqtSignal(str)

    def __init__(self, can_id: str, frames_df: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._id     = can_id
        self._frames = frames_df

    def run(self):
        try:
            from core.checksum_guesser import guess_all_bytes
            results = guess_all_bytes(self._frames, self._id)
            self.result_ready.emit(self._id, results)
        except Exception as e:
            self.error.emit(str(e))


class CorrelationWorker(QThread):
    """Cross-ID correlation sweep."""
    result_ready = pyqtSignal(list)
    progress     = pyqtSignal(int, int)
    error        = pyqtSignal(str)

    def __init__(self, frames_df: pd.DataFrame,
                 min_r: float, max_pairs: int,
                 find_lag: bool, parent=None):
        super().__init__(parent)
        self._frames    = frames_df
        self._min_r     = min_r
        self._max_pairs = max_pairs
        self._find_lag  = find_lag

    def run(self):
        try:
            from core.correlation_engine import run_correlation_sweep
            results = run_correlation_sweep(
                self._frames,
                min_r=self._min_r,
                max_id_pairs=self._max_pairs,
                find_lag=self._find_lag,
                progress_cb=lambda d, t: self.progress.emit(d, t),
            )
            self.result_ready.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class ChangeWorker(QThread):
    """Find bytes that changed near a timestamp."""
    result_ready = pyqtSignal(list)
    error        = pyqtSignal(str)

    def __init__(self, frames_df: pd.DataFrame, timestamp: float,
                 before_s: float, after_s: float, parent=None):
        super().__init__(parent)
        self._frames    = frames_df
        self._ts        = timestamp
        self._before    = before_s
        self._after     = after_s

    def run(self):
        try:
            from core.signal_classifier import find_changes_at_timestamp
            results = find_changes_at_timestamp(
                self._frames, self._ts,
                window_before_s=self._before,
                window_after_s=self._after,
            )
            self.result_ready.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class AnomalyFitWorker(QThread):
    """Fit anomaly baseline."""
    finished = pyqtSignal(object, str)   # detector, backend_name
    error    = pyqtSignal(str)

    def __init__(self, frames_df: pd.DataFrame,
                 use_iforest: bool, parent=None):
        super().__init__(parent)
        self._frames      = frames_df
        self._use_iforest = use_iforest

    def run(self):
        try:
            from core.anomaly_detector import fit_baseline
            det  = fit_baseline(self._frames, self._use_iforest)
            name = type(det).__name__
            self.finished.emit(det, name)
        except Exception as e:
            self.error.emit(str(e))


class AnomalyScoreWorker(QThread):
    """Score all frames against a fitted baseline."""
    result_ready = pyqtSignal(object)   # scored DataFrame
    error        = pyqtSignal(str)

    def __init__(self, frames_df: pd.DataFrame, baseline, parent=None):
        super().__init__(parent)
        self._frames   = frames_df
        self._baseline = baseline

    def run(self):
        try:
            from core.anomaly_detector import score_dataframe
            self.result_ready.emit(score_dataframe(self._frames, self._baseline))
        except Exception as e:
            self.error.emit(str(e))


class EmbeddingWorker(QThread):
    """Build embedding index."""
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, frames_df: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._frames = frames_df

    def run(self):
        try:
            from core.signal_embedding import build_index
            self.finished.emit(build_index(self._frames))
        except Exception as e:
            self.error.emit(str(e))


# ── Main tab ──────────────────────────────────────────────────────────────────

class SignalIntelligenceTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state          = get_state()
        self._workers: list  = []      # keep refs to prevent GC
        self._anomaly_det    = None    # fitted baseline
        self._embedding_idx: dict = {}

        self._build_ui()
        self._state.frames_loaded.connect(self._on_frames_loaded)
        self._state.id_selected.connect(self._on_id_selected)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: ID selector ────────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(190)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.setSpacing(4)

        lbl = QLabel("CAN IDs")
        lbl.setObjectName("label_dim")
        lbl.setFont(mono_font(8))
        ll.addWidget(lbl)

        self.id_list = QListWidget()
        self.id_list.setFont(mono_font(8))
        self.id_list.currentItemChanged.connect(self._on_list_id_changed)
        ll.addWidget(self.id_list)

        self.btn_analyze_sel = QPushButton("Analyze Selected")
        self.btn_analyze_sel.setObjectName("btn_amber")
        self.btn_analyze_sel.clicked.connect(self._analyze_selected)
        ll.addWidget(self.btn_analyze_sel)

        self.btn_analyze_all = QPushButton("Analyze All IDs")
        self.btn_analyze_all.clicked.connect(self._analyze_all)
        ll.addWidget(self.btn_analyze_all)

        self.lbl_status = QLabel("Load a log to begin.")
        self.lbl_status.setFont(mono_font(7))
        self.lbl_status.setObjectName("label_dim")
        self.lbl_status.setWordWrap(True)
        ll.addWidget(self.lbl_status)

        splitter.addWidget(left)

        # ── Right: sub-tabs ──────────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._tabs.addTab(self._build_roles_tab(),       "BYTE ROLES")
        self._tabs.addTab(self._build_checksum_tab(),    "CHECKSUM RE")
        self._tabs.addTab(self._build_correlation_tab(), "CORRELATION")
        self._tabs.addTab(self._build_change_tab(),      "CHANGE DETECT")
        self._tabs.addTab(self._build_anomaly_tab(),     "ANOMALY")
        self._tabs.addTab(self._build_similarity_tab(),  "FIND SIMILAR")

        rl.addWidget(self._tabs)
        splitter.addWidget(right)
        splitter.setSizes([190, 810])
        layout.addWidget(splitter)

    # ── BYTE ROLES tab ────────────────────────────────────────────────────────

    def _build_roles_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        self.lbl_roles_id = QLabel("Select an ID →")
        self.lbl_roles_id.setFont(mono_font(9, bold=True))
        self.lbl_roles_id.setObjectName("label_green")
        hdr.addWidget(self.lbl_roles_id, stretch=1)
        self.lbl_msg_type = QLabel("")
        self.lbl_msg_type.setFont(mono_font(8))
        self.lbl_msg_type.setObjectName("label_amber")
        hdr.addWidget(self.lbl_msg_type)
        lay.addLayout(hdr)

        self.roles_table = _make_table(
            ["Byte", "Role", "Confidence", "Entropy", "Unique", "Range", "Detail"]
        )
        lay.addWidget(self.roles_table)

        btn_row = QHBoxLayout()
        self.btn_roles_run = QPushButton("Classify Bytes")
        self.btn_roles_run.setObjectName("btn_amber")
        self.btn_roles_run.clicked.connect(self._run_classify)
        btn_row.addWidget(self.btn_roles_run)
        self.btn_export_roles = QPushButton("Export to DBC Context")
        self.btn_export_roles.clicked.connect(self._export_roles_to_context)
        btn_row.addWidget(self.btn_export_roles)
        lay.addLayout(btn_row)
        return w

    # ── CHECKSUM RE tab ───────────────────────────────────────────────────────

    def _build_checksum_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        self.lbl_cs_id = QLabel("Select an ID →")
        self.lbl_cs_id.setFont(mono_font(9, bold=True))
        self.lbl_cs_id.setObjectName("label_green")
        lay.addWidget(self.lbl_cs_id)

        note = QLabel(
            "Requires ≥50 frames per ID.  Algorithms: XOR8, SUM8, SUM8_INV, "
            "XOR_NIBBLES, NIBBLE_SUM, CRC8_SAE, CRC8_AUTOSAR, HYUNDAI_XOR, HYUNDAI_FULL"
        )
        note.setFont(mono_font(7))
        note.setObjectName("label_dim")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.cs_table = _make_table(
            ["Byte", "Algorithm", "Train Acc", "Val Acc", "Confidence", "Samples"]
        )
        lay.addWidget(self.cs_table)

        btn_row = QHBoxLayout()
        self.btn_cs_run = QPushButton("Run Checksum Reverser")
        self.btn_cs_run.setObjectName("btn_amber")
        self.btn_cs_run.clicked.connect(self._run_checksum)
        btn_row.addWidget(self.btn_cs_run)
        self.btn_cs_copy = QPushButton("Copy Top Result")
        self.btn_cs_copy.clicked.connect(self._copy_checksum_result)
        btn_row.addWidget(self.btn_cs_copy)
        lay.addLayout(btn_row)
        return w

    # ── CORRELATION tab ───────────────────────────────────────────────────────

    def _build_correlation_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Min |r|:", font=mono_font(8)))
        self.corr_min_r = QDoubleSpinBox()
        self.corr_min_r.setRange(0.50, 0.99)
        self.corr_min_r.setSingleStep(0.05)
        self.corr_min_r.setValue(0.80)
        self.corr_min_r.setFixedWidth(70)
        ctrl.addWidget(self.corr_min_r)

        ctrl.addWidget(QLabel("Max pairs:", font=mono_font(8)))
        self.corr_max_pairs = QSpinBox()
        self.corr_max_pairs.setRange(10, 2000)
        self.corr_max_pairs.setValue(300)
        self.corr_max_pairs.setFixedWidth(70)
        ctrl.addWidget(self.corr_max_pairs)

        self.corr_find_lag = QCheckBox("Find lag")
        self.corr_find_lag.setChecked(True)
        ctrl.addWidget(self.corr_find_lag)
        ctrl.addStretch()

        self.btn_corr_run = QPushButton("Run Sweep")
        self.btn_corr_run.setObjectName("btn_amber")
        self.btn_corr_run.clicked.connect(self._run_correlation)
        ctrl.addWidget(self.btn_corr_run)
        lay.addLayout(ctrl)

        self.corr_progress = QProgressBar()
        self.corr_progress.setVisible(False)
        self.corr_progress.setFixedHeight(6)
        lay.addWidget(self.corr_progress)

        self.corr_table = _make_table(
            ["ID1", "Byte1", "ID2", "Byte2", "r", "lag ms", "Samples"]
        )
        lay.addWidget(self.corr_table)

        self.lbl_corr_status = QLabel("No results yet.")
        self.lbl_corr_status.setFont(mono_font(7))
        self.lbl_corr_status.setObjectName("label_dim")
        lay.addWidget(self.lbl_corr_status)
        return w

    # ── CHANGE DETECT tab ─────────────────────────────────────────────────────

    def _build_change_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        note = QLabel(
            "Enter a timestamp (or pick from the log) to find which CAN bytes "
            "changed significantly at that moment."
        )
        note.setFont(mono_font(8))
        note.setObjectName("label_dim")
        note.setWordWrap(True)
        lay.addWidget(note)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Timestamp:", font=mono_font(8)))
        self.chg_ts_edit = QLineEdit()
        self.chg_ts_edit.setPlaceholderText("e.g.  1234.567")
        self.chg_ts_edit.setFixedWidth(120)
        ctrl.addWidget(self.chg_ts_edit)

        ctrl.addWidget(QLabel("Before (s):", font=mono_font(8)))
        self.chg_before = QDoubleSpinBox()
        self.chg_before.setRange(0.05, 10.0)
        self.chg_before.setValue(0.5)
        self.chg_before.setSingleStep(0.1)
        self.chg_before.setFixedWidth(65)
        ctrl.addWidget(self.chg_before)

        ctrl.addWidget(QLabel("After (s):", font=mono_font(8)))
        self.chg_after = QDoubleSpinBox()
        self.chg_after.setRange(0.05, 10.0)
        self.chg_after.setValue(0.15)
        self.chg_after.setSingleStep(0.05)
        self.chg_after.setFixedWidth(65)
        ctrl.addWidget(self.chg_after)

        ctrl.addStretch()
        self.btn_chg_run = QPushButton("Detect Changes")
        self.btn_chg_run.setObjectName("btn_amber")
        self.btn_chg_run.clicked.connect(self._run_change_detect)
        ctrl.addWidget(self.btn_chg_run)
        lay.addLayout(ctrl)

        self.chg_table = _make_table(
            ["CAN ID", "Byte", "Before", "After", "Magnitude", "Direction"]
        )
        lay.addWidget(self.chg_table)

        self.lbl_chg_status = QLabel("")
        self.lbl_chg_status.setFont(mono_font(7))
        self.lbl_chg_status.setObjectName("label_dim")
        lay.addWidget(self.lbl_chg_status)
        return w

    # ── ANOMALY tab ───────────────────────────────────────────────────────────

    def _build_anomaly_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        grp_fit = QGroupBox("BASELINE")
        fit_lay = QHBoxLayout(grp_fit)
        self.chk_iforest = QCheckBox("Isolation Forest (sklearn)")
        self.chk_iforest.setToolTip(
            "IsolationForest is more accurate but slower to fit than Z-score."
        )
        fit_lay.addWidget(self.chk_iforest)
        fit_lay.addStretch()
        self.btn_fit = QPushButton("Fit on Loaded Frames")
        self.btn_fit.setObjectName("btn_green")
        self.btn_fit.clicked.connect(self._fit_baseline)
        fit_lay.addWidget(self.btn_fit)
        lay.addWidget(grp_fit)

        self.lbl_anomaly_baseline = QLabel("Baseline: not fitted")
        self.lbl_anomaly_baseline.setFont(mono_font(8))
        self.lbl_anomaly_baseline.setObjectName("label_dim")
        lay.addWidget(self.lbl_anomaly_baseline)

        grp_score = QGroupBox("SCORING")
        score_lay = QHBoxLayout(grp_score)
        score_lay.addWidget(QLabel("Threshold:", font=mono_font(8)))
        self.anom_threshold = QDoubleSpinBox()
        self.anom_threshold.setRange(0.10, 1.00)
        self.anom_threshold.setSingleStep(0.05)
        self.anom_threshold.setValue(0.60)
        self.anom_threshold.setFixedWidth(70)
        score_lay.addWidget(self.anom_threshold)
        score_lay.addStretch()
        self.btn_score = QPushButton("Score All Frames")
        self.btn_score.setObjectName("btn_amber")
        self.btn_score.setEnabled(False)
        self.btn_score.clicked.connect(self._score_frames)
        score_lay.addWidget(self.btn_score)
        lay.addWidget(grp_score)

        self.anomaly_table = _make_table(
            ["Timestamp", "CAN ID", "Score", "Flagged Bytes"]
        )
        lay.addWidget(self.anomaly_table)

        self.lbl_anomaly_status = QLabel("")
        self.lbl_anomaly_status.setFont(mono_font(7))
        self.lbl_anomaly_status.setObjectName("label_dim")
        lay.addWidget(self.lbl_anomaly_status)
        return w

    # ── FIND SIMILAR tab ──────────────────────────────────────────────────────

    def _build_similarity_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        note = QLabel(
            "Finds CAN IDs whose byte-statistics profile (entropy, delta-variance, "
            "unique values, frequency) is most similar to the selected ID."
        )
        note.setFont(mono_font(8))
        note.setObjectName("label_dim")
        note.setWordWrap(True)
        lay.addWidget(note)

        ctrl = QHBoxLayout()
        self.lbl_sim_query = QLabel("Query ID: —")
        self.lbl_sim_query.setFont(mono_font(9, bold=True))
        self.lbl_sim_query.setObjectName("label_green")
        ctrl.addWidget(self.lbl_sim_query, stretch=1)

        ctrl.addWidget(QLabel("Top K:", font=mono_font(8)))
        self.sim_topk = QSpinBox()
        self.sim_topk.setRange(1, 30)
        self.sim_topk.setValue(5)
        self.sim_topk.setFixedWidth(55)
        ctrl.addWidget(self.sim_topk)

        self.btn_build_index = QPushButton("Build Index")
        self.btn_build_index.clicked.connect(self._build_embedding_index)
        ctrl.addWidget(self.btn_build_index)

        self.btn_find_sim = QPushButton("Find Similar")
        self.btn_find_sim.setObjectName("btn_amber")
        self.btn_find_sim.clicked.connect(self._find_similar)
        ctrl.addWidget(self.btn_find_sim)
        lay.addLayout(ctrl)

        self.sim_table = _make_table(
            ["ID", "Similarity", "Entropy Profile", "Frequency Class"]
        )
        lay.addWidget(self.sim_table)

        self.lbl_sim_status = QLabel("Build index first.")
        self.lbl_sim_status.setFont(mono_font(7))
        self.lbl_sim_status.setObjectName("label_dim")
        lay.addWidget(self.lbl_sim_status)
        return w

    # ── State handlers ────────────────────────────────────────────────────────

    def _on_frames_loaded(self, count: int):
        self.id_list.clear()
        for can_id in self._state.get_unique_ids():
            item = QListWidgetItem(can_id)
            item.setData(Qt.ItemDataRole.UserRole, can_id)
            self.id_list.addItem(item)
        self.lbl_status.setText(f"{count} frames, {self.id_list.count()} IDs")
        # Auto-rebuild embedding index silently
        self._build_embedding_index_silent()

    def _on_id_selected(self, hex_id: str):
        for i in range(self.id_list.count()):
            item = self.id_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == hex_id:
                self.id_list.setCurrentItem(item)
                break
        self._update_query_labels(hex_id)

    def _on_list_id_changed(self, item):
        if item:
            hex_id = item.data(Qt.ItemDataRole.UserRole)
            self._update_query_labels(hex_id)

    def _update_query_labels(self, hex_id: str):
        self.lbl_roles_id.setText(f"0x{hex_id}")
        self.lbl_cs_id.setText(f"0x{hex_id}")
        self.lbl_sim_query.setText(f"Query ID: 0x{hex_id}")

    def _current_id(self) -> str | None:
        item = self.id_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ── BYTE ROLES actions ────────────────────────────────────────────────────

    def _analyze_selected(self):
        self._tabs.setCurrentIndex(0)
        self._run_classify()

    def _analyze_all(self):
        """Classify all IDs sequentially using a timer-driven queue."""
        self._tabs.setCurrentIndex(0)
        self._batch_queue = [
            self.id_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.id_list.count())
        ]
        self._batch_total = len(self._batch_queue)
        self._process_next_batch()

    def _process_next_batch(self):
        if not getattr(self, "_batch_queue", []):
            self.lbl_status.setText(
                f"Batch complete — {getattr(self, '_batch_total', 0)} IDs classified"
            )
            return
        can_id = self._batch_queue.pop(0)
        # Select the ID in the list so _run_classify picks it up
        for i in range(self.id_list.count()):
            if self.id_list.item(i).data(Qt.ItemDataRole.UserRole) == can_id:
                self.id_list.setCurrentRow(i)
                break
        done = self._batch_total - len(self._batch_queue)
        self.lbl_status.setText(f"Classifying {done}/{self._batch_total}  0x{can_id}…")
        # Start the worker, then schedule next after it finishes
        frames = self._state.get_frames_for_id(can_id)
        if frames.empty:
            QTimer.singleShot(0, self._process_next_batch)
            return
        from core.signal_classifier import classify_frame, classify_message_type
        try:
            roles    = classify_frame(frames, can_id)
            msg_type = classify_message_type(frames)
            self._on_classify_done(can_id, roles, msg_type)
        except Exception:
            pass
        QTimer.singleShot(20, self._process_next_batch)

    def _run_classify(self):
        can_id = self._current_id()
        if not can_id:
            return
        frames = self._state.get_frames_for_id(can_id)
        if frames.empty:
            return
        self.lbl_status.setText(f"Classifying 0x{can_id}…")
        w = ClassifyWorker(can_id, frames, self)
        w.result_ready.connect(self._on_classify_done)
        w.error.connect(lambda e: self.lbl_status.setText(f"Error: {e}"))
        w.start()
        self._workers.append(w)

    def _on_classify_done(self, can_id: str, roles: dict, msg_type: dict):
        self.roles_table.setRowCount(0)
        for col, info in sorted(roles.items()):
            row = self.roles_table.rowCount()
            self.roles_table.insertRow(row)
            role_color = QColor(info["color"])
            items = [
                col,
                info["role"],
                f"{info['confidence']:.0%}",
                str(info["entropy"]),
                str(info["unique"]),
                str(info["range"]),
                info.get("detail", ""),
            ]
            for c, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setForeground(QBrush(role_color if c in (1, 6) else QColor(COLORS["text"])))
                self.roles_table.setItem(row, c, item)

        t = msg_type
        p = t.get("period_ms")
        period_str = f"{p} ms" if p is not None else "aperiodic"
        self.lbl_msg_type.setText(
            f"{t['type']}  ·  {period_str}  ·  jitter {t['jitter_pct']}%  ·  {t['class']}"
        )
        self.lbl_status.setText(f"0x{can_id} classified — {len(roles)} bytes")

        # Update state embedding index entry
        if self._embedding_idx:
            try:
                from core.signal_embedding import extract_features
                frames = self._state.get_frames_for_id(can_id)
                self._embedding_idx[can_id] = extract_features(frames)
                self._state._embedding_index = self._embedding_idx
            except Exception:
                pass

    def _export_roles_to_context(self):
        """Write byte role summary to AI Engine tab's context box."""
        can_id = self._current_id()
        if not can_id:
            return
        rows = []
        for r in range(self.roles_table.rowCount()):
            byte_ = self.roles_table.item(r, 0).text()
            role_ = self.roles_table.item(r, 1).text()
            conf_ = self.roles_table.item(r, 2).text()
            det_  = self.roles_table.item(r, 6).text()
            rows.append(f"  {byte_}: {role_} ({conf_}) {det_}".rstrip())
        if rows:
            summary = f"ML Byte Roles for 0x{can_id}:\n" + "\n".join(rows)
            # Locate AI tab if possible
            try:
                mw = self.window()
                if hasattr(mw, "ai_tab"):
                    cur = mw.ai_tab.context_input.toPlainText().strip()
                    sep = "\n\n" if cur else ""
                    mw.ai_tab.context_input.setPlainText(cur + sep + summary)
            except Exception:
                pass
            self.lbl_status.setText("Roles exported to AI Engine context.")

    # ── CHECKSUM RE actions ───────────────────────────────────────────────────

    def _run_checksum(self):
        can_id = self._current_id()
        if not can_id:
            return
        frames = self._state.get_frames_for_id(can_id)
        if frames.empty:
            return
        self.lbl_status.setText(f"Checksum sweep on 0x{can_id}…")
        w = ChecksumWorker(can_id, frames, self)
        w.result_ready.connect(self._on_checksum_done)
        w.error.connect(lambda e: self.lbl_status.setText(f"Error: {e}"))
        w.start()
        self._workers.append(w)

    def _on_checksum_done(self, can_id: str, results: dict):
        self.cs_table.setRowCount(0)
        for byte_idx in sorted(results.keys()):
            matches = results[byte_idx]
            for m in matches:
                row = self.cs_table.rowCount()
                self.cs_table.insertRow(row)
                conf = m["confidence"]
                color = COLORS["green"] if conf >= 0.90 else COLORS["amber"]
                cells = [
                    f"B{byte_idx}",
                    m["algorithm"],
                    f"{m.get('train_acc', 0):.1%}",
                    f"{m.get('val_acc', 0):.1%}",
                    f"{conf:.1%}",
                    str(m.get("sample_size", "?")),
                ]
                for c, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if c in (1, 4):
                        item.setForeground(QBrush(QColor(color)))
                    self.cs_table.setItem(row, c, item)

        n = sum(len(v) for v in results.values())
        self.lbl_status.setText(
            f"0x{can_id}: {n} checksum match(es) across {len(results)} byte(s)"
        )

    def _copy_checksum_result(self):
        if self.cs_table.rowCount() == 0:
            return
        parts = [self.cs_table.item(0, c).text()
                 for c in range(self.cs_table.columnCount())]
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(" | ".join(parts))

    # ── CORRELATION actions ───────────────────────────────────────────────────

    def _run_correlation(self):
        if self._state.frames_df.empty:
            return
        self.corr_progress.setVisible(True)
        self.corr_progress.setValue(0)
        self.btn_corr_run.setEnabled(False)
        self.lbl_corr_status.setText("Running sweep…")

        w = CorrelationWorker(
            self._state.frames_df,
            min_r=self.corr_min_r.value(),
            max_pairs=self.corr_max_pairs.value(),
            find_lag=self.corr_find_lag.isChecked(),
            parent=self,
        )
        w.progress.connect(lambda d, t: (
            self.corr_progress.setMaximum(t),
            self.corr_progress.setValue(d),
        ))
        w.result_ready.connect(self._on_correlation_done)
        w.error.connect(lambda e: (
            self.lbl_corr_status.setText(f"Error: {e}"),
            self.btn_corr_run.setEnabled(True),
            self.corr_progress.setVisible(False),
        ))
        w.start()
        self._workers.append(w)

    def _on_correlation_done(self, results: list):
        self.corr_table.setRowCount(0)
        for entry in results:
            row = self.corr_table.rowCount()
            self.corr_table.insertRow(row)
            r_val = entry["r"]
            color = COLORS["green"] if abs(r_val) >= 0.90 else COLORS["amber"]
            cells = [
                entry["id1"], entry["byte1"],
                entry["id2"], entry["byte2"],
                f"{r_val:+.3f}",
                f"{entry['lag_ms']:+d}",
                str(entry["n"]),
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 4:
                    item.setForeground(QBrush(QColor(color)))
                self.corr_table.setItem(row, c, item)

        self.corr_progress.setVisible(False)
        self.btn_corr_run.setEnabled(True)
        self.lbl_corr_status.setText(f"{len(results)} correlation(s) found above threshold")

    # ── CHANGE DETECT actions ─────────────────────────────────────────────────

    def _run_change_detect(self):
        if self._state.frames_df.empty:
            return
        ts_text = self.chg_ts_edit.text().strip()
        if not ts_text:
            self.lbl_chg_status.setText("Enter a timestamp first.")
            return
        try:
            ts = float(ts_text)
        except ValueError:
            self.lbl_chg_status.setText("Invalid timestamp.")
            return

        self.lbl_chg_status.setText("Detecting changes…")
        w = ChangeWorker(
            self._state.frames_df, ts,
            before_s=self.chg_before.value(),
            after_s=self.chg_after.value(),
            parent=self,
        )
        w.result_ready.connect(self._on_change_done)
        w.error.connect(lambda e: self.lbl_chg_status.setText(f"Error: {e}"))
        w.start()
        self._workers.append(w)

    def _on_change_done(self, results: list):
        self.chg_table.setRowCount(0)
        for entry in results:
            row = self.chg_table.rowCount()
            self.chg_table.insertRow(row)
            mag   = entry["magnitude"]
            color = COLORS["error"] if mag >= 100 else (
                COLORS["amber"] if mag >= 10 else COLORS["text"]
            )
            before = entry["before"]
            after  = entry["after"]
            direction = "↑ RISING" if after > before else "↓ FALLING"
            cells = [
                entry["id"],
                entry["byte"],
                f"0x{int(before):02X} ({int(before)})",
                f"0x{int(after):02X}  ({int(after)})",
                str(mag),
                direction,
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c in (4, 5):
                    item.setForeground(QBrush(QColor(color)))
                self.chg_table.setItem(row, c, item)

        self.lbl_chg_status.setText(f"{len(results)} byte change(s) detected")

    # ── ANOMALY actions ───────────────────────────────────────────────────────

    def _fit_baseline(self):
        if self._state.frames_df.empty:
            return
        self.btn_fit.setEnabled(False)
        self.lbl_anomaly_baseline.setText("Fitting baseline…")
        w = AnomalyFitWorker(
            self._state.frames_df,
            use_iforest=self.chk_iforest.isChecked(),
            parent=self,
        )
        w.finished.connect(self._on_baseline_fitted)
        w.error.connect(lambda e: (
            self.lbl_anomaly_baseline.setText(f"Error: {e}"),
            self.btn_fit.setEnabled(True),
        ))
        w.start()
        self._workers.append(w)

    def _on_baseline_fitted(self, det, name: str):
        self._anomaly_det = det
        ids = det.fitted_ids()
        self.lbl_anomaly_baseline.setText(
            f"Baseline fitted ({name}) — {len(ids)} IDs"
        )
        self.lbl_anomaly_baseline.setStyleSheet(f"color:{COLORS['green']}")
        self.btn_fit.setEnabled(True)
        self.btn_score.setEnabled(True)

    def _score_frames(self):
        if self._anomaly_det is None or self._state.frames_df.empty:
            return
        self.btn_score.setEnabled(False)
        self.lbl_anomaly_status.setText("Scoring frames…")
        w = AnomalyScoreWorker(self._state.frames_df, self._anomaly_det, self)
        w.result_ready.connect(self._on_score_done)
        w.error.connect(lambda e: (
            self.lbl_anomaly_status.setText(f"Error: {e}"),
            self.btn_score.setEnabled(True),
        ))
        w.start()
        self._workers.append(w)

    def _on_score_done(self, scored_df: pd.DataFrame):
        threshold = self.anom_threshold.value()
        flagged   = scored_df[scored_df["anomaly_score"] >= threshold].copy()
        flagged   = flagged.sort_values("anomaly_score", ascending=False)

        self.anomaly_table.setRowCount(0)
        BYTE_COLS = [f"B{i}" for i in range(8)]
        for _, row in flagged.head(200).iterrows():
            r = self.anomaly_table.rowCount()
            self.anomaly_table.insertRow(r)
            score  = float(row.get("anomaly_score", 0))
            color  = COLORS["error"] if score >= 0.80 else COLORS["amber"]
            # Which bytes are most anomalous
            changed = []
            if self._anomaly_det and hasattr(self._anomaly_det, "_baselines"):
                bl = self._anomaly_det._baselines.get(str(row.get("ID", "")), {})
                for col in BYTE_COLS:
                    if col in bl:
                        mean, std = bl[col]
                        val = row.get(col)
                        if val is not None and abs(float(val) - mean) / std > 2:
                            changed.append(f"{col}={int(float(val))}")
            cells = [
                f"{row.get('Timestamp', 0):.3f}",
                str(row.get("ID", "?")),
                f"{score:.3f}",
                ", ".join(changed[:4]),
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 2:
                    item.setForeground(QBrush(QColor(color)))
                self.anomaly_table.setItem(r, c, item)

        self.lbl_anomaly_status.setText(
            f"{len(flagged)} anomalous frame(s) above threshold {threshold:.0%}"
        )
        self.btn_score.setEnabled(True)

        # Emit state signal
        if not flagged.empty:
            top_id = str(flagged.iloc[0].get("ID", "?"))
            top_score = float(flagged.iloc[0]["anomaly_score"])
            try:
                self._state.anomaly_detected.emit(top_id, top_score)
            except Exception:
                pass

    # ── SIMILARITY actions ────────────────────────────────────────────────────

    def _build_embedding_index(self):
        if self._state.frames_df.empty:
            return
        self.lbl_sim_status.setText("Building index…")
        w = EmbeddingWorker(self._state.frames_df, self)
        w.finished.connect(self._on_index_built)
        w.error.connect(lambda e: self.lbl_sim_status.setText(f"Error: {e}"))
        w.start()
        self._workers.append(w)

    def _build_embedding_index_silent(self):
        if self._state.frames_df.empty:
            return
        w = EmbeddingWorker(self._state.frames_df, self)
        w.finished.connect(self._on_index_built)
        w.start()
        self._workers.append(w)

    def _on_index_built(self, index: dict):
        self._embedding_idx = index
        self._state._embedding_index = index
        n = len(index)
        self.lbl_sim_status.setText(f"Index ready — {n} IDs embedded.")

    def _find_similar(self):
        can_id = self._current_id()
        if not can_id:
            return
        if not self._embedding_idx:
            self._build_embedding_index()
            self.lbl_sim_status.setText("Building index first, then re-click Find Similar.")
            return

        from core.signal_embedding import find_similar
        results = find_similar(can_id, self._embedding_idx, top_k=self.sim_topk.value())

        self.sim_table.setRowCount(0)
        for entry in results:
            sim_id = entry["id"]
            sim    = entry["similarity"]
            row    = self.sim_table.rowCount()
            self.sim_table.insertRow(row)
            color  = (COLORS["green"]  if sim >= 0.90 else
                      COLORS["amber"]  if sim >= 0.70 else
                      COLORS["dim"])

            # Build entropy profile string from index vector [0:8]
            vec = self._embedding_idx.get(sim_id)
            if vec is not None:
                ent_profile = " ".join(
                    f"{v:.1f}" for v in (vec[:8] * 8)  # un-normalize
                )
            else:
                ent_profile = "—"

            # Frequency class
            try:
                df2 = self._state.frames_df[self._state.frames_df["ID"] == sim_id]
                from core.periodicity import compute_periodicity, classify_period
                per_map = compute_periodicity(df2)
                freq_class = classify_period(per_map.get(sim_id, 0)) if per_map else "?"
            except Exception:
                freq_class = "?"

            cells = [sim_id, f"{sim:.3f}", ent_profile, freq_class]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 1:
                    item.setForeground(QBrush(QColor(color)))
                self.sim_table.setItem(row, c, item)

        self.lbl_sim_status.setText(
            f"Top {len(results)} similar to 0x{can_id}"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget()
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    return t
