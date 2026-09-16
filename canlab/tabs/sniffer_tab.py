"""SNIFFER: one row per message, coloured by what just moved.

Modelled on SavvyCAN's sniffer window and cansniffer. The table is the same
size as the bus, not the same size as the log, so you can watch it while you
press something. Green means a byte went up, red means it went down, and both
fade back to grey after a second.

Notch is the reason to use this rather than the frames table: it records every
bit currently in motion and ignores it from then on, so a couple of presses
leave a quiet display in which the bit you cause is the only thing that lights.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from canlab.core.sniffer import FELL, ROSE, Sniffer
from canlab.core.state import get_state
from canlab.theme import COLORS, mono_font

REFRESH_MS = 200                     # SavvyCAN's cycle, and fast enough to see
BYTE_COLS = 8
_HEADERS = ["ID", "Hz", "Count", "Age"] + [f"B{i}" for i in range(BYTE_COLS)]


class SnifferTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = get_state()
        self._sniffer = Sniffer()
        self._cursor = 0             # how many frames of the store we have read
        self._paused = False
        self._bits_view = False
        self._rows: dict[str, int] = {}
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._state.frames_loaded.connect(self._on_reload)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        bar = QHBoxLayout()
        self.btn_notch = QPushButton("Notch")
        self.btn_notch.setToolTip(
            "Ignore every bit that is moving right now. Press it a few times "
            "with the vehicle idling, then act: what lights up is what you did.")
        self.btn_notch.clicked.connect(self._notch)
        self.btn_unnotch = QPushButton("Un-notch")
        self.btn_unnotch.setToolTip("Forget the ignored bits and colour everything again.")
        self.btn_unnotch.clicked.connect(self._unnotch)
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self._on_pause)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self._clear)
        for b in (self.btn_notch, self.btn_unnotch, self.btn_pause, self.btn_clear):
            b.setFont(mono_font(8))
            bar.addWidget(b)

        self.chk_never_expire = QCheckBox("Keep silent IDs")
        self.chk_never_expire.setToolTip(
            "An ID that stops transmitting disappears after 5 seconds. "
            "Tick this to keep it on screen.")
        self.chk_never_expire.toggled.connect(
            lambda v: setattr(self._sniffer, "never_expire", v))
        self.chk_mute = QCheckBox("Blank notched bits")
        self.chk_mute.setToolTip("Show notched bits as zero instead of their value.")
        self.chk_mute.toggled.connect(
            lambda v: setattr(self._sniffer, "mute_notched", v))
        self.chk_bits = QCheckBox("Bits")
        self.chk_bits.setToolTip("Show each byte as eight bits instead of hex.")
        self.chk_bits.toggled.connect(self._on_bits_toggled)
        for c in (self.chk_never_expire, self.chk_mute, self.chk_bits):
            c.setFont(mono_font(8))
            bar.addWidget(c)

        bar.addStretch()
        self.lbl_status = QLabel("0 IDs")
        self.lbl_status.setFont(mono_font(8))
        self.lbl_status.setObjectName("label_dim")
        bar.addWidget(self.lbl_status)
        lay.addLayout(bar)

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setFont(mono_font())
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in enumerate((70, 60, 70, 60)):
            self.table.setColumnWidth(col, width)
        self._size_byte_columns()
        lay.addWidget(self.table)

        hint = QLabel(
            "Green: the byte went up. Red: it went down. Both fade after a second. "
            "Double the value of Notch by pressing it while nothing is happening.")
        hint.setFont(mono_font(8))
        hint.setObjectName("label_dim")
        hint.setWordWrap(True)
        lay.addWidget(hint)

    def _size_byte_columns(self):
        width = 92 if self._bits_view else 34
        for i in range(BYTE_COLS):
            self.table.setColumnWidth(4 + i, width)

    # ── controls ──────────────────────────────────────────────────────────────
    def _notch(self):
        added = self._sniffer.notch()
        self.lbl_status.setText(f"notched {added} more bit(s); "
                                f"{self._sniffer.notched_bits()} ignored")
        self._refresh_table(force=True)

    def _unnotch(self):
        self._sniffer.unnotch()
        self._refresh_table(force=True)

    def _on_pause(self, paused: bool):
        self._paused = paused
        self.btn_pause.setText("Resume" if paused else "Pause")

    def _clear(self):
        """Start the view again.

        On a live bus that means "from now on", so the cursor jumps to the end
        and the next frames off the wire fill it. On a loaded capture there is
        no "from now on": the file has already been read, so jumping to the end
        left the table empty for good and the only way back was to reopen the
        log. Clearing a static capture therefore rewinds to the beginning
        instead, which is what the button appears to promise.
        """
        self._sniffer.clear()
        self._rows.clear()
        self.table.setRowCount(0)
        self._cursor = len(self._state.store) if self._state.is_connected else 0

    def _on_bits_toggled(self, on: bool):
        self._bits_view = on
        self.table.setRowCount(0)     # cell widths change, so rebuild
        self._rows.clear()
        self._size_byte_columns()
        self._refresh_table(force=True)

    def _on_reload(self, _count: int):
        """A newly opened log replaces everything, so start again."""
        self._clear()
        self._cursor = 0

    def _on_row_selected(self):
        items = self.table.selectedItems()
        if items:
            self._state.select_id(self.table.item(items[0].row(), 0).text())

    # ── the loop ──────────────────────────────────────────────────────────────
    def _tick(self):
        if self._paused or not self.isVisible():
            return
        # Frames from a bus carry wall time; frames from a file carry the
        # capture's clock, and on that clock nothing ever gets older.
        self._sniffer.live = bool(self._state.is_connected)
        self._consume_new_frames()
        self._refresh_table()

    def _consume_new_frames(self):
        """Everything appended since the last tick, live or from a loaded log."""
        store = self._state.store
        total = len(store)
        if total < self._cursor:      # the ring wrapped or the capture changed
            self._cursor = 0
        if total == self._cursor:
            return
        new = min(total - self._cursor, 20000)
        self._sniffer.update_dataframe(store.tail(new))
        self._cursor = total

    def _refresh_table(self, force: bool = False):
        now = self._sniffer.clock()
        rows = self._sniffer.snapshot(now)
        if not rows and not force:
            self.table.setRowCount(0)
            self._rows.clear()
            return
        self.table.setUpdatesEnabled(False)
        try:
            # Rebuild only when the set of IDs changes; otherwise write in
            # place, so a 200 ms tick over 180 IDs costs almost nothing.
            ids = [r.can_id for r in rows]
            if ids != list(self._rows):
                self.table.setRowCount(len(rows))
                self._rows = {cid: i for i, cid in enumerate(ids)}
                for cid, i in self._rows.items():
                    for col in range(len(_HEADERS)):
                        item = QTableWidgetItem("")
                        item.setFont(mono_font())
                        if col >= 4:
                            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        self.table.setItem(i, col, item)
                    self.table.item(i, 0).setText(cid)
            for row in rows:
                self._write_row(self._rows[row.can_id], row, now)
        finally:
            self.table.setUpdatesEnabled(True)
        self.lbl_status.setText(
            f"{len(rows)} ID(s)" + (f", {self._sniffer.notched_bits()} bit(s) notched"
                                    if self._sniffer.notched_bits() else ""))

    def _write_row(self, i: int, row, now: float):
        self.table.item(i, 0).setText(row.can_id)
        self.table.item(i, 1).setText(f"{row.rate_hz:.0f}" if row.rate_hz else "")
        self.table.item(i, 2).setText(str(row.count))
        age = row.age(now)
        age_item = self.table.item(i, 3)
        age_item.setText(f"{age:.1f}s")
        age_item.setForeground(QBrush(QColor(
            COLORS["error"] if age > 2.0 else COLORS["dim"])))

        steady = QColor(COLORS["text"])
        faded = QColor(COLORS["dim"])
        for b in range(BYTE_COLS):
            item = self.table.item(i, 4 + b)
            if b >= len(row.data):
                item.setText("")
                item.setBackground(QBrush(Qt.GlobalColor.transparent))
                continue
            if self._bits_view:
                bits = row.bits()[b]
                item.setText("".join(str(v) for v in bits))
            else:
                item.setText(f"{row.data[b]:02X}")
            direction = row.direction[b] if b < len(row.direction) else 0
            if direction == ROSE:
                item.setForeground(QBrush(QColor(COLORS["green"])))
                item.setBackground(QBrush(QColor("#04220f")))
            elif direction == FELL:
                item.setForeground(QBrush(QColor(COLORS["error"])))
                item.setBackground(QBrush(QColor("#240808")))
            else:
                # A byte that has never moved is not worth reading, so it
                # sits back while the active ones stay bright.
                changed_ever = row.changes[b] if b < len(row.changes) else 0
                item.setForeground(QBrush(steady if changed_ever else faded))
                item.setBackground(QBrush(Qt.GlobalColor.transparent))

    def cleanup(self):
        self._timer.stop()
