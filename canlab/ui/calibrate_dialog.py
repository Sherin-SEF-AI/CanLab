"""Calibrate signals against a reference file, off the GUI thread.

The old flow was a file picker and a message box: the sweep ran on the GUI
thread, froze the window for as long as it took, and the result was a wall of
text that could not be turned into a DBC signal. This dialog loads a CSV or
GPX, shows every series it holds, runs the sweep in a ``ProgressWorker`` with
a progress bar and a Cancel button, and turns the rows you pick into DBC
signals through ``state.add_dbc_signals`` (one undo step).

Nothing here transmits and nothing runs until Run is pressed.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from canlab.core.dbc_manager import dbc_identifier, validate_signals
from canlab.core.reference_calibrate import calibrate_many, candidate_to_signal_def
from canlab.core.reference_series import ReferenceSeries, load_reference_file
from canlab.theme import COLORS, mono_font
from canlab.ui.compute_worker import ProgressWorker

RESULT_COLUMNS = ("Reference", "ID", "Start bit", "Len", "Order", "Scale", "Offset",
                  "R2", "n", "Lag s", "Verdict")


class CalibrateDialog(QDialog):
    """Pick a reference file, run the sweep, add the fields it verifies."""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibrate signals from a reference")
        self.setMinimumSize(880, 620)
        self._state = state
        self._series: list[ReferenceSeries] = []
        self._results: list[dict] = []
        self._worker: ProgressWorker | None = None
        self.added = 0
        self._build()

    # ── layout ───────────────────────────────────────────────────────────────

    def _build(self):
        lay = QVBoxLayout(self)

        file_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("A CSV with a time column, or a GPX track")
        self.path_edit.setFont(mono_font(9))
        self.btn_open = QPushButton("Open…")
        self.btn_open.clicked.connect(self._pick_file)
        file_row.addWidget(QLabel("Reference:"))
        file_row.addWidget(self.path_edit, 1)
        file_row.addWidget(self.btn_open)
        lay.addLayout(file_row)

        self.series_table = QTableWidget(0, 5)
        self.series_table.setHorizontalHeaderLabels(["Use", "Series", "Unit", "Samples", "Span s"])
        self.series_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.series_table.verticalHeader().setVisible(False)
        self.series_table.setFont(mono_font(9))
        self.series_table.setMaximumHeight(150)
        lay.addWidget(self.series_table)

        form = QFormLayout()
        self.lag_spin = QDoubleSpinBox()
        self.lag_spin.setRange(0.0, 3600.0)
        self.lag_spin.setDecimals(1)
        self.lag_spin.setValue(30.0)
        self.lag_spin.setSuffix(" s")
        self.lag_spin.setToolTip("Search this far either way for the clock offset between "
                                 "the reference and the capture. 0 turns the search off.")
        form.addRow("Lag window:", self.lag_spin)
        self.min_r2_spin = QDoubleSpinBox()
        self.min_r2_spin.setRange(0.5, 1.0)
        self.min_r2_spin.setDecimals(2)
        self.min_r2_spin.setSingleStep(0.01)
        self.min_r2_spin.setValue(0.9)
        form.addRow("PASS at R2 of at least:", self.min_r2_spin)
        widths = QHBoxLayout()
        self.width_checks = {}
        for w in (8, 12, 16):
            chk = QCheckBox(f"{w}-bit")
            chk.setChecked(True)
            self.width_checks[w] = chk
            widths.addWidget(chk)
        widths.addStretch()
        form.addRow("Field widths:", widths)
        self.top_k_spin = QSpinBox()
        self.top_k_spin.setRange(1, 100)
        self.top_k_spin.setValue(8)
        form.addRow("Candidates per reference:", self.top_k_spin)
        lay.addLayout(form)

        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Run")
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self._run)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        run_row.addWidget(self.btn_run)
        run_row.addWidget(self.btn_cancel)
        run_row.addWidget(self.progress, 1)
        lay.addLayout(run_row)

        self.lbl_status = QLabel("Open a reference file to begin.")
        self.lbl_status.setFont(mono_font(8))
        self.lbl_status.setStyleSheet(f"color:{COLORS['dim']};")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        self.results_table = QTableWidget(0, len(RESULT_COLUMNS))
        self.results_table.setHorizontalHeaderLabels(list(RESULT_COLUMNS))
        self.results_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.setFont(mono_font(9))
        self.results_table.itemSelectionChanged.connect(self._sync_buttons)
        lay.addWidget(self.results_table, 1)

        add_row = QHBoxLayout()
        self.btn_add_selected = QPushButton("Add selected to DBC")
        self.btn_add_selected.setEnabled(False)
        self.btn_add_selected.clicked.connect(self._add_selected)
        self.btn_add_best = QPushButton("Add best per reference")
        self.btn_add_best.setEnabled(False)
        self.btn_add_best.clicked.connect(self._add_best)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        add_row.addWidget(self.btn_add_selected)
        add_row.addWidget(self.btn_add_best)
        add_row.addStretch()
        add_row.addWidget(self.btn_close)
        lay.addLayout(add_row)

    # ── the reference file ───────────────────────────────────────────────────

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Reference file", "",
            "Reference files (*.csv *.gpx);;CSV (*.csv);;GPX (*.gpx);;All Files (*)")
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> list[ReferenceSeries]:
        """Load a CSV or GPX and list its series. Returns them."""
        try:
            series = load_reference_file(path)
        except Exception as e:
            QMessageBox.warning(self, "Reference file", f"Could not read {path}:\n{e}")
            return []
        self._series = series
        self.path_edit.setText(path)
        self.series_table.setRowCount(0)
        for s in series:
            row = self.series_table.rowCount()
            self.series_table.insertRow(row)
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            use.setCheckState(Qt.CheckState.Checked)
            self.series_table.setItem(row, 0, use)
            name = QTableWidgetItem(s.name)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.series_table.setItem(row, 1, name)
            self.series_table.setItem(row, 2, QTableWidgetItem(s.unit))
            for col, text in ((3, str(s.samples)), (4, f"{s.span_s:.1f}")):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.series_table.setItem(row, col, item)
        self.btn_run.setEnabled(bool(series))
        self.lbl_status.setText(
            f"{Path(path).name}: {len(series)} series. Tick the ones to search for, "
            "set a unit if the file did not carry one, then Run.")
        return series

    def _chosen_series(self) -> list[ReferenceSeries]:
        chosen = []
        for row, s in enumerate(self._series):
            use = self.series_table.item(row, 0)
            if use is None or use.checkState() != Qt.CheckState.Checked:
                continue
            unit_item = self.series_table.item(row, 2)
            unit = unit_item.text().strip() if unit_item is not None else s.unit
            chosen.append(ReferenceSeries(s.name, s.ts, s.values, unit=unit, source=s.source))
        return chosen

    # ── the sweep ────────────────────────────────────────────────────────────

    def _run(self):
        refs = self._chosen_series()
        if not refs:
            self.lbl_status.setText("Tick at least one series.")
            return
        widths = tuple(w for w, chk in self.width_checks.items() if chk.isChecked())
        if not widths:
            self.lbl_status.setText("Tick at least one field width.")
            return
        df = self._state.frames_snapshot()
        if df is None or df.empty:
            self.lbl_status.setText("No capture is loaded.")
            return
        self._set_running(True)
        self.results_table.setRowCount(0)
        self._results = []
        self.progress.setValue(0)
        self.lbl_status.setText(f"Searching {df['ID'].nunique()} IDs for {len(refs)} series…")
        self._worker = ProgressWorker(
            calibrate_many, df, refs,
            window_s=float(self.lag_spin.value()),
            min_r2=float(self.min_r2_spin.value()),
            widths=widths, top_k=int(self.top_k_spin.value()))
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _cancel(self):
        if self._worker is not None:
            self._worker.stop()
            self.lbl_status.setText("Stopping after the current ID…")

    def _on_progress(self, done: int, total: int):
        self.progress.setValue(int(100 * done / max(1, total)))

    def _on_done(self, results):
        self._results = list(results or [])
        self._fill_results()
        self._set_running(False)
        stopped = self._worker is not None and self._worker.stopped
        passes = sum(1 for c in self._results if c.get("verdict") == "PASS")
        note = " (stopped early)" if stopped else ""
        if not self._results:
            self.lbl_status.setText("No candidate field found" + note + ".")
        else:
            self.lbl_status.setText(
                f"{len(self._results)} candidates, {passes} PASS{note}. Select rows and "
                "add them, or add the best per reference.")
        self.progress.setValue(100)

    def _on_failed(self, message: str):
        self._set_running(False)
        self.lbl_status.setText(f"Failed: {message}")

    def _set_running(self, running: bool):
        self.btn_run.setEnabled(not running and bool(self._series))
        self.btn_cancel.setEnabled(running)
        self.btn_open.setEnabled(not running)
        self.btn_add_best.setEnabled(not running and bool(self._results))
        self._sync_buttons()

    def _fill_results(self):
        self.results_table.setRowCount(0)
        for cand in self._results:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            cells = (cand.get("series", ""), cand["id"], str(cand["start_bit"]),
                     str(cand["length"]), cand["byte_order"], f"{cand['scale']:g}",
                     f"{cand['offset']:g}", f"{cand['r2']:.4f}", str(cand["n"]),
                     f"{cand.get('lag_s', 0.0):.1f}", cand["verdict"])
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == len(cells) - 1:
                    item.setForeground(
                        Qt.GlobalColor.green if text == "PASS" else Qt.GlobalColor.gray)
                self.results_table.setItem(row, col, item)

    def _sync_buttons(self):
        rows = self._selected_rows()
        self.btn_add_selected.setEnabled(bool(rows) and self._worker_idle())

    def _worker_idle(self) -> bool:
        return self._worker is None or not self._worker.isRunning()

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.results_table.selectedIndexes()})

    # ── into the DBC ─────────────────────────────────────────────────────────

    def _signals_for(self, cands: list[dict]) -> list[dict]:
        taken = {s.get("signal_name") for s in self._state.dbc_signals}
        out = []
        for cand in cands:
            base = dbc_identifier(f"{cand.get('series') or 'REF'}_{cand['id']}", "REF")
            name, n = base, 2
            while name in taken:
                name = f"{base}_{n}"
                n += 1
            taken.add(name)
            out.append(candidate_to_signal_def(cand, name, cand.get("unit", "")))
        return out

    def _add(self, cands: list[dict]):
        if not cands:
            return
        signals = self._signals_for(cands)
        errors = validate_signals(signals)
        if errors:
            QMessageBox.warning(self, "Calibrate", "Not added:\n" + "\n".join(errors[:6]))
            return
        n = self._state.add_dbc_signals(signals)
        self.added += n
        self.lbl_status.setText(f"Added {n} signal(s) to the DBC. Undo takes them all back.")

    def _add_selected(self):
        self._add([self._results[r] for r in self._selected_rows() if r < len(self._results)])

    def _add_best(self):
        best: dict[str, dict] = {}
        for cand in self._results:
            key = cand.get("series", "")
            if key not in best or cand["r2"] > best[key]["r2"]:
                best[key] = cand
        self._add(list(best.values()))

    # ── lifecycle ────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    def done(self, result: int):
        self._stop_worker()
        super().done(result)

    def _stop_worker(self):
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.stop()
            worker.wait(10_000)
