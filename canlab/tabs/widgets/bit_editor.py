"""
Interactive 64-bit grid widget.

Displays an 8×8 grid (8 rows = bytes, 8 columns = bits).
Click-and-drag selects a contiguous bit range.
Emits selection_changed(start_bit, length, is_little_endian).

Bit numbering follows cantools / DBC convention:
  Little-endian: LSB = start_bit, MSBit at start_bit + length - 1
  Big-endian (Motorola): MSB = start_bit
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox
from PyQt6.QtCore    import Qt, pyqtSignal, QRect, QPoint
from PyQt6.QtGui     import QPainter, QPen, QBrush, QColor, QFont

from theme import COLORS, mono_font


CELL  = 28    # px per bit cell
ROWS  = 8
COLS  = 8
TOTAL = ROWS * COLS   # 64 bits


class _Grid(QWidget):
    """Raw 8×8 grid. Mouse events handle selection."""

    selection_changed = pyqtSignal(int, int)   # (start_bit, length)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sel_start  = -1
        self._sel_end    = -1
        self._hover_bit  = -1
        self._data_bytes = bytes(8)
        self.setMinimumSize(COLS * CELL + 2, ROWS * CELL + 2)
        self.setMaximumSize(COLS * CELL + 2, ROWS * CELL + 2)
        self.setMouseTracking(True)

    def set_data(self, data: bytes) -> None:
        self._data_bytes = data.ljust(8, b"\x00")[:8]
        self.update()

    def set_selection(self, start_bit: int, length: int) -> None:
        self._sel_start = start_bit
        self._sel_end   = start_bit + length - 1
        self.update()

    def _bit_at(self, pos: QPoint) -> int:
        col = pos.x() // CELL
        row = pos.y() // CELL
        if 0 <= col < COLS and 0 <= row < ROWS:
            return row * COLS + col
        return -1

    def mousePressEvent(self, e):
        bit = self._bit_at(e.pos())
        if bit >= 0:
            self._sel_start = bit
            self._sel_end   = bit
            self.update()

    def mouseMoveEvent(self, e):
        bit = self._bit_at(e.pos())
        self._hover_bit = bit
        if e.buttons() & Qt.MouseButton.LeftButton and self._sel_start >= 0 and bit >= 0:
            self._sel_end = bit
            self.update()
            self._emit()
        else:
            self.update()

    def mouseReleaseEvent(self, e):
        bit = self._bit_at(e.pos())
        if bit >= 0 and self._sel_start >= 0:
            self._sel_end = bit
            self.update()
            self._emit()

    def _emit(self):
        lo = min(self._sel_start, self._sel_end)
        hi = max(self._sel_start, self._sel_end)
        self.selection_changed.emit(lo, hi - lo + 1)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        lo = min(self._sel_start, self._sel_end) if self._sel_start >= 0 else -1
        hi = max(self._sel_start, self._sel_end) if self._sel_start >= 0 else -1

        for bit in range(TOTAL):
            row = bit // COLS
            col = bit %  COLS
            x   = col * CELL
            y   = row * CELL

            # Bit value from data
            byte_i = bit // 8
            bit_i  = 7 - (bit % 8)    # MSB first visually
            val    = bool(self._data_bytes[byte_i] & (1 << bit_i)) if byte_i < len(self._data_bytes) else False

            # Background
            if lo <= bit <= hi:
                bg = QColor(COLORS["green"])
                bg.setAlpha(180)
            elif bit == self._hover_bit:
                bg = QColor(COLORS["amber"])
                bg.setAlpha(100)
            else:
                bg = QColor(COLORS["panel_bg"]) if row % 2 == 0 else QColor(COLORS["bg"])

            p.fillRect(x + 1, y + 1, CELL - 2, CELL - 2, QBrush(bg))

            # Bit value text
            text_color = QColor(COLORS["bg"]) if lo <= bit <= hi else (
                QColor(COLORS["green"]) if val else QColor(COLORS["dim"])
            )
            p.setPen(text_color)
            p.setFont(QFont("Courier New", 8))
            p.drawText(x + 1, y + 1, CELL - 2, CELL - 2,
                       Qt.AlignmentFlag.AlignCenter, "1" if val else "0")

            # Border
            p.setPen(QPen(QColor(COLORS["border"]), 1))
            p.drawRect(x, y, CELL, CELL)

        # Byte labels on left (row headers)
        p.setPen(QColor(COLORS["dim"]))
        p.setFont(QFont("Courier New", 7))
        for row in range(ROWS):
            p.drawText(-20, row * CELL, 18, CELL,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"B{row}")

        p.end()


class BitGridWidget(QWidget):
    """
    Full bit-editor widget: grid + info label + endianness toggle.

    Emits selection_changed(start_bit, length, is_little_endian).
    """

    selection_changed = pyqtSignal(int, int, bool)   # start_bit, length, little_endian

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        lay.addWidget(QLabel("BIT EDITOR  (drag to select signal range)", font=mono_font(8)))

        # Grid (add left margin for row labels)
        grid_row = QHBoxLayout()
        grid_row.addSpacing(24)    # room for byte labels
        self._grid = _Grid()
        self._grid.selection_changed.connect(self._on_grid_sel)
        grid_row.addWidget(self._grid)
        grid_row.addStretch()
        lay.addLayout(grid_row)

        # Bit-index header
        hdr = QHBoxLayout()
        hdr.addSpacing(24)
        for i in range(8):
            lbl = QLabel(str(7 - i))
            lbl.setFixedWidth(CELL)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(QFont("Courier New", 7))
            lbl.setStyleSheet(f"color:{COLORS['dim']}")
            hdr.addWidget(lbl)
        hdr.addStretch()
        lay.insertLayout(1, hdr)   # insert before grid

        # Info + endianness
        info_row = QHBoxLayout()
        self.lbl_info = QLabel("start_bit: —  length: —")
        self.lbl_info.setFont(mono_font(8))
        info_row.addWidget(self.lbl_info)
        info_row.addStretch()
        self.chk_endian = QCheckBox("Little-endian")
        self.chk_endian.setChecked(True)
        self.chk_endian.toggled.connect(self._on_endian_toggle)
        info_row.addWidget(self.chk_endian)
        lay.addLayout(info_row)

        self._start_bit = 0
        self._length    = 0

    def set_data(self, data: bytes) -> None:
        self._grid.set_data(data)

    def set_selection(self, start_bit: int, length: int) -> None:
        self._start_bit = start_bit
        self._length    = length
        self._grid.set_selection(start_bit, length)
        self._update_label()

    def _on_grid_sel(self, start_bit: int, length: int):
        self._start_bit = start_bit
        self._length    = length
        self._update_label()
        self.selection_changed.emit(
            start_bit, length, self.chk_endian.isChecked()
        )

    def _on_endian_toggle(self, _checked: bool):
        if self._length > 0:
            self.selection_changed.emit(
                self._start_bit, self._length, self.chk_endian.isChecked()
            )

    def _update_label(self):
        self.lbl_info.setText(
            f"start_bit: {self._start_bit}  length: {self._length}  "
            f"bits [{self._start_bit}…{self._start_bit + self._length - 1}]"
        )
