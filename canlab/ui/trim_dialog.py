"""Trim the loaded capture down to the part worth analysing.

SavvyCAN's Bisector, plus a time window. The count updates as you type, so you
can see what a bound does before committing, and the result either replaces
the loaded capture (one undo-free step, so it asks) or is written to a file.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QRadioButton, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from canlab.core import capture_split
from canlab.theme import COLORS, mono_font


class TrimDialog(QDialog):
    """Pick a subset. ``result_frames`` holds it once the dialog is accepted."""

    def __init__(self, df, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Trim capture")
        self.setMinimumWidth(520)
        self._df = df
        self.result_frames = None
        self.replace_loaded = True
        self._build()
        self._recalculate()

    def _build(self):
        lay = QVBoxLayout(self)
        n = len(self._df)
        span = 0.0
        if n:
            span = float(self._df["Timestamp"].max() - self._df["Timestamp"].min())
        head = QLabel(f"Loaded: {n} frames, {self._df['ID'].nunique() if n else 0} IDs, "
                      f"{span:.2f} s")
        head.setFont(mono_font(9))
        lay.addWidget(head)

        self.mode_combo = QComboBox()
        for label, key in (("Time window", "time"), ("Frame numbers", "frames"),
                           ("Percentage", "percent"), ("Arbitration IDs", "ids"),
                           ("Bus", "bus")):
            self.mode_combo.addItem(label, key)
        self.mode_combo.setFont(mono_font(9))
        form = QFormLayout()
        form.addRow("Keep by:", self.mode_combo)
        lay.addLayout(form)

        self.pages = QStackedWidget()
        lay.addWidget(self.pages)

        # time
        page = QWidget(); f = QFormLayout(page)
        self.t_from = _spin(0.0, 0.0, max(span, 1e6))
        self.t_to = _spin(span or 1.0, 0.0, max(span, 1e6))
        f.addRow("From (s):", self.t_from)
        f.addRow("To (s):", self.t_to)
        self.chk_relative = QCheckBox("Seconds from the first frame")
        self.chk_relative.setChecked(True)
        f.addRow("", self.chk_relative)
        self.pages.addWidget(page)

        # frames
        page = QWidget(); f = QFormLayout(page)
        self.f_from = _int_spin(0, 0, max(n - 1, 0))
        self.f_to = _int_spin(max(n - 1, 0), 0, max(n - 1, 0))
        f.addRow("First frame:", self.f_from)
        f.addRow("Last frame:", self.f_to)
        self.pages.addWidget(page)

        # percent
        page = QWidget(); f = QFormLayout(page)
        self.p_from = _spin(0.0, 0.0, 100.0, step=5.0)
        self.p_to = _spin(50.0, 0.0, 100.0, step=5.0)
        f.addRow("From (%):", self.p_from)
        f.addRow("To (%):", self.p_to)
        self.pages.addWidget(page)

        # ids
        page = QWidget(); f = QFormLayout(page)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("100, 1A0, 200-2FF")
        self.id_edit.setFont(mono_font(9))
        f.addRow("IDs:", self.id_edit)
        self.pages.addWidget(page)

        # bus
        page = QWidget(); f = QFormLayout(page)
        self.bus_spin = _int_spin(0, 0, 15)
        f.addRow("Bus:", self.bus_spin)
        self.pages.addWidget(page)

        self.chk_invert = QCheckBox("Invert: throw this away and keep the rest")
        lay.addWidget(self.chk_invert)

        self.lbl_preview = QLabel("")
        self.lbl_preview.setFont(mono_font(8))
        self.lbl_preview.setWordWrap(True)
        lay.addWidget(self.lbl_preview)

        dest = QHBoxLayout()
        self.radio_replace = QRadioButton("Replace the loaded capture")
        self.radio_replace.setChecked(True)
        self.radio_file = QRadioButton("Save to a file instead")
        dest.addWidget(self.radio_replace)
        dest.addWidget(self.radio_file)
        dest.addStretch()
        lay.addLayout(dest)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        self.mode_combo.currentIndexChanged.connect(self.pages.setCurrentIndex)
        self.mode_combo.currentIndexChanged.connect(self._recalculate)
        self.chk_invert.toggled.connect(self._recalculate)
        self.chk_relative.toggled.connect(self._recalculate)
        self.id_edit.textChanged.connect(self._recalculate)
        for w in (self.t_from, self.t_to, self.p_from, self.p_to):
            w.valueChanged.connect(self._recalculate)
        for w in (self.f_from, self.f_to, self.bus_spin):
            w.valueChanged.connect(self._recalculate)

    # -- preview ---------------------------------------------------------------
    def _args(self) -> tuple[str, dict]:
        mode = self.mode_combo.currentData()
        invert = self.chk_invert.isChecked()
        if mode == "time":
            return mode, {"start_s": self.t_from.value(), "end_s": self.t_to.value(),
                          "relative": self.chk_relative.isChecked(), "invert": invert}
        if mode == "frames":
            return mode, {"first": self.f_from.value(), "last": self.f_to.value(),
                          "invert": invert}
        if mode == "percent":
            return mode, {"first_pct": self.p_from.value(), "last_pct": self.p_to.value(),
                          "invert": invert}
        if mode == "ids":
            return mode, {"ids": self.id_edit.text(), "invert": invert}
        return mode, {"bus": self.bus_spin.value(), "invert": invert}

    def _recalculate(self, *_):
        mode, kw = self._args()
        try:
            self._result = capture_split.split(self._df, mode, **kw)
        except Exception as e:                       # a half-typed ID range
            self.lbl_preview.setText(f"Cannot split: {e}")
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            return
        keeping = self._result.n_kept
        self.lbl_preview.setText(self._result.summary())
        self.lbl_preview.setStyleSheet(
            f"color:{COLORS['dim'] if keeping else COLORS['error']}")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(keeping > 0)

    def _accept(self):
        self.result_frames = self._result.kept
        self.replace_loaded = self.radio_replace.isChecked()
        if self.replace_loaded and self._result.n_dropped:
            ok = QMessageBox.question(
                self, "Trim capture",
                f"This discards {self._result.n_dropped} frames from the loaded "
                "capture. The file on disk is not touched, but the frames go "
                "from memory and cannot be brought back without reopening it.\n\n"
                "Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ok != QMessageBox.StandardButton.Yes:
                return
        self.accept()


def _spin(value: float, lo: float, hi: float, step: float = 0.1) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setDecimals(3)
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setValue(value)
    w.setFont(mono_font(9))
    return w


def _int_spin(value: int, lo: int, hi: int) -> QSpinBox:
    w = QSpinBox()
    w.setRange(lo, max(hi, lo))
    w.setValue(value)
    w.setFont(mono_font(9))
    return w
