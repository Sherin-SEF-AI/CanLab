"""Conversions between the bit-editor grid and DBC bit numbering.

The editor draws byte ``b`` on row ``b`` with bit 7 at the left, so grid index
``g = b*8 + col`` shows DBC bit ``b*8 + (7 - col)``. ``cell`` maps between the
two and is its own inverse.

DBC start bits: little-endian signals start at their LSB and grow towards
higher bit numbers; big-endian (Motorola) signals start at their MSB and walk
the sawtooth (7→0 within a byte, then bit 7 of the next byte).
"""
from __future__ import annotations

from typing import Iterable


def cell(g: int) -> int:
    """Grid index <-> DBC bit number (self-inverse)."""
    return (g // 8) * 8 + (7 - g % 8)


def grid_to_dbc(selected: Iterable[int], little_endian: bool) -> tuple[int, int]:
    """(start_bit, length) for a set of selected grid cells."""
    sel = sorted(set(int(g) for g in selected))
    if not sel:
        return 0, 0
    length = len(sel)
    if little_endian:
        start = min(cell(g) for g in sel)          # LSB of the field
    else:
        start = cell(sel[0])                       # top-left cell is the MSB
    return start, length


def dbc_to_grid(start_bit: int, length: int, little_endian: bool) -> list[int]:
    """Grid cells covered by a DBC signal, in significance order (MSB last for LE)."""
    cells: list[int] = []
    if length <= 0:
        return cells
    if little_endian:
        for bit in range(start_bit, start_bit + length):
            if bit < 512:
                cells.append(cell(bit))
        return cells
    bit = start_bit
    for _ in range(length):
        if bit < 0 or bit >= 512:
            break
        cells.append(cell(bit))
        bit = bit + 15 if bit % 8 == 0 else bit - 1
    return cells
