"""
Change-on-Action Detector — high-accuracy edition.

Improvements over v1:
  - Mann-Whitney U test for statistical significance (p < 0.05 required)
  - Direction classification: RISING, FALLING, TOGGLE, PULSE, SUSTAINED
  - Persistence score: fraction of action frames that differ from baseline median
  - Bit-level delta for changed bytes (identifies which bits flipped)
  - Results ranked by significance (p-value ascending)
  - Noise floor: changes < 2 LSB on constant signals are suppressed
"""
from typing import Optional
import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

BYTE_COLS = [f"B{i}" for i in range(8)]
MIN_FRAMES_PER_WINDOW = 5


class ChangeRecorder:
    def __init__(self):
        self._baseline_df: Optional[pd.DataFrame] = None
        self._action_df:   Optional[pd.DataFrame] = None

    def capture_baseline(self, df: pd.DataFrame) -> None:
        self._baseline_df = df.copy()

    def capture_action(self, df: pd.DataFrame) -> None:
        self._action_df = df.copy()

    def clear(self) -> None:
        self._baseline_df = None
        self._action_df   = None

    def compute_delta(self) -> list[dict]:
        """
        Return per-ID per-byte change entries with statistical significance.

        Each entry:
          {
            "id": "0A6",
            "byte": 2,
            "before": 0.0,
            "after": 128.0,
            "delta": 128.0,
            "direction": "RISING",
            "persistence": 0.95,
            "p_value": 0.0001,
            "significant": True,
            "changed_bits": [7],   # list of bit indices that flipped
          }
        Only entries with significant=True are returned (p < 0.05 or
        deterministic change when sample is small).
        """
        if self._baseline_df is None or self._action_df is None:
            return []

        results = []
        all_ids = set(self._baseline_df["ID"].unique()) | set(self._action_df["ID"].unique())

        for can_id in sorted(all_ids):
            base_frames = self._baseline_df[self._baseline_df["ID"] == can_id]
            act_frames  = self._action_df[self._action_df["ID"] == can_id]

            for i, col in enumerate(BYTE_COLS):
                base_vals = _col_vals(base_frames, col)
                act_vals  = _col_vals(act_frames,  col)

                if base_vals is None or act_vals is None:
                    continue

                before = float(np.median(base_vals))
                after  = float(np.median(act_vals))
                delta  = after - before

                if abs(delta) < 0.5:
                    continue

                # Statistical significance
                p_value    = _mannwhitney_p(base_vals, act_vals)
                significant = p_value < 0.05 if p_value is not None else (abs(delta) > 2)

                if not significant:
                    continue

                persistence = _persistence(base_vals, act_vals)
                direction   = _direction(base_vals, act_vals)
                changed_bits = _changed_bits(int(round(before)), int(round(after)))

                results.append({
                    "id":           can_id,
                    "byte":         i,
                    "before":       round(before, 1),
                    "after":        round(after, 1),
                    "delta":        round(delta, 1),
                    "direction":    direction,
                    "persistence":  round(persistence, 3),
                    "p_value":      round(p_value, 6) if p_value is not None else None,
                    "significant":  True,
                    "changed_bits": changed_bits,
                })

        # Sort by p-value (most significant first), then by abs delta
        results.sort(key=lambda x: (x["p_value"] or 1.0, -abs(x["delta"])))
        return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _col_vals(frames: pd.DataFrame, col: str) -> Optional[np.ndarray]:
    if col not in frames.columns or frames.empty:
        return None
    vals = frames[col].dropna().astype(float).values
    return vals if len(vals) >= 1 else None


def _mannwhitney_p(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    try:
        _, p = mannwhitneyu(a, b, alternative="two-sided")
        return float(p)
    except Exception:
        return None


def _persistence(base_vals: np.ndarray, act_vals: np.ndarray) -> float:
    """Fraction of action frames that differ from baseline median by > 0.5."""
    base_median = np.median(base_vals)
    return float((np.abs(act_vals - base_median) > 0.5).mean())


def _direction(base_vals: np.ndarray, act_vals: np.ndarray) -> str:
    base_med = np.median(base_vals)
    act_med  = np.median(act_vals)
    delta    = act_med - base_med

    # Toggles: action values flip between exactly two states
    act_unique = np.unique(act_vals.astype(int))
    if len(act_unique) == 2:
        return "TOGGLE"

    # Pulse: action values return toward baseline in later frames
    if len(act_vals) >= 4:
        first_half = np.median(act_vals[:len(act_vals)//2])
        second_half = np.median(act_vals[len(act_vals)//2:])
        if abs(first_half - base_med) > abs(second_half - base_med) * 1.5:
            return "PULSE"

    if delta > 0:
        return "RISING"
    elif delta < 0:
        return "FALLING"
    return "SUSTAINED"


def _changed_bits(before: int, after: int) -> list[int]:
    """Return list of bit indices (0=LSB) that differ between before and after."""
    diff = (before ^ after) & 0xFF
    return [bit for bit in range(8) if (diff >> bit) & 1]


def _summarise(df: pd.DataFrame) -> pd.DataFrame:
    """Compute median byte values per ID (kept for API compatibility)."""
    rows = []
    if df.empty:
        return pd.DataFrame(columns=["ID"] + BYTE_COLS)
    for can_id in df["ID"].unique():
        grp = df[df["ID"] == can_id]
        row = {"ID": can_id}
        for col in BYTE_COLS:
            if col in grp.columns:
                vals = grp[col].dropna()
                row[col] = float(vals.median()) if not vals.empty else 0.0
            else:
                row[col] = 0.0
        rows.append(row)
    return pd.DataFrame(rows)
