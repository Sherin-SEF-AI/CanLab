"""Tests for the harvested calibration refinements (sentinel masking + OEM
scale snapping) and their effect on the reference calibrator."""
import numpy as np
import pytest
import pandas as pd

from canlab.core.calibrate_refine import nice_scale, mask_sentinels, snap_calibration  # noqa: F401
from canlab.core.reference_calibrate import best_signal


def test_nice_scale():
    assert nice_scale(0.09983)["nice"] and abs(nice_scale(0.09983)["nearest"] - 0.1) < 1e-9
    assert nice_scale(0.125)["nice"]                       # 2^-3
    assert not nice_scale(0.0013822)["nice"]               # proprietary, not nice


def test_mask_sentinels_drops_out_of_band_anchor_codes():
    # A clean ramp 0..500 with a few 0xFFFF (65535) "unavailable" spikes.
    raw = np.concatenate([np.linspace(0, 500, 200),
                          np.full(8, 65535.0)])
    keep = mask_sentinels(raw, length=16)
    assert keep[:200].all()          # real data kept
    assert not keep[200:].any()      # sentinels dropped


def test_mask_does_not_gut_continuous_signal():
    raw = np.linspace(0, 65535, 300)   # full-range continuous, no gap
    assert mask_sentinels(raw, length=16).all()


def test_snap_calibration_rounds_noise_scale():
    raw = np.linspace(0, 500, 300)
    ref = 0.0998 * raw + 0.02
    snap = snap_calibration(0.0998, 0.02, raw, ref)
    assert snap is not None and snap["auto"]
    assert abs(snap["scale"] - 0.1) < 1e-9
    assert snap["offset"] == 0.0


def _synth_with_sentinels(scale=0.1, n=300, noise=0.05):
    rng = np.random.default_rng(0)
    ts = np.arange(n) * 0.01
    speed = np.linspace(0, 60, n)
    raw = np.round(speed / scale).astype(int)
    rows = []
    for i in range(n):
        b = [0] * 8
        b[0] = i & 0xFF
        if i % 60 == 0:                 # periodic "signal unavailable" sentinel
            b[2] = 0xFF; b[3] = 0xFF
        else:
            b[2] = raw[i] & 0xFF; b[3] = (raw[i] >> 8) & 0xFF
        rows.append({"Timestamp": ts[i], "ID": "0A6", "Bus": 0, "DLC": 8,
                     **{f"B{k}": b[k] for k in range(8)}})
    # slightly biased/noisy reference so the raw fit lands near-but-not-exactly 0.1
    ref = speed + rng.normal(0, noise, n)
    return pd.DataFrame(rows), ts, ref


def test_end_to_end_masks_and_snaps():
    df, ref_ts, ref_val = _synth_with_sentinels()
    best = best_signal(df, ref_ts, ref_val, min_r2=0.95)
    assert best is not None and best["verdict"] == "PASS"
    assert best["sentinels_masked"] > 0          # sentinels were removed
    assert best["snapped"] is True               # scale snapped to a nice value
    # The winning window may be a bit-shifted equivalent (scale 0.1, 0.05, …) —
    # the contract is that whatever wins was snapped to a clean OEM value.
    assert nice_scale(best["scale"])["rel_err"] < 1e-6


# ── scales defined in another unit ───────────────────────────────────────────

def test_a_km_per_h_scale_snaps_when_the_reference_is_in_m_per_s():
    """0.01 km/h per bit is 0.0027778 m/s per bit: no round number in m/s."""
    raw = np.arange(0, 8000, 7, dtype=float)
    ref = raw * 0.0027790                          # within 0.05% of 0.01 km/h
    plain = snap_calibration(0.0027790, 0.0, raw, ref)
    assert plain is None or plain["native_unit"] == ""
    snap = snap_calibration(0.0027790, 0.0, raw, ref, unit="m/s")
    assert snap["auto"] and snap["native_unit"] == "km/h"
    assert snap["native_scale"] == pytest.approx(0.01)
    assert snap["scale"] == pytest.approx(0.01 / 3.6)


def test_the_bias_gate_still_refuses_a_snap_that_moves_the_decode():
    raw = np.arange(0, 8000, 7, dtype=float)
    snap = snap_calibration(0.00275, 0.0, raw, raw * 0.00275, unit="m/s")   # 1% off
    assert snap is None or not snap["auto"]


def test_degrees_and_radians():
    raw = np.arange(0, 3600, 3, dtype=float)
    scale = 0.1 * np.pi / 180                      # 0.1 degree per bit, reference in rad
    snap = snap_calibration(scale * 1.0003, 0.0, raw, raw * scale, unit="rad")
    assert snap["native_unit"] == "deg" and snap["native_scale"] == pytest.approx(0.1)
