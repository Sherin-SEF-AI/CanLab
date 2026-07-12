"""Calibration refinements: sentinel masking and OEM scale/offset snapping.

These algorithms are ported/adapted from CSS Electronics' "CAN bus reverse
engineering skills" (scripts/common.py), MIT-licensed:

    Copyright (c) 2026 CSS Electronics
    https://github.com/CSS-Electronics/can-bus-reverse-engineering-skills

Adapted for CanLab's native reference calibrator. Three ideas are carried over:

  1. Sentinel masking — "signal unavailable" codes (0xFFFF, 0x3FFF, most-negative)
     read far outside the real data band and wreck a least-squares fit. Mask them
     using a robust (median/MAD) band plus a structural-anchor confidence check.
  2. OEM scale snapping — OEM signals use round scales (m·10^k or 2^-j). Snap a
     fitted scale to the nearest such value when it's close.
  3. Bias-gated rounding — only apply the snap silently when it barely moves the
     decode (a systematic-bias budget, not R², which is blind to a ~2% slope error).
"""
from __future__ import annotations

import numpy as np


# ── OEM "nice" scale snapping ─────────────────────────────────────────────────

def nice_scale(scale: float, tol: float = 0.02) -> dict:
    """How close is |scale| to a 'nice' OEM value (m·10^k or 2^-j)?

    Returns {"nice": bool, "nearest": float, "rel_err": float}.
    """
    a = abs(float(scale))
    if a == 0.0 or not np.isfinite(a):
        return {"nice": False, "nearest": 0.0, "rel_err": float("inf")}
    cands = [m * 10.0 ** k for k in range(-12, 7) for m in (1.0, 2.0, 2.5, 5.0)]
    cands += [2.0 ** -j for j in range(0, 24)]
    cands = np.array(cands, dtype=np.float64)
    nearest = float(cands[np.argmin(np.abs(np.log(cands) - np.log(a)))])
    rel_err = abs(a - nearest) / nearest
    return {"nice": bool(rel_err <= tol), "nearest": nearest,
            "rel_err": round(float(rel_err), 4)}


# ── Robust band + structural anchors for sentinel detection ───────────────────

def robust_band(x: np.ndarray) -> tuple[float, float, float]:
    """Robust centre/scale of a value array -> (median, MAD, scale).

    MAD (1.4826-scaled) survives up to ~50% contamination, so a sentinel cluster
    doesn't drag the band onto itself. Falls back to a scaled IQR, floored at 1.0.
    """
    x = np.asarray(x, dtype=np.float64)
    med = float(np.median(x))
    mad = 1.4826 * float(np.median(np.abs(x - med)))
    if mad <= 0.0:
        q1, q3 = (float(v) for v in np.percentile(x, [25, 75]))
        mad = (q3 - q1) / 1.349
    return med, mad, max(mad, 1.0)


def structural_anchors(length: int) -> list[float]:
    """Unsigned codes that act as 'signal invalid' sentinels: 0, the all-ones top
    code, the most-negative (signed) code, and every shorter all-ones run 2^k-1."""
    L = int(length)
    anchors = {0.0, float((1 << L) - 1), float(1 << (L - 1))}
    anchors.update(float((1 << k) - 1) for k in range(1, L + 1))
    return sorted(anchors)


def mask_sentinels(raw: np.ndarray, length: int, k_band: float = 6.0,
                   max_frac: float = 0.45) -> np.ndarray:
    """Return a boolean 'keep' mask that drops sentinel/out-of-band samples.

    A sample is masked out when it sits far outside the robust band (|z| > k_band)
    AND its unsigned code is near a structural anchor (high-confidence sentinel).
    Bails (keeps everything) if that would remove more than max_frac of the data —
    a full-range continuous signal must not be gutted.
    """
    raw = np.asarray(raw, dtype=np.float64)
    keep = np.ones(len(raw), dtype=bool)
    finite = np.isfinite(raw)
    if finite.sum() < 20:
        return keep
    med, mad, scale = robust_band(raw[finite])
    z = np.abs((raw - med) / scale)
    far = finite & (z > k_band)
    if far.sum() == 0 or far.sum() / finite.sum() > max_frac:
        return keep
    # Confidence: the out-of-band code must be near a structural anchor.
    unsigned = np.mod(raw, float(1 << int(length)))
    anchors = np.array(structural_anchors(length))
    span = float(1 << int(length))
    for i in np.where(far)[0]:
        near = np.min(np.abs(anchors - unsigned[i])) <= max(2.0, 0.01 * span)
        if near:
            keep[i] = False
    return keep


# ── Bias-gated OEM snapping of a fitted line ──────────────────────────────────

def _snap_offset(off: float, rng: float) -> float:
    if abs(off) <= 0.02 * rng:
        return 0.0
    if abs(off - round(off)) <= 0.02 * rng:
        return float(round(off))
    return float(off)


def snap_calibration(scale: float, offset: float, raw: np.ndarray, ref: np.ndarray,
                     scale_tol: float = 0.03, bias_budget: float = 0.01) -> dict | None:
    """Snap a fitted (scale, offset) to neat OEM values, gated on systematic bias.

    Returns {"scale","offset","scale_changed","offset_changed","bias_frac","auto"}
    where auto=True means the snap barely moves the decode (safe to apply), or None
    when there's nothing to round.
    """
    raw = np.asarray(raw, dtype=np.float64)
    ref = np.asarray(ref, dtype=np.float64)
    v = np.isfinite(raw) & np.isfinite(ref)
    raw, ref = raw[v], ref[v]
    if len(raw) < 5 or np.ptp(raw) == 0:
        return None
    rng = abs(scale) * float(np.ptp(raw))
    if rng == 0:
        return None
    sp = nice_scale(scale, tol=scale_tol)
    ns = (-1.0 if scale < 0 else 1.0) * sp["nearest"] if sp["nice"] else float(scale)
    scale_changed = ns != scale
    if scale_changed:
        no = _snap_offset(float(np.median(ref - ns * raw)), rng)
    else:
        no = _snap_offset(float(offset), rng)
    offset_changed = no != offset
    if not (scale_changed or offset_changed):
        return None
    diff = (ns - scale) * raw + (no - offset)
    bias_frac = float(np.max(np.abs(diff))) / rng
    return {"scale": ns, "offset": no,
            "scale_changed": scale_changed, "offset_changed": offset_changed,
            "bias_frac": round(bias_frac, 4), "auto": bias_frac <= bias_budget}
