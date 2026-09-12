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
from PyQt6.QtCore    import Qt, pyqtSignal, QPoint
from PyQt6.QtGui     import QPainter, QPen, QBrush, QColor, QFont

from canlab.theme import COLORS, mono_font
from canlab.core.bit_coords import grid_to_dbc, dbc_to_grid


CELL  = 28    # px per bit cell
COLS  = 8
DEFAULT_ROWS = 8      # classic CAN; an FD message can be up to 64 rows
MAX_ROWS = 64


class _Grid(QWidget):
    """Raw 8×8 grid. Mouse events handle selection."""

    selection_changed = pyqtSignal(int, int)   # (first grid cell, cell count) of a drag

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sel_start  = -1          # drag range (grid indices) while the mouse is down
        self._sel_end    = -1
        self._cells: set = set()       # committed selection, painted after a drag
        self._hover_bit  = -1
        self._rows = DEFAULT_ROWS
        self._data_bytes = bytes(self._rows)
        self._apply_size()
        self.setMouseTracking(True)

    def _apply_size(self) -> None:
        self.setMinimumSize(COLS * CELL + 2, self._rows * CELL + 2)
        self.setMaximumSize(COLS * CELL + 2, self._rows * CELL + 2)

    @property
    def rows(self) -> int:
        return self._rows

    def set_rows(self, rows: int) -> None:
        """Size the grid to the message: 8 bytes for classic CAN, up to 64."""
        rows = max(1, min(MAX_ROWS, int(rows)))
        if rows == self._rows:
            return
        self._rows = rows
        self._data_bytes = self._data_bytes.ljust(rows, b"\x00")[:rows]
        self._cells = {c for c in self._cells if c < rows * COLS}
        self._apply_size()
        self.updateGeometry()
        self.update()

    def set_data(self, data: bytes) -> None:
        # A frame longer than the grid grows it; that is how an FD frame
        # arriving at the preview widens the editor without a separate step.
        if len(data) > self._rows:
            self.set_rows(len(data))
        self._data_bytes = data.ljust(self._rows, b"\x00")[:self._rows]
        self.update()

    def set_cells(self, cells) -> None:
        """Highlight exactly these grid cells and drop any drag range."""
        self._cells = set(int(c) for c in cells)
        self._sel_start = self._sel_end = -1
        self.update()

    @property
    def cells(self) -> set:
        return set(self._cells)

    def _bit_at(self, pos: QPoint) -> int:
        col = pos.x() // CELL
        row = pos.y() // CELL
        if 0 <= col < COLS and 0 <= row < self._rows:
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

        dragging = self._sel_start >= 0
        lo = min(self._sel_start, self._sel_end) if dragging else -1
        hi = max(self._sel_start, self._sel_end) if dragging else -1

        for bit in range(self._rows * COLS):
            selected = (lo <= bit <= hi) if dragging else (bit in self._cells)
            row = bit // COLS
            col = bit %  COLS
            x   = col * CELL
            y   = row * CELL

            # Bit value from data
            byte_i = bit // 8
            bit_i  = 7 - (bit % 8)    # MSB first visually
            val    = bool(self._data_bytes[byte_i] & (1 << bit_i)) if byte_i < len(self._data_bytes) else False

            # Background
            if selected:
                bg = QColor(COLORS["green"])
                bg.setAlpha(180)
            elif bit == self._hover_bit:
                bg = QColor(COLORS["amber"])
                bg.setAlpha(100)
            else:
                bg = QColor(COLORS["panel_bg"]) if row % 2 == 0 else QColor(COLORS["bg"])

            p.fillRect(x + 1, y + 1, CELL - 2, CELL - 2, QBrush(bg))

            # Bit value text
            text_color = QColor(COLORS["bg"]) if selected else (
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
        for row in range(self._rows):
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

    def set_rows(self, rows: int) -> None:
        """Size the grid to the message length: 8 for classic CAN, up to 64."""
        self._grid.set_rows(rows)

    def set_selection(self, start_bit: int, length: int, little_endian=None) -> None:
        """Show a DBC signal (start bit in DBC numbering) on the grid."""
        if little_endian is not None:
            self.chk_endian.blockSignals(True)
            self.chk_endian.setChecked(bool(little_endian))
            self.chk_endian.blockSignals(False)
        self._start_bit = int(start_bit)
        self._length    = int(length)
        self._grid.set_cells(dbc_to_grid(self._start_bit, self._length,
                                         self.chk_endian.isChecked()))
        self._update_label()

    def _on_grid_sel(self, first_cell: int, count: int):
        little = self.chk_endian.isChecked()
        start, length = grid_to_dbc(range(first_cell, first_cell + count), little)
        self.set_selection(start, length)
        self.selection_changed.emit(start, length, little)

    def _on_endian_toggle(self, checked: bool):
        # Keep the highlighted cells; re-derive the DBC start bit for the new order.
        cells = self._grid.cells
        if not cells:
            return
        start, length = grid_to_dbc(cells, checked)
        self.set_selection(start, length)
        self.selection_changed.emit(start, length, checked)

    def _update_label(self):
        order = "little-endian (LSB)" if self.chk_endian.isChecked() else "big-endian (MSB)"
        self.lbl_info.setText(
            f"start_bit: {self._start_bit}  length: {self._length}  [{order}]"
        )
