import re
import pandas as pd
from typing import Optional


def parse_annotations(text: str) -> list[dict]:
    """Parse README-style annotations: 'TS 614 Switched to Drive'"""
    events = []
    pattern = re.compile(r"TS\s+(\d+(?:\.\d+)?)\s+(.+)", re.IGNORECASE)
    for line in text.splitlines():
        m = pattern.search(line.strip())
        if m:
            events.append({
                "timestamp": float(m.group(1)),
                "event":     m.group(2).strip(),
            })
    return events


def correlate_events(
    df: pd.DataFrame,
    events: list[dict],
    window: float = 2.0,
) -> dict[str, list[str]]:
    """
    For each event, find IDs that changed value within ±window seconds.
    Returns: {event_label: [list of CAN IDs]}
    """
    if df.empty or not events:
        return {}

    byte_cols = [c for c in ["B0","B1","B2","B3","B4","B5","B6","B7"] if c in df.columns]
    result = {}

    for evt in events:
        ts = evt["timestamp"]
        label = f"TS{int(ts)}: {evt['event']}"

        window_df = df[
            (df["Timestamp"] >= ts - window) &
            (df["Timestamp"] <= ts + window)
        ]
        before_df = df[
            (df["Timestamp"] >= ts - window) &
            (df["Timestamp"] < ts)
        ]
        after_df = df[
            (df["Timestamp"] > ts) &
            (df["Timestamp"] <= ts + window)
        ]

        changed_ids = []
        for can_id in window_df["ID"].unique():
            before = before_df[before_df["ID"] == can_id]
            after  = after_df[after_df["ID"] == can_id]
            if before.empty or after.empty:
                continue
            for col in byte_cols:
                if col not in before.columns:
                    continue
                b_vals = before[col].dropna()
                a_vals = after[col].dropna()
                if b_vals.empty or a_vals.empty:
                    continue
                if abs(b_vals.mean() - a_vals.mean()) > 5:
                    changed_ids.append(can_id)
                    break
        result[label] = list(dict.fromkeys(changed_ids))

    return result


DEFAULT_KONA_ANNOTATIONS = """
TS 0 Recording started
TS 45 Engine start
TS 120 Vehicle moving forward
TS 200 Brake applied
TS 250 Brake released
TS 300 Accelerator pressed
TS 380 Switched to Drive
TS 450 Hard brake event
TS 520 Turn signal left
TS 590 Turn signal right
TS 614 Switched to Park
TS 680 Engine stop
"""
