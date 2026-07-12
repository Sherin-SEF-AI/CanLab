"""Tests for reference-driven calibration (#1)."""
import numpy as np
import pandas as pd

from core.reference_calibrate import calibrate_against_reference, best_signal


def _synth(scale=0.1, big_endian=False, start_byte=2, n=300):
    """A 16-bit field at start_byte encodes speed = raw*scale; build frames+ref."""
    ts = np.arange(n) * 0.01
    speed = np.linspace(0, 60, n)          # km/h reference
    raw = np.round(speed / scale).astype(int)   # what the ECU would transmit
    rows = []
    for i in range(n):
        b = [0] * 8
        b[0] = i & 0xFF                    # unrelated rolling counter
        if big_endian:
            b[start_byte]   = (raw[i] >> 8) & 0xFF
            b[start_byte+1] = raw[i] & 0xFF
        else:
            b[start_byte]   = raw[i] & 0xFF
            b[start_byte+1] = (raw[i] >> 8) & 0xFF
        rows.append({"Timestamp": ts[i], "ID": "0A6", "Bus": 0, "DLC": 8,
                     **{f"B{k}": b[k] for k in range(8)}})
    df = pd.DataFrame(rows)
    return df, ts, speed


def test_recovers_signal_with_high_r2_and_pass():
    # A field that linearly encodes the reference must be found and verified.
    # (Exact start_bit/endianness is intentionally not asserted: a linear signal
    #  read at a shifted offset is still linear, so several fields fit equally —
    #  the meaningful guarantee is a PASS with near-perfect R² on the right ID.)
    df, ref_ts, ref_val = _synth(scale=0.1, big_endian=False, start_byte=2)
    best = best_signal(df, ref_ts, ref_val, min_r2=0.95)
    assert best is not None
    assert best["verdict"] == "PASS"
    assert best["id"] == "0A6"
    assert best["r2"] > 0.99
    assert best["scale"] > 0


def test_big_endian_multibyte_signal_passes():
    df, ref_ts, ref_val = _synth(scale=0.05, big_endian=True, start_byte=3)
    best = best_signal(df, ref_ts, ref_val, min_r2=0.95)
    assert best is not None and best["verdict"] == "PASS"
    assert best["r2"] > 0.99


def test_unconfirmed_when_no_signal_matches():
    # Random reference unrelated to any byte -> no PASS.
    df, ref_ts, _ = _synth()
    rng = np.random.default_rng(0)
    noise = rng.normal(size=len(ref_ts))
    res = calibrate_against_reference(df, ref_ts, noise, min_r2=0.9)
    assert all(c["verdict"] == "UNCONFIRMED" for c in res)
