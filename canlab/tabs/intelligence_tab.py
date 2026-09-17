"""INTELLIGENCE tab — auto-DBC, diff, periodicity, opendbc cross-ref, J1939, value lookup."""
from PyQt6.QtWidgets import (
    QScrollArea, QFrame,
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QFileDialog,
    QTextEdit, QMessageBox, QLineEdit, QListWidget, QDoubleSpinBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush

from canlab.theme import COLORS, mono_font, desc_label
from canlab.ui.tokens import MAX_H
from canlab.core.state import get_state
from canlab.ui.widgets import set_status


STATUS_COLORS = {
    "added":   COLORS["green"],
    "removed": COLORS["error"],
    "changed": COLORS["amber"],
    "same":    COLORS["dim"],
}


def _frame_bytes(row) -> bytes:
    """The payload of one frames row, as bytes.

    A frame shorter than eight bytes leaves NaN in the columns it does not
    reach. `row.get("B7", 0) or 0` looks like it handles that and does not:
    NaN is truthy, so the NaN survives the `or` and int() raises on it, which
    took down the whole PGN scan on the first short frame in a capture.
    """
    out = bytearray()
    for i in range(8):
        value = row.get(f"B{i}", None)
        if value is None or value != value:        # NaN is not equal to itself
            break
        out.append(int(value) & 0xFF)
    return bytes(out)


def _decoded_preview(decoded: dict) -> str:
    """One line describing a decoded message, whatever shape it came back in.

    Most PGNs decode to {name: (value, unit)}, but DM1 is active fault codes
    and comes back as lamps plus a list. Unpacking that as a value and a unit
    raised, so a J1939 log containing any fault-code message took the whole
    scan down with it.
    """
    if not decoded:
        return ""
    if "dtcs" in decoded:
        codes = decoded.get("dtcs") or []
        lamps = [name for name, on in (decoded.get("lamps") or {}).items() if on]
        if not codes:
            return "no active codes" + (f"; lamps: {', '.join(lamps)}" if lamps else "")
        first = codes[0]
        summary = ", ".join(f"SPN {c.get('spn')} FMI {c.get('fmi')}" for c in codes[:2])
        return (f"{len(codes)} active code{'s' if len(codes) != 1 else ''}: {summary}"
                if first else f"{len(codes)} active codes")
    parts = []
    for name, value in list(decoded.items())[:3]:
        if isinstance(value, tuple) and len(value) == 2:
            number, unit = value
            parts.append(f"{name}={number:g} {unit}".rstrip())
        else:
            parts.append(f"{name}={value}")
    return "  |  ".join(parts)


class IntelligenceTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = get_state()
        self._build_ui()
        self._state.frames_loaded.connect(self._on_frames_loaded)
        self._state.dbc_updated.connect(self._on_dbc_updated)
        # Marks arrive from outside this tab too: a loaded project, the REST
        # /mark endpoint, the live watch. Without these the list only ever
        # refreshed from its own buttons, so a project's marks were invisible.
        self._state.project_loaded.connect(self._ann_refresh_list)
        self._state.annotations_changed.connect(self._ann_refresh_list)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left panel ────────────────────────────────────────────────────────
        left = QWidget()
        left.setMinimumWidth(240)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.setSpacing(6)

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

        # Annotated capture: mark when something happened, rank what tracked it.
        ann_grp = QGroupBox("ANNOTATED CAPTURE")
        ann_lay = QVBoxLayout(ann_grp)
        ann_lay.addWidget(desc_label(
            "Mark when you did something (press the brake, flick the "
            "indicator) and every byte and bit on the bus is ranked by how "
            "well it followed your marks."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Label:", font=mono_font(8)))
        self.ann_label = QLineEdit("brake")
        self.ann_label.setFont(mono_font(8))
        row.addWidget(self.ann_label, 1)
        ann_lay.addLayout(row)
        self.btn_ann_toggle = QPushButton("Start")
        self.btn_ann_toggle.setObjectName("btn_green")
        self.btn_ann_toggle.setCheckable(True)
        self.btn_ann_toggle.toggled.connect(self._ann_toggle)
        ann_lay.addWidget(self.btn_ann_toggle)
        manual = QHBoxLayout()
        self.ann_start = QDoubleSpinBox()
        self.ann_end = QDoubleSpinBox()
        for sp in (self.ann_start, self.ann_end):
            sp.setRange(0, 1e9)
            sp.setDecimals(2)
            sp.setSuffix(" s")
            sp.setFont(mono_font(8))
        self.btn_ann_add = QPushButton("Add range")
        self.btn_ann_add.clicked.connect(self._ann_add_manual)
        manual.addWidget(self.ann_start)
        manual.addWidget(self.ann_end)
        manual.addWidget(self.btn_ann_add)
        ann_lay.addLayout(manual)
        self.ann_list = QListWidget()
        self.ann_list.setFont(mono_font(8))
        self.ann_list.setMaximumHeight(110)
        ann_lay.addWidget(self.ann_list)
        btns = QHBoxLayout()
        self.btn_ann_rank = QPushButton("Rank candidates")
        self.btn_ann_rank.setObjectName("btn_green")
        self.btn_ann_rank.clicked.connect(self._ann_rank)
        self.btn_ann_remove = QPushButton("Remove")
        self.btn_ann_remove.clicked.connect(self._ann_remove)
        self.btn_ann_clear = QPushButton("Clear")
        self.btn_ann_clear.clicked.connect(self._ann_clear)
        for b in (self.btn_ann_rank, self.btn_ann_remove, self.btn_ann_clear):
            btns.addWidget(b)
        ann_lay.addLayout(btns)
        self.lbl_ann_status = QLabel("No marks yet.")
        self.lbl_ann_status.setFont(mono_font(8))
        self.lbl_ann_status.setObjectName("label_dim")
        ann_lay.addWidget(self.lbl_ann_status)
        ll.addWidget(ann_grp)

        # J1939 Decoder
        j1939_grp = QGroupBox("J1939 / NMEA 2000 PGN DECODER")
        j1939_lay = QVBoxLayout(j1939_grp)
        self.btn_j1939 = QPushButton("Scan for PGNs")
        self.btn_j1939.clicked.connect(self._run_j1939)
        j1939_lay.addWidget(self.btn_j1939)
        self.lbl_j1939 = QLabel("—")
        self.lbl_j1939.setFont(mono_font(8))
        j1939_lay.addWidget(self.lbl_j1939)
        ll.addWidget(j1939_grp)

        # Value Reverse Lookup
        vr_grp = QGroupBox("VALUE REVERSE LOOKUP")
        vr_lay = QVBoxLayout(vr_grp)
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

        # The control column is a tall stack of group boxes. Left as a plain
        # widget its full height became the minimum height of the whole
        # application window; in a scroll area it can shrink and scroll.
        left_scroll = QScrollArea()
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(240)
        left_scroll.setMaximumWidth(420)
        splitter.addWidget(left_scroll)

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

        # J1939 table
        self.j1939_table = QTableWidget(0, 7)
        self.j1939_table.setHorizontalHeaderLabels(
            ["ID", "PGN", "PGN Name", "Protocol", "Source", "Frames", "Decoded"]
        )
        self.j1939_table.setFont(mono_font(8))
        self.j1939_table.verticalHeader().setVisible(False)
        self.j1939_table.verticalHeader().setDefaultSectionSize(20)
        # The identifier, PGN and frame count are short and fixed; the decoded
        # values are the column worth reading, so give the spare width to that
        # one rather than spreading it over all seven.
        header = self.j1939_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.j1939_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.j1939_table.setMaximumHeight(MAX_H["xl"])
        rl.addWidget(QLabel("PGN SCAN RESULTS", font=mono_font(8)))
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

        self.ann_table = QTableWidget(0, 6)
        self.ann_table.setHorizontalHeaderLabels(
            ["Label", "Location", "r", "On", "Off", "What it did"])
        self.ann_table.setFont(mono_font())
        self.ann_table.verticalHeader().setVisible(False)
        self.ann_table.verticalHeader().setDefaultSectionSize(20)
        self.ann_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ann_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ann_table.setToolTip("Double-click a row to add it to the DBC as a signal.")
        self.ann_table.doubleClicked.connect(self._ann_add_signal)
        rl.addWidget(QLabel("ANNOTATION CANDIDATES  (double-click to add to DBC)",
                            font=mono_font(8)))
        rl.addWidget(self.ann_table)

        splitter.setSizes([240, 760])
        outer.addWidget(splitter)
        self._change_recorder = None

    # ── Periodicity ───────────────────────────────────────────────────────────

    def _compute_periodicity(self):
        from canlab.core.periodicity import compute_periodicity, classify_period
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
        from canlab.core.auto_dbc import build_from_analyzer
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
        set_status(self.lbl_auto_dbc, "ok")

    # ── Diff ──────────────────────────────────────────────────────────────────

    def _set_baseline(self):
        if self._state.frames_df.empty:
            self.lbl_baseline.setText("Baseline: (no data)")
            return
        self._state.diff_baseline_df = self._state.frames_df.copy()
        n = len(self._state.diff_baseline_df)
        self.lbl_baseline.setText(f"Baseline: {n} frames")
        set_status(self.lbl_baseline, "warn")

    def _run_diff(self):
        from canlab.core.diff_engine import diff_logs
        from canlab.core.log_parser import parse_log_file
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
        from canlab.core.opendbc_matcher import scan
        if not self._state.dbc_signals:
            self.xref_text.setPlainText("No signals in DBC Builder yet.")
            return
        matches = scan(self._state)
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

    # ── Change-on-Action ──────────────────────────────────────────────────────

    def _get_recorder(self):
        if self._change_recorder is None:
            from canlab.core.change_detector import ChangeRecorder
            self._change_recorder = ChangeRecorder()
        return self._change_recorder

    def _coa_capture_baseline(self):
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load frames first.")
            return
        self._get_recorder().capture_baseline(df)
        self.lbl_coa_status.setText(f"Baseline captured  ({len(df)} frames)")
        set_status(self.lbl_coa_status, "warn")

    def _coa_capture_action(self):
        df = self._state.frames_df
        if df.empty:
            return
        self._get_recorder().capture_action(df)
        self.lbl_coa_status.setText(f"Action captured  ({len(df)} frames)")
        set_status(self.lbl_coa_status, "warn")

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
            }.get(direction, COLORS["text"])
            for ci, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font())
                color = dir_color if ci == 4 else COLORS["amber"]
                item.setForeground(QBrush(QColor(color)))
                self.delta_table.setItem(row, ci, item)
        self.lbl_coa_status.setText(f"{len(deltas)} byte changes detected")
        set_status(self.lbl_coa_status, "ok")
        self._state.change_detected.emit(deltas)

    def _coa_clear(self):
        if self._change_recorder:
            self._change_recorder.clear()
        self.delta_table.setRowCount(0)
        self.lbl_coa_status.setText("—")
        self.lbl_coa_status.setStyleSheet("")

    # ── Annotated capture ─────────────────────────────────────────────────

    def _ann_now(self) -> float:
        """The clock the frames use: wall time while live, capture time when
        working from a file (the newest frame, so a mark lands at the end)."""
        import time
        if self._state.is_connected:
            return time.time()
        df = self._state.frames_df
        return float(df["Timestamp"].max()) if len(df) else 0.0

    def _ann_toggle(self, on: bool):
        label = self.ann_label.text().strip() or "action"
        ann = self._state.annotations
        if on:
            ann.begin(label, self._ann_now())
            self.btn_ann_toggle.setText(f"Stop '{label}'")
            self.lbl_ann_status.setText(f"Marking '{label}'…")
        else:
            ann.end(label, self._ann_now())
            self.btn_ann_toggle.setText("Start")
            self._ann_refresh_list()

    def _ann_add_manual(self):
        label = self.ann_label.text().strip() or "action"
        a, b = self.ann_start.value(), self.ann_end.value()
        if b <= a:
            self.lbl_ann_status.setText("End must be after start.")
            return
        self._state.annotations.add(label, a, b)
        self._ann_refresh_list()

    def _ann_refresh_list(self):
        self.ann_list.clear()
        for a in self._state.annotations.items:
            end = f"{a.end:.2f}" if a.closed else "open"
            self.ann_list.addItem(f"{a.label:<12} {a.start:.2f} → {end}")
        n = len(self._state.annotations.items)
        self.lbl_ann_status.setText(f"{n} mark(s).")

    def _ann_remove(self):
        row = self.ann_list.currentRow()
        if row >= 0:
            self._state.annotations.remove(row)
            self._ann_refresh_list()

    def _ann_clear(self):
        self._state.annotations.clear()
        self.ann_table.setRowCount(0)
        self._ann_refresh_list()

    def _ann_rank(self):
        from canlab.core.annotations import rank_candidates
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load or capture frames first.")
            return
        ann = self._state.annotations
        if not any(a.closed for a in ann.items):
            QMessageBox.information(self, "No marks",
                                    "Add at least one closed mark first.")
            return
        cands = rank_candidates(df, ann)
        self._ann_candidates = cands
        self.ann_table.setRowCount(len(cands))
        for r, c in enumerate(cands):
            cells = [c.label, c.location, f"{c.r:+.2f}", str(c.frames_on),
                     str(c.frames_off), c.describe().split(": ", 1)[-1]]
            for col, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font())
                if col == 2:
                    strong = c.strength >= 0.8
                    item.setForeground(QBrush(QColor(
                        COLORS["green"] if strong else COLORS["amber"])))
                self.ann_table.setItem(r, col, item)
        self.lbl_ann_status.setText(
            f"{len(cands)} candidate(s) for {len(ann.labels())} label(s).")

    def _ann_add_signal(self, index):
        from canlab.core.annotations import candidate_to_signal
        cands = getattr(self, "_ann_candidates", [])
        row = index.row()
        if 0 <= row < len(cands):
            self._state.add_dbc_signal(candidate_to_signal(cands[row]))
            self.lbl_ann_status.setText(f"Added {cands[row].location} to the DBC.")

    # ── J1939 / NMEA 2000 ─────────────────────────────────────────────────────

    def _run_j1939(self):
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load frames first.")
            return
        from canlab.core.j1939 import scan_for_j1939, decode_pgn
        hits = scan_for_j1939(df)
        if not hits:
            self.lbl_j1939.setText(
                "No 29-bit IDs, so neither J1939 nor NMEA 2000 (all IDs are ≤ 0x7FF).")
            return
        # Say which protocol the bus actually is. Both use the same 29-bit
        # frame, and reporting a marine bus as J1939 was misleading enough that
        # the PGN names came out blank.
        counts: dict[str, int] = {}
        for h in hits:
            counts[h["protocol"]] = counts.get(h["protocol"], 0) + 1
        named = sum(1 for h in hits if not h["pgn_name"].startswith("PGN "))
        summary = ", ".join(f"{n} {proto}" for proto, n in
                            sorted(counts.items(), key=lambda kv: -kv[1]))
        self.lbl_j1939.setText(f"{len(hits)} PGNs: {summary}. {named} named.")

        # Populate right-panel J1939 table
        self.j1939_table.setRowCount(0)
        for h in hits:
            frames = df[df["ID"] == h["id_hex"]]
            # Decode first frame for SPN preview
            spn_preview = ""
            if not h["single_frame"]:
                # An NMEA 2000 fast-packet message is split across frames with
                # a sequence byte. Decoding one frame of it in isolation gives
                # a confident wrong answer, so say nothing instead.
                spn_preview = "fast packet, needs reassembly"
            elif not frames.empty:
                spn_preview = _decoded_preview(
                    decode_pgn(h["pgn"], _frame_bytes(frames.iloc[0])))
            r = self.j1939_table.rowCount()
            self.j1939_table.insertRow(r)
            cells = [
                h["id_hex"],
                f"0x{h['pgn']:04X}" if h["protocol"] == "J1939" else str(h["pgn"]),
                h["pgn_name"],
                h["protocol"],
                h["sa_name"],
                str(h["frame_count"]),
                spn_preview,
            ]
            for ci, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setFont(mono_font(8))
                if ci == 2 and not txt.startswith("PGN "):
                    item.setForeground(QBrush(QColor(COLORS["green"])))
                elif ci == 6 and txt.startswith("fast packet"):
                    item.setForeground(QBrush(QColor(COLORS["dim"])))
                self.j1939_table.setItem(r, ci, item)

    # ── Value Reverse Lookup ──────────────────────────────────────────────────

    def _run_value_reverse(self):
        df = self._state.frames_df
        if df.empty:
            QMessageBox.information(self, "No Data", "Load frames first.")
            return
        target = self.vr_target.value()
        tol    = self.vr_tol.value()
        from canlab.core.value_reverse import find_signal_for_value
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
