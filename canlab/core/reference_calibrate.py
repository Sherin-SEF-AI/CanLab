"""Reference-driven signal calibration + verification.

CanLab's other engines (entropy, correlation, checksum) are *reference-free*:
they surface candidate bytes but can't prove what a byte means. This module
closes the loop — given a time-aligned **physical reference** (vehicle speed from
OBD-II, GPS speed, or a value OCR'd from a dashboard video), it searches every
(ID, byte-range, endianness) candidate for the raw field whose values best map
linearly to the reference, fits scale/offset by least squares, and reports a
verification score (R²). A candidate is only accepted (PASS) when the fit is
strong — turning "here are candidate bytes" into "here is a confirmed signal
with proven scale and offset".

The fit is hardened by two refinements (see core.calibrate_refine, adapted from
CSS Electronics' MIT-licensed RE skills): "signal unavailable" sentinel codes are
masked out before fitting, and the fitted scale/offset are snapped to neat OEM
values when that barely moves the decode.

Public API:
    calibrate_against_reference(frames_df, ref_ts, ref_val, ...) -> list[dict]
    best_signal(frames_df, ref_ts, ref_val, ...) -> dict | None
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.calibrate_refine import mask_sentinels, snap_calibration

BYTE_COLS = [f"B{i}" for i in range(8)]

# Candidate field widths to search (bits). 8 and 16 cover the vast majority of
# real physical signals; 12 catches packed sensor values.
_DEFAULT_WIDTHS = (8, 12, 16)


def _byte_matrix(frames_for_id: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return (timestamps, byte_matrix[N,8]) for one ID, NaN bytes -> 0."""
    cols = [c for c in BYTE_COLS if c in frames_for_id.columns]
    ts = frames_for_id["Timestamp"].to_numpy(dtype=float)
    mat = np.zeros((len(frames_for_id), 8), dtype=np.float64)
    for i, c in enumerate(BYTE_COLS):
        if c in frames_for_id.columns:
            mat[:, i] = pd.to_numeric(frames_for_id[c], errors="coerce").fillna(0).to_numpy()
    return ts, mat


def _extract_raw(mat: np.ndarray, start_bit: int, length: int, big_endian: bool) -> np.ndarray | None:
    """Vectorized little/big-endian bit-field extraction from an (N,8) byte matrix.

    Little-endian follows CanLab's intra-frame convention (bit i of the field is
    bit (start_bit+i) of the 64-bit frame, B0 = bits 0-7). Big-endian is the
    byte-reversed reading of the same byte span (Motorola, byte-aligned).
    """
    end_bit = start_bit + length
    if end_bit > 64:
        return None
    # Build the field as an integer accumulator over the relevant bytes.
    raw = np.zeros(mat.shape[0], dtype=np.float64)
    if not big_endian:
        for i in range(length):
            bit = start_bit + i
            byte_idx = bit // 8
            bit_in_byte = bit % 8
            bits = (mat[:, byte_idx].astype(np.int64) >> bit_in_byte) & 1
            raw += bits * (1 << i)
    else:
        # Motorola: only byte-aligned, whole-byte widths supported.
        if start_bit % 8 != 0 or length % 8 != 0:
            return None
        n_bytes = length // 8
        start_byte = start_bit // 8
        if start_byte + n_bytes > 8:
            return None
        for j in range(n_bytes):
            raw = raw * 256 + mat[:, start_byte + j]
    return raw


def _align_to_reference(frame_ts: np.ndarray, raw: np.ndarray,
                        ref_ts: np.ndarray, ref_val: np.ndarray,
                        max_dt: float) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-neighbour align frame raw values onto reference samples."""
    order = np.argsort(ref_ts)
    rts, rval = ref_ts[order], ref_val[order]
    idx = np.searchsorted(rts, frame_ts)
    idx = np.clip(idx, 0, len(rts) - 1)
    # consider the neighbour on the left too
    left = np.clip(idx - 1, 0, len(rts) - 1)
    use_left = np.abs(rts[left] - frame_ts) < np.abs(rts[idx] - frame_ts)
    nn = np.where(use_left, left, idx)
    dt = np.abs(rts[nn] - frame_ts)
    ok = dt <= max_dt
    return raw[ok], rval[nn][ok]


def _linfit_r2(raw: np.ndarray, ref: np.ndarray) -> tuple[float, float, float]:
    """Least-squares fit ref = scale*raw + offset; return (scale, offset, r2)."""
    if len(raw) < 5 or np.std(raw) < 1e-9:
        return 0.0, 0.0, 0.0
    A = np.vstack([raw, np.ones_like(raw)]).T
    (scale, offset), _res, _rank, _sv = np.linalg.lstsq(A, ref, rcond=None)
    pred = scale * raw + offset
    ss_res = float(np.sum((ref - pred) ** 2))
    ss_tot = float(np.sum((ref - np.mean(ref)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return float(scale), float(offset), float(r2)


def calibrate_against_reference(
    frames_df: pd.DataFrame,
    ref_ts,
    ref_val,
    widths=_DEFAULT_WIDTHS,
    max_dt: float = 0.2,
    min_r2: float = 0.9,
    top_k: int = 10,
    ids: list[str] | None = None,
) -> list[dict]:
    """Search all (ID, byte-range, endianness) candidates for the field that best
    linearly explains the reference series.

    Returns a ranked list of candidate dicts:
        {"id","start_bit","length","byte_order","scale","offset","r2","n","verdict"}
    verdict is "PASS" when r2 >= min_r2, else "UNCONFIRMED".
    """
    if frames_df.empty:
        return []
    ref_ts = np.asarray(ref_ts, dtype=float)
    ref_val = np.asarray(ref_val, dtype=float)
    if len(ref_ts) < 5:
        return []

    id_list = ids if ids is not None else sorted(frames_df["ID"].unique())
    results: list[dict] = []

    for can_id in id_list:
        grp = frames_df[frames_df["ID"] == can_id]
        if len(grp) < 10:
            continue
        fts, mat = _byte_matrix(grp)
        # active byte count for this ID
        dlc = int(pd.to_numeric(grp["DLC"], errors="coerce").max()) if "DLC" in grp.columns else 8
        n_bits = min(max(dlc, 1), 8) * 8

        for length in widths:
            if length > n_bits:
                continue
            for start_bit in range(0, n_bits - length + 1):
                for big_endian in (False, True):
                    raw = _extract_raw(mat, start_bit, length, big_endian)
                    if raw is None or np.std(raw) < 1e-9:
                        continue
                    r, v = _align_to_reference(fts, raw, ref_ts, ref_val, max_dt)
                    if len(r) < 10:
                        continue
                    # Mask "signal unavailable" sentinels (0xFFFF, 0x3FFF, …) so a
                    # handful of out-of-band codes don't wreck the fit.
                    keep = mask_sentinels(r, length)
                    if keep.sum() >= 10:
                        r, v = r[keep], v[keep]
                    scale, offset, r2 = _linfit_r2(r, v)
                    if r2 <= 0:
                        continue

                    # Snap the fitted line to a neat OEM scale/offset when that
                    # barely moves the decode (0.09983 -> 0.1, offset -> 0).
                    snap = snap_calibration(scale, offset, r, v)
                    out_scale, out_offset, snapped = scale, offset, False
                    if snap and snap["auto"]:
                        out_scale, out_offset, snapped = snap["scale"], snap["offset"], True

                    results.append({
                        "id":        can_id,
                        "start_bit": start_bit,
                        "length":    length,
                        "byte_order": "big" if big_endian else "little",
                        "scale":     round(out_scale, 6),
                        "offset":    round(out_offset, 4),
                        "raw_scale": round(scale, 6),
                        "snapped":   snapped,
                        "sentinels_masked": int((~keep).sum()),
                        "r2":        round(r2, 4),
                        "n":         int(len(r)),
                        "verdict":   "PASS" if r2 >= min_r2 else "UNCONFIRMED",
                    })

    # De-dup overlapping wins per ID: keep the highest-r2 candidate first.
    results.sort(key=lambda d: d["r2"], reverse=True)
    return results[:top_k]


def best_signal(frames_df: pd.DataFrame, ref_ts, ref_val, **kw) -> dict | None:
    """Convenience: the single best-verified candidate, or None."""
    res = calibrate_against_reference(frames_df, ref_ts, ref_val, **kw)
    return res[0] if res else None


def candidate_to_signal_def(cand: dict, signal_name: str, unit: str = "") -> dict:
    """Turn a calibration result into a CanLab DBC signal-def dict."""
    return {
        "message_id":  cand["id"],
        "signal_name": signal_name,
        "start_bit":   cand["start_bit"],
        "length":      cand["length"],
        "byte_order":  cand["byte_order"],
        "value_type":  "unsigned",
        "scale":       cand["scale"],
        "offset":      cand["offset"],
        "min_val":     0,
        "max_val":     0,
        "unit":        unit,
        "description": f"Reference-calibrated (R²={cand['r2']}, n={cand['n']}, {cand['verdict']})",
    }
