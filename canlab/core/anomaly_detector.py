"""
CAN frame anomaly detection.

Two backends:
  ZScoreBaseline      — pure numpy, always available.
  IsolationForestBaseline — requires scikit-learn, falls back to Z-score.

Usage:
    baseline = ZScoreBaseline()
    baseline.fit(clean_df)
    scored = score_dataframe(live_df, baseline)
    anomalies = scored[scored["anomaly_score"] > 0.6]
"""

import numpy as np
import pandas as pd

BYTE_COLS = [f"B{i}" for i in range(8)]


# ── Z-score baseline ──────────────────────────────────────────────────────────

class ZScoreBaseline:
    """Per-ID, per-byte Z-score scorer.  score() returns 0-1 (higher = anomalous)."""

    def __init__(self, threshold_sigma: float = 4.0):
        self._sigma     = threshold_sigma
        self._baselines: dict[str, dict[str, tuple[float, float]]] = {}

    def fit(self, frames_df: pd.DataFrame) -> None:
        """Fit per-ID, per-byte mean/std from a clean capture."""
        self._baselines = {}
        for can_id, grp in frames_df.groupby("ID"):
            self._baselines[can_id] = {}
            for col in BYTE_COLS:
                if col not in grp.columns:
                    continue
                s = grp[col].dropna().astype(float)
                if len(s) < 2:
                    continue
                self._baselines[can_id][col] = (float(s.mean()), float(s.std()) + 1e-6)

    def score(self, can_id: str, row: dict) -> float:
        if can_id not in self._baselines:
            return 0.0
        baseline = self._baselines[can_id]
        z2s = []
        for col in BYTE_COLS:
            if col not in baseline:
                continue
            val = row.get(col)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            mean, std = baseline[col]
            z2s.append(((float(val) - mean) / std) ** 2)
        if not z2s:
            return 0.0
        rms_z = float(np.sqrt(np.mean(z2s)))
        return min(1.0, rms_z / self._sigma)

    @property
    def is_fitted(self) -> bool:
        return bool(self._baselines)

    def fitted_ids(self) -> list[str]:
        return list(self._baselines.keys())


# ── Isolation Forest baseline ─────────────────────────────────────────────────

class IsolationForestBaseline:
    """Isolation Forest per ID.  Falls back to ZScoreBaseline if sklearn missing."""

    def __init__(self, contamination="auto"):
        # "auto" uses the original Isolation Forest offset instead of asserting
        # that 5% of the (supposedly clean) baseline is anomalous, which would
        # otherwise flag ~5% of known-good frames.
        self._contamination = contamination
        self._models: dict[str, object]      = {}
        self._cols:   dict[str, list[str]]   = {}

    def fit(self, frames_df: pd.DataFrame) -> None:
        from sklearn.ensemble import IsolationForest   # raises ImportError if absent
        self._models = {}
        self._cols   = {}
        for can_id, grp in frames_df.groupby("ID"):
            cols = [c for c in BYTE_COLS if c in grp.columns]
            X    = grp[cols].dropna().astype(float).values
            if len(X) < 20:
                continue
            model = IsolationForest(
                contamination=self._contamination,
                n_estimators=50,
                random_state=42,
            )
            model.fit(X)
            self._models[can_id] = model
            self._cols[can_id]   = cols

    def score(self, can_id: str, row: dict) -> float:
        if can_id not in self._models:
            return 0.0
        cols  = self._cols[can_id]
        model = self._models[can_id]
        # NaN-safe: a short-DLC byte is NaN; `nan or 0` is nan (NaN is truthy),
        # and IsolationForest rejects NaN input.
        vals = []
        for c in cols:
            v = row.get(c)
            vals.append(0.0 if pd.isna(v) else float(v))
        x = np.array([vals])
        # decision_function: negative scores → anomalous
        s = -float(model.decision_function(x)[0])
        return max(0.0, min(1.0, s + 0.5))

    @property
    def is_fitted(self) -> bool:
        return bool(self._models)

    def fitted_ids(self) -> list[str]:
        return list(self._models.keys())


# ── Convenience scorer ────────────────────────────────────────────────────────

def fit_baseline(frames_df: pd.DataFrame,
                 use_isolation_forest: bool = False):
    """Fit the best available baseline to frames_df and return it."""
    if use_isolation_forest:
        try:
            det = IsolationForestBaseline()
            det.fit(frames_df)
            return det
        except (ImportError, Exception):
            pass
    det = ZScoreBaseline()
    det.fit(frames_df)
    return det


def score_dataframe(frames_df: pd.DataFrame,
                    baseline) -> pd.DataFrame:
    """Return frames_df with an added 'anomaly_score' float column (0-1)."""
    scores = []
    for _, row in frames_df.iterrows():
        can_id = str(row.get("ID", ""))
        scores.append(baseline.score(can_id, dict(row)))
    out = frames_df.copy()
    out["anomaly_score"] = scores
    return out
