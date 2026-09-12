"""Bit-editor grid <-> DBC bit numbering."""
import pytest

from canlab.core.bit_coords import cell, dbc_to_grid, grid_to_dbc
from canlab.core.dbc_manager import decode_frame


def test_cell_is_self_inverse():
    for g in range(64):
        assert cell(cell(g)) == g
    assert cell(0) == 7 and cell(7) == 0 and cell(8) == 15


@pytest.mark.parametrize("cells,little,expected", [
    (range(0, 16), True, (0, 16)),    # 16-bit field in B0..B1, little: LSB is bit 0
    (range(0, 16), False, (7, 16)),   # same cells, big: MSB is bit 7
    (range(16, 24), True, (16, 8)),   # byte B2
    (range(16, 24), False, (23, 8)),
    (range(4, 8), True, (0, 4)),      # low nibble of B0 (grid columns 4..7 = bits 3..0)
    (range(4, 8), False, (3, 4)),
])
def test_grid_to_dbc_vectors(cells, little, expected):
    assert grid_to_dbc(cells, little) == expected


def test_dbc_to_grid_orders():
    assert dbc_to_grid(0, 16, True) == [7, 6, 5, 4, 3, 2, 1, 0, 15, 14, 13, 12, 11, 10, 9, 8]
    assert dbc_to_grid(7, 16, False) == list(range(16))
    assert dbc_to_grid(23, 8, False) == list(range(16, 24))
    assert dbc_to_grid(0, 0, True) == []


@pytest.mark.parametrize("start,length", [(0, 16), (8, 4), (24, 12), (3, 5)])
def test_little_round_trip(start, length):
    cells = dbc_to_grid(start, length, True)
    assert grid_to_dbc(cells, True) == (start, length)


@pytest.mark.parametrize("first,count", [(0, 16), (5, 7), (16, 12), (60, 4)])
def test_big_endian_contiguous_drag_round_trips(first, count):
    start, length = grid_to_dbc(range(first, first + count), False)
    assert set(dbc_to_grid(start, length, False)) == set(range(first, first + count))


def test_vectors_agree_with_cantools_decoding():
    """A value written into the cells we claim a signal covers decodes as that value."""
    def payload_from_cells(cells):
        data = bytearray(8)
        for g in cells:
            bit = cell(g)
            data[bit // 8] |= 1 << (bit % 8)
        return bytes(data)
    base = {"message_id": "100", "message_name": "M", "signal_name": "S",
            "value_type": "unsigned", "scale": 1, "offset": 0}
    big = dict(base, start_bit=7, length=16, byte_order="big")
    little = dict(base, start_bit=0, length=16, byte_order="little")
    all_set = payload_from_cells(range(16))
    assert decode_frame([big], "100", all_set)["S"] == 0xFFFF
    assert decode_frame([little], "100", all_set)["S"] == 0xFFFF
    # only the MSB cell of the big-endian field set -> 0x8000
    assert decode_frame([big], "100", payload_from_cells([0]))["S"] == 0x8000
    # only the LSB of the little-endian field (grid cell 7 = bit 0) -> 1
    assert decode_frame([little], "100", payload_from_cells([7]))["S"] == 1
