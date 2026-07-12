"""Tests for the harvested calibration refinements (sentinel masking + OEM
scale snapping) and their effect on the reference calibrator."""
import numpy as np
import pandas as pd

from core.calibrate_refine import nice_scale, mask_sentinels, snap_calibration  # noqa: F401
from core.reference_calibrate import best_signal


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
