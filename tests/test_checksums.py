"""Checksum algorithms, against published check values and opendbc's own code.

The previous implementation could not match any real OEM checksum: its CRC-8
helper ignored the initial value it was given, and its "Hyundai" entries were
invented. These tests pin the algorithms to references that can be checked.
"""
import numpy as np
import pandas as pd
import pytest

from canlab.core.checksums import (ALGORITHMS, POLY_H2F, POLY_J1850, compute,
                                    crc8, hyundai_crc8)
from canlab.core.checksum_guesser import guess_all_bytes, guess_checksum

CHECK_INPUT = b"123456789"


def _opendbc_mk_crc8(poly, init_crc, xor_out):
    """commaai/opendbc's mk_crc8_fun, reproduced verbatim as the reference."""
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    init_reg = init_crc ^ xor_out

    def fn(data: bytes) -> int:
        reg = init_reg
        for b in data:
            reg = table[reg ^ b]
        return reg ^ xor_out
    return fn


@pytest.mark.parametrize("algo_id,expected", [
    ("crc8_j1850", 0x4B),      # CRC-8/SAE-J1850
    ("crc8_autosar", 0xDF),    # CRC-8/AUTOSAR (H2F)
    ("crc8", 0xF4),            # CRC-8 (poly 0x07)
])
def test_published_crc_check_values(algo_id, expected):
    """The standard check value: the CRC of the ASCII string "123456789"."""
    algo = ALGORITHMS[algo_id]
    assert algo.check == expected
    # cs_index past the end means "no byte excluded"
    assert algo.compute(CHECK_INPUT, 0, len(CHECK_INPUT)) == expected


def test_crc8_honours_its_initial_value():
    """The bug that made every OEM CRC unfindable: init was ignored."""
    assert crc8(b"\x00", POLY_J1850, init=0x00) != crc8(b"\x00", POLY_J1850, init=0xFF)
    assert crc8(b"", POLY_H2F, init=0xAB, xor_out=0x00) == 0xAB


@pytest.mark.parametrize("payload", [
    bytes(range(7)), b"\xde\xad\xbe\xef", bytes(7), b"\xff" * 7, bytes([0x10, 0x00, 0x55]),
])
def test_hyundai_crc_matches_opendbc(payload):
    reference = _opendbc_mk_crc8(0x1D, 0xFD, 0xDF)
    assert hyundai_crc8(payload) == reference(payload)


def test_toyota_matches_opendbc_formula():
    """opendbc toyota_checksum: len(d) + address bytes + all but the last byte."""
    data = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x00])
    address = 0x2E4
    expected = (len(data) + (0x2E4 & 0xFF) + (0x2E4 >> 8) + sum(data[:-1])) & 0xFF
    assert compute("toyota", data, address, 7) == expected


def test_subaru_matches_opendbc_formula():
    """opendbc subaru_checksum: address bytes + every byte after the first."""
    data = bytes([0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77])
    address = 0x220
    expected = ((0x220 & 0xFF) + (0x220 >> 8) + sum(data[1:])) & 0xFF
    assert compute("subaru", data, address, 0) == expected


def test_honda_is_a_four_bit_nibble_sum():
    data = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x80])
    got = compute("honda", data, 0x1FA, 7)
    assert 0 <= got <= 0xF


def test_simple_algorithms():
    data = bytes([0x01, 0x02, 0x03, 0x00])
    assert compute("xor8", data, 0, 3) == 0x00 ^ 0x01 ^ 0x02 ^ 0x03
    assert compute("sum8", data, 0, 3) == 6
    assert compute("sum8_inv", data, 0, 3) == (~6) & 0xFF
    assert compute("sum8_twos", data, 0, 3) == (-6) & 0xFF
    assert compute("nibble_sum", data, 0, 3) == 1 + 2 + 3


def _frames(make_checksum, can_id="1A0", n=200, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        d = [int(x) for x in rng.integers(0, 256, 7)]
        d.append(make_checksum(bytes(d)))
        rows.append({"Timestamp": i * 0.01, "ID": can_id,
                     **{f"B{k}": d[k] for k in range(8)}})
    return pd.DataFrame(rows)


def test_guesser_finds_a_hyundai_crc():
    df = _frames(hyundai_crc8)
    found = guess_all_bytes(df, "1A0")
    assert 7 in found
    assert "crc8_hyundai" in {m["algorithm"] for m in found[7]}


def test_guesser_finds_a_toyota_checksum():
    df = _frames(lambda d: compute("toyota", d + b"\x00", 0x2E4, 7), can_id="2E4")
    found = guess_all_bytes(df, "2E4")
    assert 7 in found and found[7][0]["algorithm"] == "toyota"


def test_guesser_finds_a_plain_sum():
    df = _frames(lambda d: sum(d) & 0xFF)
    found = guess_all_bytes(df, "1A0")
    assert "sum8" in {m["algorithm"] for m in found[7]}


def test_guesser_rejects_random_bytes():
    rng = np.random.default_rng(7)
    rows = [{"Timestamp": i * 0.01, "ID": "1A0",
             **{f"B{k}": int(rng.integers(0, 256)) for k in range(8)}}
            for i in range(200)]
    assert guess_all_bytes(pd.DataFrame(rows), "1A0") == {}


def test_guesser_needs_enough_frames():
    df = _frames(hyundai_crc8, n=20)
    assert guess_checksum(df, 7, "1A0") == []


def test_guesser_rejects_a_checksum_that_stops_holding():
    """A coincidence over the first frames must not pass validation."""
    df = _frames(hyundai_crc8, n=200)
    df.loc[150:, "B7"] = 0                       # the tail no longer matches
    assert guess_checksum(df, 7, "1A0") == []
