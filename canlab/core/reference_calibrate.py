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

Two clocks rarely agree. A GPS logger stamps epoch seconds while a capture
may start at zero, and even on the same clock a phone and an adapter drift a
few seconds apart. ``find_time_offset`` searches a window of lags for the one
that makes some field in the capture line up with the reference; the result
is fed back as ``lag_s`` so the fit sees the right samples. Overlapping wins
on one ID (the 16-bit word and the 8-bit byte inside it) are collapsed to
the best, so the ranked list is one row per distinct field.

Public API:
    calibrate_against_reference(frames_df, ref_ts, ref_val, ...) -> list[dict]
    best_signal(frames_df, ref_ts, ref_val, ...) -> dict | None
    find_time_offset(frames_df, ref_ts, ref_val, ...) -> dict
    calibrate_with_lag_search(frames_df, series, ...) -> list[dict]
    calibrate_many(frames_df, refs, ...) -> list[dict]
    candidate_to_signal_def(cand, name, unit) -> dict
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from canlab.core.calibrate_refine import mask_sentinels, snap_calibration
from canlab.core.reference_series import ReferenceSeries

BYTE_COLS = [f"B{i}" for i in range(8)]

# Candidate field widths to search (bits). 8 and 16 cover the vast majority of
# real physical signals; 12 catches packed sensor values.
_DEFAULT_WIDTHS = (8, 12, 16)


def _byte_matrix(frames_for_id: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return (timestamps, byte_matrix[N,8]) for one ID, NaN bytes -> 0."""
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
    lag_s: float = 0.0,
    dedup: bool = True,
    progress_cb=None,
    should_stop=None,
) -> list[dict]:
    """Search all (ID, byte-range, endianness) candidates for the field that best
    linearly explains the reference series.

    ``lag_s`` is subtracted from the reference timestamps first: it is the
    amount the reference clock runs ahead of the capture clock, which is what
    ``find_time_offset`` returns. With ``dedup`` each ID keeps only its best
    candidate per overlapping bit span. ``progress_cb(done, total)`` is called
    once per ID and ``should_stop()`` is polled between IDs.

    Returns a ranked list of candidate dicts:
        {"id","start_bit","length","byte_order","scale","offset","r2","n",
         "verdict","lag_s"}
    verdict is "PASS" when r2 >= min_r2, else "UNCONFIRMED".
    """
    if frames_df.empty:
        return []
    ref_ts = np.asarray(ref_ts, dtype=float) - float(lag_s)
    ref_val = np.asarray(ref_val, dtype=float)
    finite = np.isfinite(ref_ts) & np.isfinite(ref_val)
    ref_ts, ref_val = ref_ts[finite], ref_val[finite]
    if len(ref_ts) < 5:
        return []

    id_list = ids if ids is not None else sorted(frames_df["ID"].unique())
    results: list[dict] = []

    for index, can_id in enumerate(id_list):
        if should_stop is not None and should_stop():
            break
        if progress_cb is not None:
            progress_cb(index, len(id_list))
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
                        "lag_s":     round(float(lag_s), 3),
                    })

    if progress_cb is not None:
        progress_cb(len(id_list), len(id_list))
    # Best fit first. On a tie (the same bytes read two ways, which happens
    # when a message repeats a word or a neighbouring byte is always zero),
    # prefer whole-byte widths, byte-aligned starts, then the wider reading,
    # so a 16-bit word at byte 2 outranks the 12-bit slice inside it and the
    # same word read from bit 9.
    results.sort(key=lambda d: (-d["r2"], d["length"] % 8 != 0,
                                d["start_bit"] % 8 != 0, -d["length"]))
    if dedup:
        results = _dedup_overlaps(results)
    return results[:top_k]


def _span(cand: dict) -> tuple[int, int]:
    """The bit span a candidate reads, as [first, last) in frame bits.

    Both byte orders are searched over byte-aligned spans, so the candidate's
    ``start_bit`` is the low end of the span whichever way it is read.
    """
    return int(cand["start_bit"]), int(cand["start_bit"]) + int(cand["length"])


def _dedup_overlaps(results: list[dict]) -> list[dict]:
    """Keep the best candidate per overlapping bit span on each ID.

    ``results`` must already be sorted best first. A 16-bit word that fits
    also makes its high byte fit, less well; showing both as separate finds
    doubles the list without adding a field.
    """
    kept: list[dict] = []
    taken: dict[str, list[tuple[int, int]]] = {}
    for cand in results:
        lo, hi = _span(cand)
        spans = taken.setdefault(cand["id"], [])
        if any(lo < b and a < hi for a, b in spans):
            continue
        spans.append((lo, hi))
        kept.append(cand)
    return kept


def best_signal(frames_df: pd.DataFrame, ref_ts, ref_val, **kw) -> dict | None:
    """Convenience: the single best-verified candidate, or None."""
    res = calibrate_against_reference(frames_df, ref_ts, ref_val, **kw)
    return res[0] if res else None


def candidate_to_signal_def(cand: dict, signal_name: str, unit: str = "") -> dict:
    """Turn a calibration result into a CanLab DBC signal-def dict.

    The search reads a big-endian field from the low end of its byte span, but
    a DBC Motorola start bit names the most significant bit, which sits in the
    first byte of that span: ``start_byte * 8 + 7``. Little-endian start bits
    are the LSB and pass through unchanged.
    """
    start_bit = int(cand["start_bit"])
    if cand.get("byte_order") == "big":
        start_bit = (start_bit // 8) * 8 + 7
    detail = f"R2={cand['r2']}, n={cand['n']}, {cand['verdict']}"
    if cand.get("series"):
        detail = f"{cand['series']}, " + detail
    if cand.get("lag_s"):
        detail += f", lag {cand['lag_s']} s"
    return {
        "message_id":  cand["id"],
        "signal_name": signal_name,
        "start_bit":   start_bit,
        "length":      cand["length"],
        "byte_order":  cand["byte_order"],
        "value_type":  "unsigned",
        "scale":       cand["scale"],
        "offset":      cand["offset"],
        "min_val":     0,
        "max_val":     0,
        "unit":        unit or cand.get("unit", ""),
        "description": f"Reference-calibrated ({detail})",
    }


# ── clock offset ─────────────────────────────────────────────────────────────

def _coarse_fields(mat: np.ndarray) -> np.ndarray:
    """The 22 byte-aligned readings of an (N, 8) byte matrix as (N, 22)."""
    cols = [mat[:, i] for i in range(8)]
    cols += [mat[:, i] + 256.0 * mat[:, i + 1] for i in range(7)]        # little
    cols += [256.0 * mat[:, i] + mat[:, i + 1] for i in range(7)]        # big
    return np.stack(cols, axis=1)


_FIELD_LABELS = ([(i * 8, 8, "little") for i in range(8)]
                 + [(i * 8, 16, "little") for i in range(7)]
                 + [(i * 8, 16, "big") for i in range(7)])


def _bin_means(ts: np.ndarray, values: np.ndarray, t0: float, step: float,
               n_bins: int) -> np.ndarray:
    """Mean of ``values`` per time bin, NaN where a bin is empty.

    ``values`` may be (N,) or (N, K); the result is (n_bins,) or (n_bins, K).
    """
    idx = np.floor((ts - t0) / step).astype(int)
    ok = (idx >= 0) & (idx < n_bins)
    idx, values = idx[ok], values[ok]
    shape = (n_bins,) + values.shape[1:]
    total = np.zeros(shape)
    count = np.zeros(n_bins)
    np.add.at(total, idx, values)
    np.add.at(count, idx, 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        if values.ndim == 1:
            return np.where(count > 0, total / count, np.nan)
        return np.where(count[:, None] > 0, total / count[:, None], np.nan)


def _pearson_columns(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """|r| between vector ``x`` and every column of ``y``, NaN rows dropped."""
    ok = np.isfinite(x) & np.all(np.isfinite(y), axis=1)
    if ok.sum() < 5:
        return np.zeros(y.shape[1])
    x, y = x[ok], y[ok]
    x = x - x.mean()
    y = y - y.mean(axis=0)
    denom = np.sqrt((x @ x) * np.sum(y * y, axis=0))
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(denom > 1e-12, (x @ y) / denom, 0.0)
    return np.abs(r)


def base_shift_s(frame_ts, ref_ts) -> float:
    """The shift that puts a reference on a capture's clock when their spans
    do not overlap at all (epoch GPS against a capture that starts at zero).
    Zero when they overlap, because then the clocks already agree roughly."""
    frame_ts = np.asarray(frame_ts, dtype=float)
    ref_ts = np.asarray(ref_ts, dtype=float)
    if len(frame_ts) == 0 or len(ref_ts) == 0:
        return 0.0
    f0, f1 = float(np.nanmin(frame_ts)), float(np.nanmax(frame_ts))
    r0, r1 = float(np.nanmin(ref_ts)), float(np.nanmax(ref_ts))
    if r1 < f0 or r0 > f1:
        return r0 - f0
    return 0.0


def find_time_offset(
    frames_df: pd.DataFrame,
    ref_ts,
    ref_val,
    *,
    window_s: float = 30.0,
    coarse_step_s: float = 1.0,
    fine_step_s: float = 0.1,
    ids: list[str] | None = None,
    progress_cb=None,
    should_stop=None,
) -> dict:
    """The lag that best lines the reference up with some field in the capture.

    Coarse pass: both sides are averaged into ``coarse_step_s`` bins and the
    reference is slid across every byte-aligned 8- and 16-bit reading of every
    ID over ``-window_s..window_s``; the best absolute Pearson r wins, with
    ties going to the smaller lag. Fine pass: the winning field is re-aligned
    sample by sample on a ``fine_step_s`` grid one coarse step either side.

    A reference whose span does not overlap the capture at all is first moved
    by ``base_shift_s`` (its start onto the capture's start), and that shift
    is included in the returned ``lag_s``.

    Returns ``{"lag_s", "r", "id", "start_bit", "length", "byte_order",
    "base_shift_s", "coarse_lag_s", "window_s"}``. ``lag_s`` is what to pass
    to ``calibrate_against_reference``. A periodic reference is ambiguous
    at lags near a multiple of its period; keep the window under half of it.
    """
    empty = {"lag_s": 0.0, "r": 0.0, "id": None, "start_bit": None, "length": None,
             "byte_order": None, "base_shift_s": 0.0, "coarse_lag_s": 0.0,
             "window_s": float(window_s)}
    if frames_df.empty:
        return empty
    ref_ts = np.asarray(ref_ts, dtype=float)
    ref_val = np.asarray(ref_val, dtype=float)
    finite = np.isfinite(ref_ts) & np.isfinite(ref_val)
    ref_ts, ref_val = ref_ts[finite], ref_val[finite]
    if len(ref_ts) < 5 or np.std(ref_val) < 1e-12:
        return empty

    frame_ts_all = frames_df["Timestamp"].to_numpy(dtype=float)
    base = base_shift_s(frame_ts_all, ref_ts)
    ref_ts = ref_ts - base
    empty["base_shift_s"] = round(base, 3)
    if window_s <= 0:
        return {**empty, "lag_s": round(base, 3)}

    step = max(float(coarse_step_s), 1e-3)
    n_lags = int(round(window_s / step))
    t0 = min(float(frame_ts_all.min()), float(ref_ts.min())) - n_lags * step
    t1 = max(float(frame_ts_all.max()), float(ref_ts.max())) + n_lags * step
    n_bins = int(np.ceil((t1 - t0) / step)) + 1
    ref_bins = _bin_means(ref_ts, ref_val, t0, step, n_bins)

    # lags ordered by magnitude so a tie is resolved toward zero
    lags = sorted(range(-n_lags, n_lags + 1), key=lambda k: (abs(k), k))
    best = {"r": 0.0, "lag": 0, "id": None, "field": None}
    id_list = ids if ids is not None else sorted(frames_df["ID"].unique())
    for index, can_id in enumerate(id_list):
        if should_stop is not None and should_stop():
            break
        if progress_cb is not None:
            progress_cb(index, len(id_list))
        grp = frames_df[frames_df["ID"] == can_id]
        if len(grp) < 10:
            continue
        fts, mat = _byte_matrix(grp)
        fields = _coarse_fields(mat)
        if not np.any(np.std(fields, axis=0) > 1e-9):
            continue
        field_bins = _bin_means(fts, fields, t0, step, n_bins)
        for k in lags:
            # a reference k bins ahead of the capture pairs bin i+k with bin i
            if k >= 0:
                r = _pearson_columns(ref_bins[k:], field_bins[:n_bins - k])
            else:
                r = _pearson_columns(ref_bins[:n_bins + k], field_bins[-k:])
            j = int(np.argmax(r))
            if r[j] > best["r"] + 1e-9:
                best = {"r": float(r[j]), "lag": k, "id": can_id, "field": j}
    if progress_cb is not None:
        progress_cb(len(id_list), len(id_list))
    if best["id"] is None:
        return {**empty, "lag_s": round(base, 3)}

    start_bit, length, order = _FIELD_LABELS[best["field"]]
    coarse_lag = best["lag"] * step
    grp = frames_df[frames_df["ID"] == best["id"]]
    fts, mat = _byte_matrix(grp)
    raw = _extract_raw(mat, start_bit, length, order == "big")
    max_dt = max(0.05, float(np.median(np.diff(np.sort(ref_ts)))) if len(ref_ts) > 1 else 0.05)
    fine = np.arange(coarse_lag - step, coarse_lag + step + fine_step_s / 2, fine_step_s)
    fine = sorted(fine, key=lambda x: (round(abs(x), 6), x))
    best_lag, best_r = coarse_lag, -1.0
    for lag in fine:
        r_raw, r_ref = _align_to_reference(fts, raw, ref_ts - lag, ref_val, max_dt)
        if len(r_raw) < 10 or np.std(r_raw) < 1e-9:
            continue
        r = abs(float(np.corrcoef(r_raw, r_ref)[0, 1]))
        if np.isfinite(r) and r > best_r + 1e-9:
            best_lag, best_r = float(lag), r
    if best_r < 0:
        best_r = best["r"]
    return {"lag_s": round(base + best_lag, 3), "r": round(best_r, 4),
            "id": best["id"], "start_bit": start_bit, "length": length,
            "byte_order": order, "base_shift_s": round(base, 3),
            "coarse_lag_s": round(coarse_lag, 3), "window_s": float(window_s)}


def calibrate_with_lag_search(frames_df: pd.DataFrame, series: ReferenceSeries, *,
                              window_s: float = 30.0, progress_cb=None,
                              should_stop=None, **kw) -> list[dict]:
    """Find the clock offset for one reference series, then calibrate with it.

    ``window_s`` of zero skips the search and only applies the base shift.
    Each candidate carries the series name and unit and the lag used.
    """
    def half(offset):
        if progress_cb is None:
            return None
        return lambda done, total: progress_cb(offset * total + done, 2 * total)

    offset = find_time_offset(frames_df, series.ts, series.values, window_s=window_s,
                              progress_cb=half(0), should_stop=should_stop)
    if should_stop is not None and should_stop():
        return []
    cands = calibrate_against_reference(frames_df, series.ts, series.values,
                                        lag_s=offset["lag_s"], progress_cb=half(1),
                                        should_stop=should_stop, **kw)
    for cand in cands:
        cand["series"] = series.name
        cand["unit"] = series.unit
        cand["lag_r"] = offset["r"]
    return cands


def calibrate_many(frames_df: pd.DataFrame, refs: list[ReferenceSeries], *,
                   window_s: float = 30.0, progress_cb=None, should_stop=None,
                   **kw) -> list[dict]:
    """Calibrate every reference series independently; one flat ranked list.

    Progress counts through the references, each split into its offset
    search and its fit. Stopping returns what has finished so far.
    """
    out: list[dict] = []
    n = max(1, len(refs))
    for i, series in enumerate(refs):
        if should_stop is not None and should_stop():
            break
        inner = None
        if progress_cb is not None:
            inner = lambda done, total, i=i: progress_cb(i * 100 + int(100 * done / max(1, total)), n * 100)
        out.extend(calibrate_with_lag_search(frames_df, series, window_s=window_s,
                                             progress_cb=inner, should_stop=should_stop,
                                             **kw))
    if progress_cb is not None:
        progress_cb(n * 100, n * 100)
    out.sort(key=lambda d: (d.get("series", ""), -d["r2"]))
    return out
