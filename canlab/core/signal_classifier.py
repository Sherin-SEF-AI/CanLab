"""
Unified byte-role classifier for CAN frames.

Orchestrates existing detectors (counter_checksum_detector, checksum_guesser,
periodicity) and adds BOOLEAN / PHYSICAL / PADDING labelling via entropy + value
analysis.  All operations are pure-Python/numpy — no API calls.
"""

import numpy as np
import pandas as pd

BYTE_COLS = [f"B{i}" for i in range(8)]

ROLE_COLORS = {
    "COUNTER":  "#00aaff",
    "CHECKSUM": "#ff3333",
    "BOOLEAN":  "#cc88ff",
    "PHYSICAL": "#00ff88",
    "PADDING":  "#555555",
    "UNKNOWN":  "#ffb300",
}


# ── Per-byte role classification ──────────────────────────────────────────────

def classify_frame(frames_df: pd.DataFrame,
                   msg_id_hex: str = "000") -> dict[str, dict]:
    """
    Classify each byte's role for one CAN ID's frame set.

    Returns:
        {"B0": {"role": "COUNTER", "confidence": 0.99, "entropy": 0.4,
                "unique": 16, "range": 15, "detail": "nibble_hi_counter"}, ...}
    """
    from core.counter_checksum_detector import detect_counters_and_checksums
    from core.checksum_guesser import guess_all_bytes

    results: dict[str, dict] = {}

    if frames_df.empty:
        return results

    # ── Step 1: run existing counter + checksum detectors ─────────────────────
    cc_info  = detect_counters_and_checksums(frames_df)
    all_cs   = guess_all_bytes(frames_df, msg_id_hex)

    # Flatten into byte-indexed lookups
    can_ids = list(cc_info.keys())
    counters_by_byte:  dict[int, dict] = {}
    checksums_by_byte: dict[int, dict] = {}

    if can_ids:
        info = cc_info[can_ids[0]]
        for c in info.get("counters", []):
            counters_by_byte[c["byte"]] = c
        for c in info.get("checksums", []):
            if c["byte"] not in checksums_by_byte:
                checksums_by_byte[c["byte"]] = c

    # High-accuracy checksum guesser may catch additional bytes
    for byte_idx, matches in all_cs.items():
        if byte_idx not in checksums_by_byte and matches:
            top = matches[0]
            if top["confidence"] > 0.85:
                checksums_by_byte[byte_idx] = {
                    "algorithm":  top["algorithm"],
                    "confidence": top["confidence"],
                }

    # ── Step 2: classify each byte ────────────────────────────────────────────
    for i, col in enumerate(BYTE_COLS):
        if col not in frames_df.columns:
            continue

        s = frames_df[col].dropna()
        if s.empty:
            results[col] = _make(col, "PADDING", 0.99, 0.0, 0, 0, "empty column")
            continue

        vals    = s.astype(int).values
        entropy = _entropy(vals)
        unique  = int(len(np.unique(vals)))
        val_range = int(vals.max() - vals.min())

        # Priority 1 — COUNTER (existing detector, high reliability)
        if i in counters_by_byte:
            c = counters_by_byte[i]
            results[col] = _make(col, "COUNTER", c["confidence"],
                                 entropy, unique, val_range, c["type"])
            continue

        # Priority 2 — CHECKSUM (existing detector + high-accuracy guesser)
        if i in checksums_by_byte:
            c = checksums_by_byte[i]
            results[col] = _make(col, "CHECKSUM", c["confidence"],
                                 entropy, unique, val_range, c["algorithm"])
            continue

        # Priority 3 — PADDING (constant or effectively constant)
        if val_range == 0 or unique == 1:
            results[col] = _make(col, "PADDING", 0.99,
                                 entropy, unique, val_range,
                                 f"constant={int(vals[0])}")
            continue

        if entropy < 0.08:
            results[col] = _make(col, "PADDING", 0.95,
                                 entropy, unique, val_range, "near-constant")
            continue

        # Priority 4 — BOOLEAN (bimodal or very few states)
        uniq_set = set(np.unique(vals).tolist())
        if uniq_set <= {0, 1}:
            results[col] = _make(col, "BOOLEAN", 0.97,
                                 entropy, unique, val_range, "binary 0/1")
            continue

        if unique <= 4 and val_range <= 15:
            label = "multi-state ({})".format(
                ", ".join(str(v) for v in sorted(uniq_set)[:4])
            )
            results[col] = _make(col, "BOOLEAN", 0.85,
                                 entropy, unique, val_range, label)
            continue

        # Priority 5 — PHYSICAL (continuous, high entropy, smooth deltas)
        if entropy > 1.5 and val_range > 10:
            deltas    = np.diff(s.astype(float).values)
            jitter    = float(np.std(deltas))
            smooth    = jitter < val_range * 0.6
            conf      = 0.78 if smooth else 0.60
            results[col] = _make(col, "PHYSICAL", conf,
                                 entropy, unique, val_range,
                                 f"range 0–{val_range}")
            continue

        results[col] = _make(col, "UNKNOWN", 0.40,
                             entropy, unique, val_range, "")

    return results


def _make(col, role, conf, entropy, unique, val_range, detail) -> dict:
    return {
        "col":        col,
        "role":       role,
        "confidence": round(float(conf), 3),
        "entropy":    round(float(entropy), 2),
        "unique":     unique,
        "range":      val_range,
        "detail":     detail,
        "color":      ROLE_COLORS.get(role, "#888888"),
    }


def _entropy(vals: np.ndarray) -> float:
    counts = np.bincount(vals, minlength=256).astype(float)
    probs  = counts[counts > 0] / counts.sum()
    return float(-np.sum(probs * np.log2(probs + 1e-12)))


# ── Message periodicity ───────────────────────────────────────────────────────

def classify_message_type(frames_df: pd.DataFrame) -> dict:
    """
    Classify a message as CYCLIC, EVENT, or CYCLIC+EVENT.

    Returns:
        {"type": "CYCLIC", "period_ms": 10.0,
         "class": "HIGH-FREQ (≤15ms)", "jitter_pct": 2.3}
    """
    from core.periodicity import classify_period

    if len(frames_df) < 5:
        return {"type": "EVENT", "period_ms": None, "class": "?", "jitter_pct": 0.0}

    ts   = frames_df["Timestamp"].sort_values().values
    ifis = np.diff(ts) * 1000.0        # ms
    ifis = ifis[ifis > 0]
    if len(ifis) == 0:
        return {"type": "EVENT", "period_ms": None, "class": "?", "jitter_pct": 0.0}

    median_ifi = float(np.median(ifis))
    std_ifi    = float(np.std(ifis))
    cv         = std_ifi / (median_ifi + 1e-6)   # coefficient of variation

    if cv < 0.15:
        msg_type = "CYCLIC"
    elif cv < 0.55:
        msg_type = "CYCLIC+EVENT"
    else:
        msg_type = "EVENT"

    return {
        "type":       msg_type,
        "period_ms":  round(median_ifi, 1),
        "class":      classify_period(median_ifi),
        "jitter_pct": round(cv * 100, 1),
    }


# ── Change detection at timestamp ─────────────────────────────────────────────

def find_changes_at_timestamp(frames_df: pd.DataFrame,
                               timestamp:        float,
                               window_before_s:  float = 0.5,
                               window_after_s:   float = 0.15) -> list[dict]:
    """
    For every CAN ID, compare the median byte values before vs after `timestamp`.
    Returns list sorted by magnitude descending:
      [{"id": "4F1", "byte": "B3", "before": 12.0, "after": 128.0, "magnitude": 116.0}]
    """
    results = []
    t0      = timestamp - window_before_s
    t1      = timestamp + window_after_s

    for can_id in frames_df["ID"].unique():
        df     = frames_df[frames_df["ID"] == can_id].sort_values("Timestamp")
        before = df[(df["Timestamp"] >= t0) & (df["Timestamp"] < timestamp)].tail(15)
        after  = df[(df["Timestamp"] >= timestamp) & (df["Timestamp"] <= t1)].head(15)
        if before.empty or after.empty:
            continue

        for col in BYTE_COLS:
            if col not in df.columns:
                continue
            bv = before[col].dropna().astype(float)
            av = after[col].dropna().astype(float)
            if bv.empty or av.empty:
                continue
            b_med = float(bv.median())
            a_med = float(av.median())
            mag   = abs(a_med - b_med)
            if mag >= 0.5:
                results.append({
                    "id":        can_id,
                    "byte":      col,
                    "before":    round(b_med, 1),
                    "after":     round(a_med, 1),
                    "magnitude": round(mag, 1),
                })

    return sorted(results, key=lambda r: r["magnitude"], reverse=True)
