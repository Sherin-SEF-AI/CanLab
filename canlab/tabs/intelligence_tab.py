"""INTELLIGENCE tab — auto-DBC, diff, fingerprint, periodicity, opendbc cross-ref."""
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QFileDialog,
    QTextEdit, QMessageBox, QProgressBar, QTabWidget, QLineEdit,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush, QFont

from theme import COLORS, mono_font
from core.state import get_state


STATUS_COLORS = {
    "added":   COLORS["green"],
    "removed": COLORS["error"],
    "changed": COLORS["amber"],
    "same":    COLORS["dim"],
}


class IntelligenceTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = get_state()
        self._build_ui()
        self._state.frames_loaded.connect(self._on_frames_loaded)
        self._state.dbc_updated.connect(self._on_dbc_updated)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left panel ────────────────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(240)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.setSpacing(6)

        # Fingerprint
        fp_grp = QGroupBox("VEHICLE FINGERPRINT")
        fp_lay = QVBoxLayout(fp_grp)
        self.btn_fingerprint = QPushButton("Run Fingerprint")
        self.btn_fingerprint.clicked.connect(self._run_fingerprint)
        fp_lay.addWidget(self.btn_fingerprint)
        self.lbl_fingerprint = QLabel("—")
        self.lbl_fingerprint.setFont(mono_font(8))
        self.lbl_fingerprint.setWordWrap(True)
        fp_lay.addWidget(self.lbl_fingerprint)
        ll.addWidget(fp_grp)

        # Periodicity
        per_grp = QGroupBox("SIGNAL PERIODICITY")
        per_lay = QVBoxLayout(per_grp)
        self.btn_period = QPushButton("Compute Periodicities")
        self.btn_period.clicked.connect(self._compute_periodicity)
        per_lay.addWidget(self.btn_period)
        ll.addWidget(per_grp)

        # Auto DBC
        dbc_grp = QGroupBox("AUTO DBC GENERATION")
        dbc_lay = QVBoxLayout(dbc_grp)
        self.btn_auto_dbc = QPushButton("Auto-Build DBC")
        self.btn_auto_dbc.setObjectName("btn_green")
        self.btn_auto_dbc.clicked.connect(self._auto_build_dbc)
        dbc_lay.addWidget(self.btn_auto_dbc)
        self.lbl_auto_dbc = QLabel("")
        self.lbl_auto_dbc.setFont(mono_font(8))
        self.lbl_auto_dbc.setObjectName("label_dim")
        dbc_lay.addWidget(self.lbl_auto_dbc)
        ll.addWidget(dbc_grp)

        # Diff
        diff_grp = QGroupBox("LOG DIFF")
        diff_lay = QVBoxLayout(diff_grp)
        self.btn_set_baseline = QPushButton("Set Current as Baseline")
        self.btn_set_baseline.clicked.connect(self._set_baseline)
        self.btn_run_diff     = QPushButton("Load & Compare Log…")
        self.btn_run_diff.clicked.connect(self._run_diff)
        self.lbl_baseline = QLabel("Baseline: none")
        self.lbl_baseline.setFont(mono_font(8))
        self.lbl_baseline.setObjectName("label_dim")
        diff_lay.addWidget(self.lbl_baseline)
        diff_lay.addWidget(self.btn_set_baseline)
        diff_lay.addWidget(self.btn_run_diff)
        ll.addWidget(diff_grp)

        # opendbc cross-ref
        ref_grp = QGroupBox("opendbc CROSS-REF")
        ref_lay = QVBoxLayout(ref_grp)
        self.btn_xref = QPushButton("Cross-Reference Signals")
        self.btn_xref.clicked.connect(self._run_xref)
        ref_lay.addWidget(self.btn_xref)
        ll.addWidget(ref_grp)

        # Change-on-Action
        coa_grp = QGroupBox("CHANGE-ON-ACTION")
        coa_lay = QVBoxLayout(coa_grp)
        self.btn_coa_baseline = QPushButton("① Capture Baseline")
        self.btn_coa_baseline.clicked.connect(self._coa_capture_baseline)
        self.btn_coa_action   = QPushButton("② Capture After Action")
        self.btn_coa_action.clicked.connect(self._coa_capture_action)
        self.btn_coa_compute  = QPushButton("③ Show Delta")
        self.btn_coa_compute.setObjectName("btn_green")
        self.btn_coa_compute.clicked.connect(self._coa_compute)
        self.btn_coa_clear    = QPushButton("Clear")
        self.btn_coa_clear.clicked.connect(self._coa_clear)
        self.lbl_coa_status = QLabel("—")
        self.lbl_coa_status.setFont(mono_font(8))
        self.lbl_coa_status.setObjectName("label_dim")
        for b in [self.btn_coa_baseline, self.btn_coa_action,
                  self.btn_coa_compute, self.btn_coa_clear]:
            coa_lay.addWidget(b)
        coa_lay.addWidget(self.lbl_coa_status)
        ll.addWidget(coa_grp)

        # Community Profiles
        comm_grp = QGroupBox("COMMUNITY PROFILES")
        comm_lay = QVBoxLayout(comm_grp)
        self.btn_comm_fetch   = QPushButton("Fetch Profiles…")
        self.btn_comm_fetch.clicked.connect(self._comm_fetch)
        self.btn_comm_apply   = QPushButton("Apply Selected")
        self.btn_comm_apply.clicked.connect(self._comm_apply)
        self.lbl_comm_status  = QLabel("—")
        self.lbl_comm_status.setFont(mono_font(8))
        self.lbl_comm_status.setObjectName("label_dim")
        comm_lay.addWidget(self.btn_comm_fetch)
        comm_lay.addWidget(self.btn_comm_apply)
        comm_lay.addWidget(self.lbl_comm_status)
        ll.addWidget(comm_grp)

        # J1939 Decoder
        j1939_grp = QGroupBox("J1939 PGN DECODER")
        j1939_lay = QVBoxLayout(j1939_grp)
        self.btn_j1939 = QPushButton("Scan for J1939 IDs")
        self.btn_j1939.clicked.connect(self._run_j1939)
        j1939_lay.addWidget(self.btn_j1939)
        self.lbl_j1939 = QLabel("—")
        self.lbl_j1939.setFont(mono_font(8))
        j1939_lay.addWidget(self.lbl_j1939)
        ll.addWidget(j1939_grp)

        # Value Reverse Lookup
        vr_grp = QGroupBox("VALUE REVERSE LOOKUP")
        vr_lay = QVBoxLayout(vr_grp)
        from PyQt6.QtWidgets import QDoubleSpinBox
        vr_row = QHBoxLayout()
        vr_row.addWidget(QLabel("Target:", font=mono_font(8)))
        self.vr_target = QDoubleSpinBox()
        self.vr_target.setRange(-100000, 100000)
        self.vr_target.setValue(0.0)
        self.vr_target.setDecimals(2)
        vr_row.addWidget(self.vr_target)
        vr_row.addWidget(QLabel("±", font=mono_font(8)))
        self.vr_tol = QDoubleSpinBox()
        self.vr_tol.setRange(0.001, 1000)
        self.vr_tol.setValue(1.0)
        self.vr_tol.setDecimals(3)
        vr_row.addWidget(self.vr_tol)
        vr_lay.addLayout(vr_row)
        self.btn_vr = QPushButton("Find Signal")
        self.btn_vr.setObjectName("btn_green")
        self.btn_vr.clicked.connect(self._run_value_reverse)
        vr_lay.addWidget(self.btn_vr)
        self.lbl_vr = QLabel("—")
        self.lbl_vr.setFont(mono_font(8))
        vr_lay.addWidget(self.lbl_vr)
        ll.addWidget(vr_grp)

        ll.addStretch()
        splitter.addWidget(left)

        # ── Right panel ───────────────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 6, 6)
        rl.setSpacing(6)

        # Periodicity table
        self.period_table = QTableWidget(0, 3)
        self.period_table.setHorizontalHeaderLabels(["ID", "Cycle (ms)", "Class"])
        self.period_table.setFont(mono_font())
        self.period_table.verticalHeader().setVisible(False)
        self.period_table.verticalHeader().setDefaultSectionSize(20)
        self.period_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.period_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.period_table.setMaximumHeight(200)
        rl.addWidget(QLabel("PERIODICITIES", font=mono_font(8)))
        rl.addWidget(self.period_table)

        # Diff table
        self.diff_table = QTableWidget(0, 5)
        self.diff_table.setHorizontalHeaderLabels(["ID", "Status", "Base#", "Comp#", "Changed Bytes"])
        self.diff_table.setFont(mono_font())
        self.diff_table.verticalHeader().setVisible(False)
        self.diff_table.verticalHeader().setDefaultSectionSize(20)
        self.diff_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.diff_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        rl.addWidget(QLabel("LOG DIFF RESULTS", font=mono_font(8)))
        rl.addWidget(self.diff_table)

        # Cross-ref output
        self.xref_text = QTextEdit()
        self.xref_text.setReadOnly(True)
        self.xref_text.setFont(mono_font(8))
        self.xref_text.setMaximumHeight(160)
        rl.addWidget(QLabel("opendbc MATCHES", font=mono_font(8)))
        rl.addWidget(self.xref_text)

        splitter.addWidget(right)
        # Delta table (change-on-action)
        self.delta_table = QTableWidget(0, 7)
        self.delta_table.setHorizontalHeaderLabels(
            ["ID", "Byte", "Before", "After", "Direction", "p-value", "Bits changed"]
        )
        self.delta_table.setFont(mono_font())
        self.delta_table.verticalHeader().setVisible(False)
        self.delta_table.verticalHeader().setDefaultSectionSize(20)
        self.delta_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.delta_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.delta_table.setMaximumHeight(180)
        rl.addWidget(QLabel("CHANGE DELTA", font=mono_font(8)))
        rl.addWidget(self.delta_table)

        # Community profiles list
        self.comm_list = QTableWidget(0, 3)
        self.comm_list.setHorizontalHeaderLabels(["ID", "Vehicle", "Notes"])
        self.comm_list.setFont(mono_font())
        self.comm_list.verticalHeader().setVisible(False)
        self.comm_list.verticalHeader().setDefaultSectionSize(20)
        self.comm_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.comm_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.comm_list.setMaximumHeight(140)
        rl.addWidget(QLabel("COMMUNITY PROFILES", font=mono_font(8)))
        rl.addWidget(self.comm_list)

        # J1939 table
        self.j1939_table = QTableWidget(0, 6)
        self.j1939_table.setHorizontalHeaderLabels(
            ["ID", "PGN", "PGN Name", "Source", "Frames", "SPN Preview"]
        )
        self.j1939_table.setFont(mono_font(8))
        self.j1939_table.verticalHeader().setVisible(False)
        self.j1939_table.verticalHeader().setDefaultSectionSize(20)
        self.j1939_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.j1939_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.j1939_table.setMaximumHeight(160)
        rl.addWidget(QLabel("J1939 PGN SCAN RESULTS", font=mono_font(8)))
        rl.addWidget(self.j1939_table)

        # Value Reverse table
        self.vr_table = QTableWidget(0, 8)
        self.vr_table.setHorizontalHeaderLabels(
            ["ID", "Byte", "Length", "Order", "Scale", "Offset", "Score", "Median"]
        )
        self.vr_table.setFont(mono_font(8))
        self.vr_table.verticalHeader().setVisible(False)
        self.vr_table.verticalHeader().setDefaultSectionSize(20)
        self.vr_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.vr_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.vr_table.setMaximumHeight(160)
        rl.addWidget(QLabel("VALUE REVERSE LOOKUP RESULTS", font=mono_font(8)))
        rl.addWidget(self.vr_table)

        splitter.setSizes([240, 760])
        outer.addWidget(splitter)
        self._change_recorder = None

    # ── Fingerprint ───────────────────────────────────────────────────────────

    def _run_fingerprint(self):
        from core.fingerprint import fingerprint_vehicle
        ids = set(self._state.get_unique_ids())
        if not ids:
            self.lbl_fingerprint.setText("No frames loaded.")
            return
        # Build DLC map from frames
        df = self._state.frames_df
        dlc_map = {}
        if not df.empty and "DLC" in df.columns:
            dlc_map = df.groupby("ID")["DLC"].median().astype(int).to_dict()

        result = fingerprint_vehicle(ids, self._state.periodicities, dlc_map)
        self._state.fingerprint = result
        conf_pct = int(result["confidence"] * 100)
        quality  = result.get("quality", "")
        detail   = result.get("score_detail", {})
        color = COLORS["green"] if result["confidence"] >= 0.75 else COLORS["amber"]
        top3_txt = "\n".join(
            f"  {i+1}. {c['model']}  {int(c['confidence']*100)}%"
            for i, c in enumerate(result.get("top3", [result])[:3])
        )
        self.lbl_fingerprint.setText(
            f"{result['model']}\n"
            f"Confidence: {conf_pct}%  [{quality}]\n"
            f"ID: {detail.get('id_coverage',0):.0%}  "
            f"Period: {detail.get('period',0):.0%}  "
            f"DLC: {detail.get('dlc',0):.0%}\n"
            f"Matched: {', '.join(result['matched_ids'])}\n"
            f"Missing: {', '.join(result['missing_ids']) or 'none'}\n"
            f"Top 3:\n{top3_txt}"
        )
        self.lbl_fingerprint.setStyleSheet(f"color:{color}")
        self._state.fingerprint_matched.emit(result)

    # ── Periodicity ───────────────────────────────────────────────────────────

    def _compute_periodicity(self):
        from core.periodicity import compute_periodicity, classify_period
        periods = compute_periodicity(self._state.frames_df)
        self._state.periodicities = periods
        self.period_table.setRowCount(len(periods))
        for row, (can_id, ms) in enumerate(sorted(periods.items())):
            cls = classify_period(ms)
            items = [
                QTableWidgetItem(f"0x{can_id}"),
                QTableWidgetItem(f"{ms:.2f}"),
                QTableWidgetItem(cls),
            ]
            for ci, item in enumerate(items):
                item.setFont(mono_font())
                item.setForeground(QBrush(QColor(COLORS["text"])))
                self.period_table.setItem(row, ci, item)

    # ── Auto DBC ──────────────────────────────────────────────────────────────

    def _auto_build_dbc(self):
        from core.auto_dbc import build_from_analyzer
        if self._state.frames_df.empty:
            QMessageBox.information(self, "No Data", "Load a CAN log first.")
            return
        signals = build_from_analyzer(self._state)
        added = 0
        existing_ids = {s.get("message_id") for s in self._state.dbc_signals}
        for sig in signals:
            if sig["message_id"] not in existing_ids:
                self._state.add_dbc_signal(sig)
                added += 1
        self.lbl_auto_dbc.setText(f"Added {added} signals to DBC Builder.")
        self.lbl_auto_dbc.setStyleSheet(f"color:{COLORS['green']}")

    # ── Diff ──────────────────────────────────────────────────────────────────

    def _set_baseline(self):
        if self._state.frames_df.empty:
            self.lbl_baseline.setText("Baseline: (no data)")
            return
        self._state.diff_baseline_df = self._state.frames_df.copy()
        n = len(self._state.diff_baseline_df)
        self.lbl_baseline.setText(f"Baseline: {n} frames")
        self.lbl_baseline.setStyleSheet(f"color:{COLORS['amber']}")

    def _run_diff(self):
        from core.diff_engine import diff_logs
        from core.log_parser import parse_log_file
        if self._state.diff_baseline_df.empty:
            QMessageBox.information(self, "No Baseline", "Set a baseline first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Comparison Log", "", "Log Files (*.csv *.log);;All (*)"
        )
        if not path:
            return
        try:
            comp_df = parse_log_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Parse Error", str(e))
            return
        results = diff_logs(self._state.diff_baseline_df, comp_df)
        self._populate_diff_table(results)

    def _populate_diff_table(self, results: list):
        visible = [r for r in results if r["status"] != "same"]
        self.diff_table.setRowCount(len(visible))
        for row, r in enumerate(visible):
            color = STATUS_COLORS.get(r["status"], COLORS["text"])
            changed_str = ", ".join(f"B{b}" for b in r["changed_bytes"]) or "—"
            cells = [
                f"0x{r['id']}",
                r["status"].upper(),
                str(r["baseline_count"]),
                str(r["compare_count"]),
                changed_str,
            ]
            for ci, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font())
                item.setForeground(QBrush(QColor(color)))
                self.diff_table.setItem(row, ci, item)

    # ── opendbc cross-ref ─────────────────────────────────────────────────────

    def _run_xref(self):
        from core.opendbc_matcher import scan
        if not self._state.dbc_signals:
            self.xref_text.setPlainText("No signals in DBC Builder yet.")
            return
        repo_ctx = None
        if self._state.repo_info:
            repo_ctx = {**self._state.repo_info, "readme": self._state.repo_readme}
        matches = scan(self._state, repo_ctx)
        self._state.opendbc_matches = matches
        if not matches:
            self.xref_text.setPlainText("No matches found against opendbc index.")
            return
        lines = []
        for sname, info in matches.items():
            partial = " (partial)" if info.get("partial") else ""
            lines.append(
                f"✓ {sname}{partial}  →  {info['file']}  "
                f"msg:{info['msg']}  id:{info['id']}"
            )
        self.xref_text.setPlainText("\n".join(lines))
        self._state.opendbc_matched.emit(matches)

    # ── Change-on-Action ──────────────────────────────────────────────────────

    def _get_recorder(self):
        if self._change_recorder is None:
            from core.change_detector import ChangeRecorder
            self._change_recorder = ChangeRecorder()
        return self._change_recorder

    def _coa_capture_baseline(self):
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load frames first.")
            return
        self._get_recorder().capture_baseline(df)
        self.lbl_coa_status.setText(f"Baseline captured  ({len(df)} frames)")
        self.lbl_coa_status.setStyleSheet(f"color:{COLORS['amber']}")

    def _coa_capture_action(self):
        df = self._state.frames_df
        if df.empty:
            return
        self._get_recorder().capture_action(df)
        self.lbl_coa_status.setText(f"Action captured  ({len(df)} frames)")
        self.lbl_coa_status.setStyleSheet(f"color:{COLORS['amber']}")

    def _coa_compute(self):
        deltas = self._get_recorder().compute_delta()
        if not deltas:
            QMessageBox.information(self, "No Deltas",
                                    "Capture both baseline and action first.")
            return
        self.delta_table.setRowCount(len(deltas))
        for row, d in enumerate(deltas):
            p_str    = f"{d['p_value']:.4f}" if d.get("p_value") is not None else "—"
            bits_str = ",".join(str(b) for b in d.get("changed_bits", []))
            direction = d.get("direction", "—")
            cells = [
                f"0x{d['id']}",
                f"B{d['byte']}",
                f"{int(d['before']):3d} (0x{int(d['before']):02X})",
                f"{int(d['after']):3d} (0x{int(d['after']):02X})",
                direction,
                p_str,
                bits_str,
            ]
            dir_color = {
                "RISING":    COLORS["green"],
                "FALLING":   COLORS["error"],
                "TOGGLE":    COLORS["amber"],
                "PULSE":     COLORS["amber"],
                "SUSTAINED": COLORS["green"],
            }.get(direction, COLORS["fg"])
            for ci, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font())
                color = dir_color if ci == 4 else COLORS["amber"]
                item.setForeground(QBrush(QColor(color)))
                self.delta_table.setItem(row, ci, item)
        self.lbl_coa_status.setText(f"{len(deltas)} byte changes detected")
        self.lbl_coa_status.setStyleSheet(f"color:{COLORS['green']}")
        self._state.change_detected.emit(deltas)

    def _coa_clear(self):
        if self._change_recorder:
            self._change_recorder.clear()
        self.delta_table.setRowCount(0)
        self.lbl_coa_status.setText("—")
        self.lbl_coa_status.setStyleSheet("")

    # ── Community Profiles ────────────────────────────────────────────────────

    def _comm_fetch(self):
        from core.community_sync import CommunitySyncWorker
        url = getattr(self._state, "community_profiles_url", "")
        if not url:
            QMessageBox.information(self, "No URL",
                "Set a Community Profiles URL in Settings → GITHUB.")
            return
        self._comm_worker = CommunitySyncWorker(url)
        self._comm_worker.profiles_ready.connect(self._on_profiles_ready)
        self._comm_worker.error.connect(
            lambda e: self.lbl_comm_status.setText(f"Error: {e}")
        )
        self._comm_worker.progress.connect(self.lbl_comm_status.setText)
        self._comm_worker.start()

    def _on_profiles_ready(self, profiles: list):
        self._state.community_profiles = profiles
        self.comm_list.setRowCount(len(profiles))
        for row, p in enumerate(profiles):
            cells = [
                p.get("id", "?"),
                p.get("vehicle", "?"),
                p.get("notes", ""),
            ]
            for ci, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font())
                self.comm_list.setItem(row, ci, item)
        self.lbl_comm_status.setText(f"{len(profiles)} profile(s) loaded")
        self.lbl_comm_status.setStyleSheet(f"color:{COLORS['green']}")

    def _comm_apply(self):
        row = self.comm_list.currentRow()
        if row < 0 or row >= len(self._state.community_profiles):
            QMessageBox.information(self, "No Selection",
                                    "Select a profile row first.")
            return
        profile = self._state.community_profiles[row]
        from core.community_sync import CommunitySyncWorker
        added = CommunitySyncWorker.apply_profile(self._state, profile)
        QMessageBox.information(self, "Applied",
            f"Profile '{profile.get('vehicle','?')}' applied — {added} signals added.")

    # ── J1939 ─────────────────────────────────────────────────────────────────

    def _run_j1939(self):
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load frames first.")
            return
        from core.j1939 import scan_for_j1939, decode_pgn
        hits = scan_for_j1939(df)
        if not hits:
            self.lbl_j1939.setText("No J1939 IDs detected (all IDs are ≤ 0x7FF).")
            return
        self.lbl_j1939.setText(f"{len(hits)} J1939 PGN(s) found.")

        # Populate right-panel J1939 table
        self.j1939_table.setRowCount(0)
        for h in hits:
            frames = df[df["ID"] == h["id_hex"]]
            # Decode first frame for SPN preview
            spn_preview = ""
            if not frames.empty:
                row = frames.iloc[0]
                data = bytes(int(row.get(f"B{i}", 0) or 0) for i in range(8))
                spns = decode_pgn(h["pgn"], data)
                if spns:
                    spn_preview = "  |  ".join(
                        f"{name}={v:.2f} {unit}" for name, (v, unit) in list(spns.items())[:3]
                    )
            r = self.j1939_table.rowCount()
            self.j1939_table.insertRow(r)
            cells = [
                h["id_hex"],
                f"0x{h['pgn']:04X}",
                h["pgn_name"],
                h["sa_name"],
                str(h["frame_count"]),
                spn_preview,
            ]
            for ci, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font(8))
                if ci == 2:
                    item.setForeground(QBrush(QColor(COLORS["green"])))
                self.j1939_table.setItem(r, ci, item)

    # ── Value Reverse Lookup ──────────────────────────────────────────────────

    def _run_value_reverse(self):
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load frames first.")
            return
        target = self.vr_target.value()
        tol    = self.vr_tol.value()
        from core.value_reverse import find_signal_for_value
        candidates = find_signal_for_value(df, target, tol)
        self.vr_table.setRowCount(0)
        if not candidates:
            self.lbl_vr.setText(f"No candidates found for target={target} ±{tol}.")
            return
        self.lbl_vr.setText(f"{len(candidates)} candidate(s) for target={target} ±{tol}.")
        for cand in candidates:
            r = self.vr_table.rowCount()
            self.vr_table.insertRow(r)
            cells = [
                f"0x{cand['id']}",
                f"B{cand['byte_idx']}",
                str(cand["length_bytes"]),
                cand["byte_order"],
                str(cand["scale"]),
                str(cand["offset"]),
                f"{cand['score']:.1%}",
                f"{cand['sample_median']:.3f}",
            ]
            score_color = COLORS["green"] if cand["score"] > 0.7 else COLORS["amber"]
            for ci, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font(8))
                if ci == 6:
                    item.setForeground(QBrush(QColor(score_color)))
                self.vr_table.setItem(r, ci, item)

    # ── State change handlers ─────────────────────────────────────────────────

    def _on_frames_loaded(self, count: int):
        pass

    def _on_dbc_updated(self):
        pass
