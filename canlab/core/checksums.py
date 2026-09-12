"""CAN checksum algorithms, including the OEM variants worth guessing against.

The previous guesser could only ever find a plain sum or XOR: its "Hyundai"
entries were invented, and its CRC-8 helper ignored the initial value it was
passed, so no real OEM CRC could match. These implementations follow
commaai/opendbc, which is the reference for the OEM ones.

Every algorithm has the same shape::

    compute(data: bytes, arb_id: int, cs_index: int) -> int

``data`` is the whole payload (the checksum byte included, since several
algorithms position themselves relative to it) and ``cs_index`` says which byte
holds the checksum, so it can be excluded from the sum.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# Standard CRC-8 polynomials.
POLY_J1850 = 0x1D      # SAE J1850 / AUTOSAR CRC8
POLY_H2F = 0x2F        # AUTOSAR CRC8H2F
POLY_CCITT = 0x07      # "plain" CRC-8


def crc8_table(poly: int) -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table


_TABLES: dict[int, list[int]] = {}


def _table(poly: int) -> list[int]:
    if poly not in _TABLES:
        _TABLES[poly] = crc8_table(poly)
    return _TABLES[poly]


def crc8(data: bytes, poly: int = POLY_J1850, init: int = 0x00,
         xor_out: int = 0x00) -> int:
    """Table-driven CRC-8. ``init`` seeds the register; ``xor_out`` is applied
    to the result (the old helper silently ignored ``init``)."""
    tab = _table(poly)
    reg = init & 0xFF
    for b in data:
        reg = tab[(reg ^ b) & 0xFF]
    return (reg ^ xor_out) & 0xFF


def _without(data: bytes, cs_index: int) -> bytes:
    return bytes(b for i, b in enumerate(data) if i != cs_index)


def _addr_bytes(arb_id: int) -> int:
    total, addr = 0, int(arb_id)
    while addr:
        total += addr & 0xFF
        addr >>= 8
    return total


def _addr_nibbles(arb_id: int) -> int:
    total, addr = 0, int(arb_id)
    while addr:
        total += addr & 0x0F
        addr >>= 4
    return total


# ── algorithms ───────────────────────────────────────────────────────────────

def _xor8(data, arb_id, cs_index):
    out = 0
    for b in _without(data, cs_index):
        out ^= b
    return out


def _sum8(data, arb_id, cs_index):
    return sum(_without(data, cs_index)) & 0xFF


def _sum8_inv(data, arb_id, cs_index):
    return (~sum(_without(data, cs_index))) & 0xFF

def _sum8_twos(data, arb_id, cs_index):
    return (-sum(_without(data, cs_index))) & 0xFF


def _nibble_sum(data, arb_id, cs_index):
    total = 0
    for b in _without(data, cs_index):
        total += (b & 0x0F) + (b >> 4)
    return total & 0xFF


def _crc(poly, init, xor_out):
    def compute(data, arb_id, cs_index):
        return crc8(_without(data, cs_index), poly, init, xor_out)
    return compute


def _toyota(data, arb_id, cs_index):
    """opendbc toyota_checksum: length + address bytes + the other data bytes."""
    return (len(data) + _addr_bytes(arb_id) + sum(_without(data, cs_index))) & 0xFF


def _subaru(data, arb_id, cs_index):
    """opendbc subaru_checksum: address bytes + the other data bytes."""
    return (_addr_bytes(arb_id) + sum(_without(data, cs_index))) & 0xFF


def _honda(data, arb_id, cs_index):
    """opendbc honda_checksum: a 4-bit nibble sum, in the low nibble of the
    last byte. The checksum nibble itself is excluded."""
    total = _addr_nibbles(arb_id)
    for i, b in enumerate(data):
        x = b >> 4 if i == len(data) - 1 else b
        total += (x & 0x0F) + (x >> 4)
    total = 8 - total
    if int(arb_id) > 0x7FF:
        total += 3
    return total & 0x0F


@dataclass(frozen=True)
class ChecksumAlgorithm:
    id: str
    name: str
    compute: Callable[[bytes, int, int], int]
    width: int = 8                    # bits the result occupies
    check: int | None = None          # CRC check value over b"123456789"
    fixed_index: int | None = None    # algorithms that define their own position


ALGORITHMS: dict[str, ChecksumAlgorithm] = {
    a.id: a for a in [
        ChecksumAlgorithm("xor8", "XOR of bytes", _xor8),
        ChecksumAlgorithm("sum8", "Sum of bytes", _sum8),
        ChecksumAlgorithm("sum8_inv", "Inverted sum (~sum)", _sum8_inv),
        ChecksumAlgorithm("sum8_twos", "Two's-complement sum (-sum)", _sum8_twos),
        ChecksumAlgorithm("nibble_sum", "Sum of nibbles", _nibble_sum),
        ChecksumAlgorithm("crc8_j1850", "CRC-8/SAE-J1850 (also Chrysler)",
                          _crc(POLY_J1850, 0xFF, 0xFF), check=0x4B),
        ChecksumAlgorithm("crc8_autosar", "CRC-8/AUTOSAR (H2F)",
                          _crc(POLY_H2F, 0xFF, 0xFF), check=0xDF),
        ChecksumAlgorithm("crc8", "CRC-8 (poly 0x07)",
                          _crc(POLY_CCITT, 0x00, 0x00), check=0xF4),
        # opendbc mk_crc8_fun seeds the register with init ^ xor_out, so the
        # Hyundai function is CRC8/J1850 with register seed 0xFD ^ 0xDF.
        ChecksumAlgorithm("crc8_hyundai", "CRC-8 Hyundai (J1850, init FD, xor DF)",
                          _crc(POLY_J1850, 0xFD ^ 0xDF, 0xDF)),
        ChecksumAlgorithm("toyota", "Toyota (len + address + bytes)", _toyota),
        ChecksumAlgorithm("subaru", "Subaru (address + bytes)", _subaru),
        ChecksumAlgorithm("honda", "Honda (nibble sum, 4-bit)", _honda,
                          width=4, fixed_index=-1),
    ]
}

ALGORITHM_IDS = list(ALGORITHMS)


def compute(algorithm: str, data: bytes, arb_id: int = 0, cs_index: int = -1) -> int:
    """Compute one algorithm over ``data`` (``cs_index`` may be negative)."""
    algo = ALGORITHMS[algorithm]
    if cs_index < 0:
        cs_index = len(data) + cs_index
    return algo.compute(bytes(data), int(arb_id), cs_index)


def hyundai_crc8(data: bytes) -> int:
    """The Hyundai CRC-8 over exactly the bytes given (no exclusion)."""
    return crc8(bytes(data), POLY_J1850, 0xFD ^ 0xDF, 0xDF)
