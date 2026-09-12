"""
Counter / Checksum Auto-Detector.

For each message ID in a DataFrame:
  - Counter bytes: value increments by 1 each frame, wraps at a power-of-2 boundary
    (0x0F nibble counter, 0xFF byte counter, or upper/lower nibble)
  - Checksum bytes: value can be reproduced from the other bytes using a known algorithm
    (XOR8, SUM8 mod 256, XOR of nibbles, Hyundai-style XOR)

Returns a dict: {can_id: {"counters": [...], "checksums": [...]}}
Each entry is a dict with byte index, confidence, and detected algorithm/wrap.
"""

import numpy as np

# A counter is allowed to miss some increments (dropped frames, resets); 2-bit
# counters only advance a quarter of the time, so the old 0.85 bar excluded
# every counter narrower than a nibble.
COUNTER_MATCH = 0.70
# Frames scored per byte for the non-vectorisable algorithms. The whole
# registry over every byte of every message on a busy bus is about ten
# seconds; capping the sample keeps an explicit Run Detection responsive.
CRC_SAMPLE_FRAMES = 400
import pandas as pd
from typing import Optional

BYTE_COLS = [f"B{i}" for i in range(8)]


# ── Counter detection ─────────────────────────────────────────────────────────

def _counter_score(vals: np.ndarray) -> tuple[float, Optional[int]]:
    """How well these values increment by one, and the modulus they roll over at.

    The modulus is read off the data rather than assumed, so a two-bit counter
    is reported as wrapping at 4 and not at 16. It is only reported at all when
    a roll-over was actually seen: a byte counter observed over 200 frames may
    simply not have reached its limit yet, and guessing from the highest value
    would claim it wraps at 200.
    """
    top = int(vals.max())
    if top < 1:
        return 0.0, None
    diffs = np.diff(vals)
    wrapped = (vals[1:] == 0) & (vals[:-1] == top)
    rate = float(((diffs == 1) | wrapped).mean())
    return rate, (top + 1 if wrapped.any() else None)


def _detect_counter_byte(series: pd.Series) -> Optional[dict]:
    """
    Return counter info if this byte series looks like a rolling counter, else None.
    Checks the whole byte and each nibble; the wrap comes from the values seen.
    """
    vals = series.dropna().astype(int).values
    if len(vals) < 8:
        return None

    results = []
    for name, candidate in (("byte_counter", vals),
                            ("nibble_lo_counter", vals & 0x0F),
                            ("nibble_hi_counter", (vals >> 4) & 0x0F)):
        rate, wrap = _counter_score(candidate)
        if rate > COUNTER_MATCH:
            results.append({"type": name, "wrap": wrap,
                            "confidence": round(rate, 3)})

    if not results:
        return None
    return max(results, key=lambda x: x["confidence"])


# ── Checksum detection ────────────────────────────────────────────────────────

def _detect_checksums_vectorized(frames: pd.DataFrame, msg_id_int: int = 0,
                                 min_conf: float = 0.90) -> list[dict]:
    """Vectorized replacement for per-byte _detect_checksum_byte over one ID.

    Builds the (N,8) byte matrix once and computes every algorithm for every
    byte with numpy, instead of iterrows × algorithms × bytes. XOR is its own
    inverse and SUM/NIBBLE_SUM are cumulative, so "checksum over the other 7
    bytes" is (total ⊕/− this byte) — no Python per-row loop needed.
    """
    cols = [c for c in BYTE_COLS if c in frames.columns]
    if len(cols) < 2:
        return []
    mat = frames[BYTE_COLS].to_numpy(dtype=np.float64)   # NaN for missing bytes
    valid = ~np.isnan(mat).any(axis=1)
    mat = mat[valid].astype(np.int64)
    n = len(mat)
    if n < 5:
        return []

    total_xor = np.zeros(n, dtype=np.int64)
    for k in range(8):
        total_xor ^= mat[:, k]
    total_sum = mat.sum(axis=1)
    nib = (mat & 0x0F) + ((mat >> 4) & 0x0F)
    total_nib = nib.sum(axis=1)
    hy_const = (msg_id_int >> 4) & 0xFF

    # Per byte, per algorithm: how often the relation holds. `spread` counts
    # every byte the relation holds for, including ones rejected as candidates
    # below, because that is what says whether the relation localises anything.
    scores: dict[str, dict[int, float]] = {}
    spread: dict[str, int] = {}
    for k in range(8):
        col = mat[:, k]
        # A checksum has to be worth more than a guess. Score each candidate
        # against the best constant predictor -- the byte's most common value.
        # A constant byte has a baseline of 1.0 and can never be beaten, which
        # is the right answer: padding is not a checksum.
        baseline = float(np.bincount(col, minlength=256).max()) / n
        floor = max(min_conf, baseline)
        algos = {
            "XOR8":       (total_xor ^ col),
            "SUM8":       ((total_sum - col) & 0xFF),
            "NIBBLE_SUM": ((total_nib - nib[:, k]) & 0xFF),
        }
        if msg_id_int > 0:
            algos["HYUNDAI_XOR"] = ((total_xor ^ col) ^ hy_const)
        for name, expected in algos.items():
            conf = float(np.mean(expected == col))
            if conf > min_conf:
                spread[name] = spread.get(name, 0) + 1
            if conf > floor:
                scores.setdefault(name, {})[k] = conf

    # Some relations hold for the whole message rather than one byte. "Is byte
    # k the XOR of the other seven?" is the same question as "does the message
    # XOR to zero?", which does not depend on k, so every byte matches. Two
    # very different payloads do that: one with a real XOR checksum, and one
    # that just repeats each value an even number of times.
    #
    # They are told apart by the payload itself. A repeated payload has
    # duplicate columns -- byte 0 equals byte 2 in every frame. A checksummed
    # one does not. Only the columns that vary are compared, because constant
    # padding is duplicated in almost every message and says nothing.
    varying = [k for k in range(8)
               if len(np.unique(mat[:, k])) > 1]
    seen: dict[bytes, int] = {}
    repeats_itself = False
    for k in varying:
        key = mat[:, k].tobytes()
        if key in seen:
            repeats_itself = True
            break
        seen[key] = k

    localising = {}
    for name, hits in scores.items():
        if spread.get(name, 0) <= len(cols) // 2:
            localising[name] = hits            # genuinely localised
        elif not repeats_itself and hits:
            # Message-wide, but the payload does not repeat itself, so the
            # relation is real. Convention puts the checksum last.
            localising[name] = {max(hits): hits[max(hits)]}

    # What is left may still name the same byte twice (a trailing SUM8 also
    # makes XOR8 hold on two bytes). Prefer the most specific relation: the one
    # that matched the fewest bytes, then the most confident. Where a relation
    # did match more than one byte, take the last -- a trailing checksum is the
    # near-universal convention.
    # The four relations above are the ones that vectorise. Real vehicles
    # mostly use CRC-8, which does not, and testing only the cheap four meant a
    # sweep over a real 180-message bus reported no checksums at all while the
    # per-byte guesser found them immediately. Run the full registry over the
    # bytes that are still unexplained, capped: only bytes that vary and beat
    # their constant baseline are candidates, and only the first frames are
    # scored.
    explained = {k for hits in localising.values() for k in hits}
    candidates = [k for k in varying if k not in explained]
    if candidates:
        from canlab.core.checksums import ALGORITHMS

        # Skip the three the vectorised pass already covers. Testing them
        # twice makes one relation compete with itself, and the duplicate can
        # win on a different byte than the original.
        registry = {name: algo for name, algo in ALGORITHMS.items()
                    if name not in ("xor8", "sum8", "nibble_sum")}
        sample = mat[:CRC_SAMPLE_FRAMES]
        rows = [bytes(r.tolist()) for r in sample]
        extra_scores: dict[str, dict[int, float]] = {}
        extra_spread: dict[str, int] = {}
        # Score against every varying byte, not only the candidates, so the
        # same localising test can be applied: an algorithm that "explains"
        # most of the payload has explained nothing.
        for k in varying:
            observed = sample[:, k]
            base = float(np.bincount(observed, minlength=256).max()) / len(sample)
            floor = max(min_conf, base)
            for name, algo in registry.items():
                try:
                    # The registry takes the whole payload and drops
                    # cs_index itself; pre-trimming would exclude two bytes.
                    predicted = np.fromiter(
                        (algo.compute(row, msg_id_int, k) & 0xFF
                         for row in rows), dtype=np.int64, count=len(rows))
                except Exception:
                    continue                    # an algorithm that cannot apply
                conf = float(np.mean(predicted == observed))
                if conf > min_conf:
                    extra_spread[name] = extra_spread.get(name, 0) + 1
                if conf > floor and k in candidates:
                    extra_scores.setdefault(name, {})[k] = conf

        for name, hits in extra_scores.items():
            wide = extra_spread.get(name, 0) > max(1, len(varying) // 2)
            if wide and repeats_itself:
                continue                        # the 0A6 case, in another guise
            keep = {max(hits): hits[max(hits)]} if wide else hits
            localising[name] = keep
            spread[name] = extra_spread.get(name, 1)

    best_per_byte: dict[int, tuple] = {}
    for name, hits in localising.items():
        k = max(hits)
        rank = (spread[name], -hits[k])
        if k not in best_per_byte or rank < best_per_byte[k][0]:
            best_per_byte[k] = (rank, {"byte": k, "col": f"B{k}",
                                       "algorithm": name,
                                       "confidence": round(hits[k], 3)})
    if not best_per_byte:
        return []
    # One checksum per message. Messages with two are vanishingly rare, and
    # once one byte is a checksum the others often satisfy some relation as a
    # consequence: with a trailing SUM8 over a mostly-constant payload, byte 0
    # is then exactly the exclusive-or of the rest. Reporting both sends the
    # reader after a byte that is really just an echo of the real one.
    rank, best = min(best_per_byte.values(),
                     key=lambda v: (v[0], -v[1]["byte"]))
    return [best]


# ── Main API ──────────────────────────────────────────────────────────────────

def detect_counters_and_checksums(df: pd.DataFrame) -> dict:
    """
    Analyse every message ID in df.

    Returns:
        {
          "0A6": {
            "counters":  [{"byte": 0, "type": "nibble_hi_counter", "wrap": 16, "confidence": 0.99}],
            "checksums": [{"byte": 7, "algorithm": "XOR8", "confidence": 0.97}],
          },
          ...
        }
    """
    results = {}
    for can_id in df["ID"].unique():
        frames = df[df["ID"] == can_id]
        if len(frames) < 5:
            continue
        # Increment detection compares consecutive rows, so the frames have to
        # be in time order — concatenated captures are not.
        if not frames["Timestamp"].is_monotonic_increasing:
            frames = frames.sort_values("Timestamp", kind="stable")
        frames = frames.copy()

        counters  = []
        checksums = []

        try:
            mid_int = int(can_id, 16)
        except (ValueError, TypeError):
            mid_int = 0

        for i, col in enumerate(BYTE_COLS):
            if col not in frames.columns:
                continue
            series = frames[col].dropna()
            if series.empty:
                continue
            ctr = _detect_counter_byte(series)
            if ctr:
                counters.append({"byte": i, "col": col, **ctr})

        # Checksums for all 8 bytes in one vectorized pass (was iterrows per byte).
        checksums = _detect_checksums_vectorized(frames, mid_int)

        if counters or checksums:
            results[can_id] = {"counters": counters, "checksums": checksums}

    return results
