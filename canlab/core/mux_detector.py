"""Multiplexed (mode-dependent) signal detection.

Many ECUs pack different payloads into the same CAN ID depending on a
"multiplexor" selector byte: when the selector is 0 the other bytes mean one
thing, when it's 1 they mean another. Standard reverse-engineering that treats
every byte as a fixed signal gets this wrong. This module finds the selector
byte and reports which bytes are active in each mode, so the DBC builder can
emit proper `SG_ ... M` / `SG_ ... m<n>` multiplexing.

Heuristic: a good selector is a low-cardinality byte such that, once you
condition on its value, the *other* bytes vary far less within each group than
across the whole capture (their behaviour is mode-dependent).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BYTE_COLS = [f"B{i}" for i in range(8)]


def _byte_matrix(frames_for_id: pd.DataFrame) -> np.ndarray:
    cols = [c for c in BYTE_COLS if c in frames_for_id.columns]
    n = len(frames_for_id)
    mat = np.full((n, 8), np.nan)
    for i, c in enumerate(BYTE_COLS):
        if c in frames_for_id.columns:
            mat[:, i] = pd.to_numeric(frames_for_id[c], errors="coerce")
    return mat


def detect_multiplexer(frames_for_id: pd.DataFrame,
                       min_modes: int = 2, max_modes: int = 16,
                       min_score: float = 0.35) -> dict | None:
    """Return the most likely multiplexor for one ID, or None.

    {
      "mux_byte":   int,                       # selector byte index
      "score":      float,                     # 0-1 confidence
      "modes":      {value: [active_byte_idx]} # payload bytes per selector value
      "mode_counts":{value: n_frames}
    }
    """
    if len(frames_for_id) < 30:
        return None
    mat = _byte_matrix(frames_for_id)
    # keep only fully-populated rows for a clean analysis
    valid = ~np.isnan(mat).any(axis=1)
    mat = mat[valid]
    if len(mat) < 30:
        return None

    n_bytes = mat.shape[1]
    best = None

    for sel in range(n_bytes):
        col = mat[:, sel]
        values, counts = np.unique(col, return_counts=True)
        if not (min_modes <= len(values) <= max_modes):
            continue
        # Require each mode to have a reasonable sample and be reasonably balanced.
        if counts.min() < max(5, 0.02 * len(mat)):
            continue

        other = [b for b in range(n_bytes) if b != sel]
        overall_var = mat[:, other].var(axis=0) + 1e-9

        # Mean within-group variance per other byte.
        within = np.zeros(len(other))
        for v in values:
            grp = mat[col == v][:, other]
            within += grp.var(axis=0) * (len(grp) / len(mat))
        # Fraction of variance explained by conditioning on the selector.
        reduction = np.clip(1.0 - within / overall_var, 0.0, 1.0)
        # Only count bytes that actually carry variation somewhere.
        carries = overall_var > 1.0
        if carries.sum() == 0:
            continue
        score = float(reduction[carries].mean())

        if best is None or score > best["score"]:
            modes = {}
            mode_counts = {}
            for v in values:
                grp = mat[col == v]
                active = [other[k] for k in range(len(other))
                          if grp[:, other[k]].std() > 1.0]
                modes[int(v)] = active
                mode_counts[int(v)] = int(len(grp))
            best = {"mux_byte": sel, "score": round(score, 3),
                    "modes": modes, "mode_counts": mode_counts}

    if best and best["score"] >= min_score:
        return best
    return None


def detect_all_multiplexers(frames_df: pd.DataFrame, **kw) -> dict:
    """Run detect_multiplexer per ID; return {id: result} for muxed IDs only."""
    out = {}
    if frames_df.empty:
        return out
    for can_id, grp in frames_df.groupby("ID"):
        res = detect_multiplexer(grp, **kw)
        if res:
            out[str(can_id)] = res
    return out
