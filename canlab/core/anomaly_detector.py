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
import logging

log = logging.getLogger(__name__)

BYTE_COLS = [f"B{i}" for i in range(8)]


# ── Z-score baseline ──────────────────────────────────────────────────────────

class ZScoreBaseline:
    """Per-ID, per-byte Z-score scorer.  score() returns 0-1 (higher = anomalous).

    The fit also keeps each ID's timing (median period and its spread), which
    the live watch uses to notice an ID that has gone quiet or is bursting.
    Scoring is vectorised over a byte matrix; ``score()`` on one row is the
    same arithmetic on a one-row matrix.
    """

    def __init__(self, threshold_sigma: float = 4.0):
        self._sigma     = threshold_sigma
        self._baselines: dict[str, dict[str, tuple[float, float]]] = {}
        self._mean:  dict[str, np.ndarray] = {}
        self._std:   dict[str, np.ndarray] = {}
        self._valid: dict[str, np.ndarray] = {}
        self._period: dict[str, tuple[float, float, int]] = {}

    def fit(self, frames_df: pd.DataFrame) -> None:
        """Fit per-ID, per-byte mean/std from a clean capture."""
        self._baselines = {}
        self._mean, self._std, self._valid, self._period = {}, {}, {}, {}
        if frames_df.empty:
            return
        cols = [c for c in BYTE_COLS if c in frames_df.columns]
        for can_id, grp in frames_df.groupby("ID", sort=False):
            can_id = str(can_id)
            mat = np.full((len(grp), 8), np.nan)
            for i, c in enumerate(cols):
                mat[:, BYTE_COLS.index(c)] = pd.to_numeric(grp[c], errors="coerce").to_numpy(dtype=float)
            count = np.isfinite(mat).sum(axis=0)
            valid = count >= 2
            mean = np.zeros(8)
            std = np.ones(8)
            with np.errstate(invalid="ignore"):
                if valid.any():
                    mean[valid] = np.nanmean(mat[:, valid], axis=0)
                    # ddof=1 to match the pandas std the first version used
                    std[valid] = np.nanstd(mat[:, valid], axis=0, ddof=1) + 1e-6
            self._mean[can_id], self._std[can_id], self._valid[can_id] = mean, std, valid
            self._baselines[can_id] = {
                BYTE_COLS[i]: (float(mean[i]), float(std[i])) for i in range(8) if valid[i]}
            ts = np.sort(pd.to_numeric(grp["Timestamp"], errors="coerce").dropna().to_numpy(dtype=float))
            if len(ts) >= 3:
                dt = np.diff(ts)
                med = float(np.median(dt))
                mad = float(np.median(np.abs(dt - med)))
                self._period[can_id] = (med, mad, int(len(ts)))

    def score_matrix(self, can_id: str, mat: np.ndarray) -> np.ndarray:
        """Scores for an (N, 8) byte matrix (NaN for bytes a frame lacks)."""
        mat = np.asarray(mat, dtype=float)
        if mat.ndim == 1:
            mat = mat[None, :]
        n = mat.shape[0]
        if can_id not in self._mean or n == 0:
            return np.zeros(n)
        if mat.shape[1] < 8:
            mat = np.hstack([mat, np.full((n, 8 - mat.shape[1]), np.nan)])
        mat = mat[:, :8]
        use = np.isfinite(mat) & self._valid[can_id][None, :]
        z2 = np.where(use, ((mat - self._mean[can_id]) / self._std[can_id]) ** 2, 0.0)
        count = use.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            rms = np.sqrt(np.where(count > 0, z2.sum(axis=1) / np.maximum(count, 1), 0.0))
        return np.clip(rms / self._sigma, 0.0, 1.0)

    def score(self, can_id: str, row: dict) -> float:
        if can_id not in self._mean:
            return 0.0
        vals = []
        for col in BYTE_COLS:
            val = row.get(col)
            try:
                vals.append(np.nan if val is None else float(val))
            except (TypeError, ValueError):
                vals.append(np.nan)
        return float(self.score_matrix(can_id, np.array([vals]))[0])

    def period_stats(self, can_id: str) -> dict | None:
        """``{"median_dt", "mad_dt", "n"}`` for an ID seen at least three times."""
        p = self._period.get(can_id)
        if p is None:
            return None
        return {"median_dt": p[0], "mad_dt": p[1], "n": p[2]}

    def byte_deviations(self, can_id: str, values) -> list[tuple[int, float, float]]:
        """(byte index, value, z) for the bytes of one frame, largest |z| first."""
        if can_id not in self._mean:
            return []
        vals = np.asarray(values, dtype=float)
        out = []
        for i in range(min(8, len(vals))):
            if not self._valid[can_id][i] or not np.isfinite(vals[i]):
                continue
            z = (vals[i] - self._mean[can_id][i]) / self._std[can_id][i]
            out.append((i, float(vals[i]), float(z)))
        out.sort(key=lambda t: -abs(t[2]))
        return out

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
            log.debug("suppressed exception", exc_info=True)
    det = ZScoreBaseline()
    det.fit(frames_df)
    return det


def byte_matrix(frames_df: pd.DataFrame) -> np.ndarray:
    """An (N, 8) float matrix of the byte columns, NaN where a frame has none."""
    mat = np.full((len(frames_df), 8), np.nan)
    for i, c in enumerate(BYTE_COLS):
        if c in frames_df.columns:
            mat[:, i] = pd.to_numeric(frames_df[c], errors="coerce").to_numpy(dtype=float)
    return mat


def score_dataframe(frames_df: pd.DataFrame,
                    baseline) -> pd.DataFrame:
    """Return frames_df with an added 'anomaly_score' float column (0-1).

    A baseline with ``score_matrix`` is scored one ID at a time in one
    vectorised call each; the row loop remains for the Isolation Forest.
    """
    out = frames_df.copy()
    if frames_df.empty:
        out["anomaly_score"] = np.zeros(0)
        return out
    if hasattr(baseline, "score_matrix"):
        scores = np.zeros(len(frames_df))
        mat = byte_matrix(frames_df)
        ids = frames_df["ID"].astype(str).to_numpy()
        for can_id in pd.unique(ids):
            where = np.flatnonzero(ids == can_id)
            scores[where] = baseline.score_matrix(str(can_id), mat[where])
        out["anomaly_score"] = scores
        return out
    scores = []
    for _, row in frames_df.iterrows():
        can_id = str(row.get("ID", ""))
        scores.append(baseline.score(can_id, dict(row)))
    out["anomaly_score"] = scores
    return out
