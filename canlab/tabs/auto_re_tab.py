"""
AUTO-RE tab — automated reverse engineering tools:
  1. Counter / Checksum Detector
  2. Entropy Signal Boundary Detector
  3. Correlated Signal Finder
  4. Checksum Algorithm Guesser
"""
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QComboBox, QSpinBox, QTextEdit, QTabWidget, QSplitter,
    QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush

from theme import COLORS, mono_font
from core.state import get_state


class AutoRETab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = get_state()
        self._build_ui()
        self._state.frames_loaded.connect(self._on_frames_loaded)

    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget()
        tabs.addTab(self._build_ctr_chk_tab(),    "COUNTER/CHECKSUM")
        tabs.addTab(self._build_entropy_tab(),     "ENTROPY BOUNDARIES")
        tabs.addTab(self._build_correlation_tab(), "CORRELATION")
        tabs.addTab(self._build_guesser_tab(),     "CHECKSUM GUESSER")
        outer.addWidget(tabs)

    # ── 1. Counter / Checksum Detector ────────────────────────────────────────

    def _build_ctr_chk_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(
            "Automatically detect counter bytes (rolling +1 pattern) and "
            "checksum bytes (reproducible from other bytes).",
            font=mono_font(8),
        ))
        hdr.addStretch()
        self.btn_run_ctr = QPushButton("Run Detection")
        self.btn_run_ctr.setObjectName("btn_green")
        self.btn_run_ctr.clicked.connect(self._run_counter_checksum)
        hdr.addWidget(self.btn_run_ctr)
        lay.addLayout(hdr)

        self.ctr_table = QTableWidget(0, 6)
        self.ctr_table.setHorizontalHeaderLabels([
            "ID", "Byte", "Type", "Algorithm / Wrap", "Confidence", "Notes"
        ])
        self.ctr_table.setFont(mono_font())
        self.ctr_table.verticalHeader().setVisible(False)
        self.ctr_table.verticalHeader().setDefaultSectionSize(20)
        self.ctr_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ctr_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.ctr_table)

        self.lbl_ctr_status = QLabel("Load frames, then click Run Detection.")
        self.lbl_ctr_status.setFont(mono_font(8))
        self.lbl_ctr_status.setObjectName("label_dim")
        lay.addWidget(self.lbl_ctr_status)
        return w

    def _run_counter_checksum(self):
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load a CAN log first.")
            return
        self.btn_run_ctr.setEnabled(False)
        self.lbl_ctr_status.setText("Analysing…")

        from core.counter_checksum_detector import detect_counters_and_checksums
        results = detect_counters_and_checksums(df)

        rows = []
        for can_id, data in results.items():
            for ctr in data["counters"]:
                rows.append((can_id, ctr["col"], "COUNTER",
                             f"wrap={ctr['wrap']} ({ctr['type']})",
                             ctr["confidence"]))
            for chk in data["checksums"]:
                rows.append((can_id, chk["col"], "CHECKSUM",
                             chk["algorithm"], chk["confidence"]))

        self.ctr_table.setRowCount(len(rows))
        for r, (can_id, col, rtype, detail, conf) in enumerate(rows):
            color = COLORS["green"] if rtype == "CHECKSUM" else COLORS["amber"]
            cells = [f"0x{can_id}", col, rtype, detail, f"{conf:.1%}", ""]
            for c, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font())
                if c in (2, 3):
                    item.setForeground(QBrush(QColor(color)))
                self.ctr_table.setItem(r, c, item)

        n_ctr = sum(len(v["counters"])  for v in results.values())
        n_chk = sum(len(v["checksums"]) for v in results.values())
        self.lbl_ctr_status.setText(
            f"Found {n_ctr} counter byte(s) and {n_chk} checksum byte(s) "
            f"across {len(results)} message(s)."
        )
        self.lbl_ctr_status.setStyleSheet(f"color:{COLORS['green']}")
        self.btn_run_ctr.setEnabled(True)

    # ── 2. Entropy Boundary Detector ─────────────────────────────────────────

    def _build_entropy_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(
            "Per-bit entropy analysis — contiguous high-entropy bit runs "
            "suggest signal boundaries. Click a row to view bit-level chart.",
            font=mono_font(8),
        ))
        hdr.addStretch()
        self.btn_run_entropy = QPushButton("Run Analysis")
        self.btn_run_entropy.setObjectName("btn_green")
        self.btn_run_entropy.clicked.connect(self._run_entropy)
        hdr.addWidget(self.btn_run_entropy)
        lay.addLayout(hdr)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.entropy_table = QTableWidget(0, 6)
        self.entropy_table.setHorizontalHeaderLabels([
            "ID", "Start Bit", "Length", "Entropy", "Confidence", "Bit Range Label"
        ])
        self.entropy_table.setFont(mono_font())
        self.entropy_table.verticalHeader().setVisible(False)
        self.entropy_table.verticalHeader().setDefaultSectionSize(20)
        self.entropy_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.entropy_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.entropy_table.cellClicked.connect(self._on_entropy_row_click)
        splitter.addWidget(self.entropy_table)

        # Per-bit entropy bar chart
        self.entropy_plot = pg.PlotWidget()
        self.entropy_plot.setBackground(COLORS["bg"])
        self.entropy_plot.setLabel("bottom", "Bit position (0=LSB B0)")
        self.entropy_plot.setLabel("left",   "Entropy (bits)")
        self.entropy_plot.setYRange(0, 1)
        self._entropy_bar = pg.BarGraphItem(x=[], height=[], width=0.8,
                                            brush=COLORS["green"])
        self.entropy_plot.addItem(self._entropy_bar)
        splitter.addWidget(self.entropy_plot)

        splitter.setSizes([300, 200])
        lay.addWidget(splitter)

        self.lbl_entropy_status = QLabel("Load frames, then click Run Analysis.")
        self.lbl_entropy_status.setFont(mono_font(8))
        self.lbl_entropy_status.setObjectName("label_dim")
        lay.addWidget(self.lbl_entropy_status)

        self._entropy_results = {}
        return w

    def _run_entropy(self):
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load a CAN log first.")
            return
        self.btn_run_entropy.setEnabled(False)
        self.lbl_entropy_status.setText("Computing bit entropies…")

        from core.entropy_boundary import suggest_signals, detect_signal_boundaries
        self._entropy_results = detect_signal_boundaries(df)
        suggestions = suggest_signals(df)

        self.entropy_table.setRowCount(len(suggestions))
        for r, s in enumerate(suggestions):
            conf = s.get("confidence", 0.0)
            cells = [
                f"0x{s['id']}",
                str(s["start_bit"]),
                str(s["length"]),
                f"{s['mean_entropy']:.3f}",
                f"{conf:.1%}",
                s["label"],
            ]
            for c, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font())
                if c == 3:
                    ent = s["mean_entropy"]
                    col = COLORS["green"] if ent > 0.7 else COLORS["amber"]
                    item.setForeground(QBrush(QColor(col)))
                elif c == 4:
                    col = COLORS["green"] if conf >= 0.80 else COLORS["amber"]
                    item.setForeground(QBrush(QColor(col)))
                self.entropy_table.setItem(r, c, item)

        self.lbl_entropy_status.setText(
            f"Found {len(suggestions)} candidate signal range(s)."
        )
        self.lbl_entropy_status.setStyleSheet(f"color:{COLORS['green']}")
        self.btn_run_entropy.setEnabled(True)

    def _on_entropy_row_click(self, row, _col):
        id_item = self.entropy_table.item(row, 0)
        if not id_item:
            return
        can_id = id_item.text().lstrip("0x")
        df = self._state.frames_df
        if df.empty or can_id not in df["ID"].values:
            return

        from core.entropy_boundary import _bit_entropy
        frames = df[df["ID"] == can_id]
        ent = _bit_entropy(frames)
        x = list(range(64))
        self._entropy_bar.setOpts(x=x, height=ent.tolist(), width=0.8)
        self.entropy_plot.setTitle(f"Bit entropy — 0x{can_id}")

    # ── 3. Correlated Signal Finder ───────────────────────────────────────────

    def _build_correlation_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(
            "Combined dependency score: Pearson + Spearman rank + Mutual Information. "
            "Catches linear, monotonic, and bitfield correlations. High score = change together.",
            font=mono_font(8),
        ))
        hdr.addStretch()
        self.btn_run_corr = QPushButton("Run Correlation")
        self.btn_run_corr.setObjectName("btn_green")
        self.btn_run_corr.clicked.connect(self._run_correlation)
        hdr.addWidget(self.btn_run_corr)
        lay.addLayout(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Heatmap
        self.corr_img = pg.ImageView()
        self.corr_img.ui.roiBtn.hide()
        self.corr_img.ui.menuBtn.hide()
        self.corr_img.getView().setBackgroundColor(COLORS["bg"])
        splitter.addWidget(self.corr_img)

        # High-correlation pairs table
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("HIGH CORRELATION PAIRS  (|r| > 0.7)", font=mono_font(8)))
        self.corr_pairs_table = QTableWidget(0, 3)
        self.corr_pairs_table.setHorizontalHeaderLabels(["ID A", "ID B", "r"])
        self.corr_pairs_table.setFont(mono_font())
        self.corr_pairs_table.verticalHeader().setVisible(False)
        self.corr_pairs_table.verticalHeader().setDefaultSectionSize(20)
        self.corr_pairs_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.corr_pairs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        rl.addWidget(self.corr_pairs_table)
        splitter.addWidget(right)

        splitter.setSizes([500, 300])
        lay.addWidget(splitter)

        self.lbl_corr_status = QLabel("Load frames, then click Run Correlation.")
        self.lbl_corr_status.setFont(mono_font(8))
        self.lbl_corr_status.setObjectName("label_dim")
        lay.addWidget(self.lbl_corr_status)
        return w

    def _run_correlation(self):
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load a CAN log first.")
            return
        self.btn_run_corr.setEnabled(False)
        self.lbl_corr_status.setText("Computing correlation matrix…")

        from core.signal_analyzer import compute_correlation_matrix
        corr_df = compute_correlation_matrix(df)
        if corr_df.empty:
            self.lbl_corr_status.setText("Not enough data.")
            self.btn_run_corr.setEnabled(True)
            return

        # Show heatmap
        arr = corr_df.values.astype(np.float32)
        self.corr_img.setImage(arr, autoRange=True, autoLevels=True)

        # Find high-correlation pairs
        ids   = corr_df.columns.tolist()
        pairs = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                r = corr_df.iloc[i, j]
                if abs(r) > 0.7:
                    pairs.append((ids[i], ids[j], r))
        pairs.sort(key=lambda x: abs(x[2]), reverse=True)

        self.corr_pairs_table.setRowCount(len(pairs))
        for row, (a, b, r) in enumerate(pairs):
            col = COLORS["green"] if r > 0 else COLORS["error"]
            cells = [f"0x{a}", f"0x{b}", f"{r:.3f}"]
            for c, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font())
                if c == 2:
                    item.setForeground(QBrush(QColor(col)))
                self.corr_pairs_table.setItem(row, c, item)

        self.lbl_corr_status.setText(
            f"Matrix: {len(ids)}×{len(ids)} IDs  |  "
            f"{len(pairs)} high-correlation pair(s) found."
        )
        self.lbl_corr_status.setStyleSheet(f"color:{COLORS['green']}")
        self.btn_run_corr.setEnabled(True)

    # ── 4. Checksum Guesser ───────────────────────────────────────────────────

    def _build_guesser_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lay.addWidget(QLabel(
            "Select a message ID and a candidate checksum byte. "
            "Tries XOR8, SUM8, CRC8-SAE, CRC8-AUTOSAR, Hyundai-XOR, and more.",
            font=mono_font(8),
        ))

        cfg_grp = QGroupBox("CONFIGURATION")
        cg = QHBoxLayout(cfg_grp)
        cg.addWidget(QLabel("Message ID:"))
        self.guesser_id_combo = QComboBox()
        self.guesser_id_combo.setFont(mono_font())
        cg.addWidget(self.guesser_id_combo, 1)
        cg.addWidget(QLabel("Candidate byte:"))
        self.guesser_byte_spin = QSpinBox()
        self.guesser_byte_spin.setRange(0, 7)
        self.guesser_byte_spin.setValue(7)
        cg.addWidget(self.guesser_byte_spin)
        self.btn_guess = QPushButton("Guess Algorithm")
        self.btn_guess.setObjectName("btn_green")
        self.btn_guess.clicked.connect(self._run_guesser)
        cg.addWidget(self.btn_guess)
        lay.addWidget(cfg_grp)

        self.guesser_table = QTableWidget(0, 5)
        self.guesser_table.setHorizontalHeaderLabels([
            "Algorithm", "Confidence", "Train Acc", "Val Acc", "Sample"
        ])
        self.guesser_table.setFont(mono_font())
        self.guesser_table.verticalHeader().setVisible(False)
        self.guesser_table.verticalHeader().setDefaultSectionSize(22)
        self.guesser_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.guesser_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.guesser_table)

        self.guesser_detail = QTextEdit()
        self.guesser_detail.setReadOnly(True)
        self.guesser_detail.setFont(mono_font(8))
        self.guesser_detail.setMaximumHeight(100)
        lay.addWidget(self.guesser_detail)

        self.lbl_guesser_status = QLabel("Select ID and byte, then click Guess.")
        self.lbl_guesser_status.setFont(mono_font(8))
        self.lbl_guesser_status.setObjectName("label_dim")
        lay.addWidget(self.lbl_guesser_status)

        self._refresh_guesser_ids()
        return w

    def _refresh_guesser_ids(self):
        ids = self._state.get_unique_ids()
        self.guesser_id_combo.blockSignals(True)
        self.guesser_id_combo.clear()
        for can_id in ids:
            self.guesser_id_combo.addItem(f"0x{can_id}", can_id)
        self.guesser_id_combo.blockSignals(False)

    def _run_guesser(self):
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load a CAN log first.")
            return
        can_id = self.guesser_id_combo.currentData()
        if not can_id:
            return
        byte_idx = self.guesser_byte_spin.value()
        frames   = df[df["ID"] == can_id]
        if len(frames) < 5:
            self.lbl_guesser_status.setText("Need at least 5 frames for this ID.")
            return

        from core.checksum_guesser import guess_checksum
        results = guess_checksum(frames, byte_idx, can_id)

        self.guesser_table.setRowCount(len(results))
        for row, r in enumerate(results):
            conf_col = COLORS["green"] if r["confidence"] > 0.90 else COLORS["amber"]
            cells = [
                r["algorithm"],
                f"{r['confidence']:.1%}",
                f"{r['train_acc']:.1%}",
                f"{r['val_acc']:.1%}",
                str(r["sample_size"]),
            ]
            for c, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font())
                if c == 1:
                    item.setForeground(QBrush(QColor(conf_col)))
                self.guesser_table.setItem(row, c, item)

        if results:
            best = results[0]
            detail = (
                f"Best match: {best['algorithm']}  "
                f"confidence={best['confidence']:.1%}  "
                f"train={best['train_acc']:.1%}  val={best['val_acc']:.1%}  "
                f"n={best['sample_size']}\n\n"
                f"To use in DBC Builder / injection, set checksum algorithm "
                f"to '{best['algorithm']}' for message 0x{can_id}."
            )
            self.guesser_detail.setPlainText(detail)
            self.lbl_guesser_status.setText(
                f"Found {len(results)} matching algorithm(s) for B{byte_idx} of 0x{can_id}."
            )
            self.lbl_guesser_status.setStyleSheet(f"color:{COLORS['green']}")
        else:
            self.guesser_detail.setPlainText(
                f"No algorithm matched B{byte_idx} of 0x{can_id}.\n"
                "This byte may not be a checksum, or uses a proprietary algorithm."
            )
            self.lbl_guesser_status.setText("No matches found.")
            self.lbl_guesser_status.setStyleSheet(f"color:{COLORS['dim']}")

    # ── State handlers ────────────────────────────────────────────────────────

    def _on_frames_loaded(self, _count: int):
        self._refresh_guesser_ids()
