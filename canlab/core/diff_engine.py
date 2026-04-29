"""Compare two log DataFrames and highlight per-ID / per-byte differences."""
import pandas as pd
import numpy as np

BYTE_COLS = [f"B{i}" for i in range(8)]


def diff_logs(baseline_df: pd.DataFrame, compare_df: pd.DataFrame) -> list:
    """
    Return a list of dicts, one per CAN ID, describing differences:
      - status: 'added' | 'removed' | 'changed' | 'same'
      - id
      - baseline_count, compare_count
      - changed_bytes: list of byte indices where mean differs by >5%
      - freq_delta_hz
    """
    if baseline_df.empty and compare_df.empty:
        return []

    base_ids = set(baseline_df["ID"].unique()) if not baseline_df.empty else set()
    comp_ids = set(compare_df["ID"].unique())   if not compare_df.empty  else set()
    all_ids  = base_ids | comp_ids

    results = []
    for can_id in sorted(all_ids):
        base_grp = baseline_df[baseline_df["ID"] == can_id] if not baseline_df.empty else pd.DataFrame()
        comp_grp = compare_df[compare_df["ID"] == can_id]   if not compare_df.empty  else pd.DataFrame()

        if base_grp.empty:
            results.append({"id": can_id, "status": "added",
                            "baseline_count": 0, "compare_count": len(comp_grp),
                            "changed_bytes": [], "freq_delta_hz": 0.0})
            continue
        if comp_grp.empty:
            results.append({"id": can_id, "status": "removed",
                            "baseline_count": len(base_grp), "compare_count": 0,
                            "changed_bytes": [], "freq_delta_hz": 0.0})
            continue

        changed_bytes = []
        for col in BYTE_COLS:
            if col not in base_grp.columns or col not in comp_grp.columns:
                continue
            bm = base_grp[col].dropna().mean()
            cm = comp_grp[col].dropna().mean()
            if bm == 0 and cm == 0:
                continue
            ref = max(abs(bm), abs(cm), 1)
            if abs(bm - cm) / ref > 0.05:
                changed_bytes.append(int(col[1]))

        def freq(grp):
            ts = grp["Timestamp"].sort_values()
            span = ts.iloc[-1] - ts.iloc[0]
            return len(grp) / span if span > 0 else 0.0

        freq_delta = freq(comp_grp) - freq(base_grp)
        status = "changed" if changed_bytes or abs(freq_delta) > 0.5 else "same"

        results.append({
            "id":              can_id,
            "status":          status,
            "baseline_count":  len(base_grp),
            "compare_count":   len(comp_grp),
            "changed_bytes":   changed_bytes,
            "freq_delta_hz":   round(freq_delta, 2),
        })

    return results
