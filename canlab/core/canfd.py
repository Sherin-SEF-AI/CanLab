"""CAN FD helpers — variable-length frame support up to 64 bytes."""

# DLC → actual byte count mapping (CAN FD)
DLC_TO_LEN = {
    0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7,
    8: 8, 9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64,
}

LEN_TO_DLC = {v: k for k, v in DLC_TO_LEN.items()}

# Standard (non-FD) byte columns
CLASSIC_COLS = [f"B{i}" for i in range(8)]

# Maximum FD byte columns
FD_MAX_COLS = [f"B{i}" for i in range(64)]


def byte_cols_for_dlc(dlc: int) -> list[str]:
    """Return the byte column names needed for a given DLC value."""
    n = DLC_TO_LEN.get(dlc, min(dlc, 64))
    return [f"B{i}" for i in range(n)]


def byte_cols_for_len(n: int) -> list[str]:
    """Return byte column names for n data bytes."""
    return [f"B{i}" for i in range(min(n, 64))]


def is_fd_frame(dlc: int) -> bool:
    """Return True if this DLC value implies CAN FD (>8 bytes)."""
    return DLC_TO_LEN.get(dlc, dlc) > 8


def pad_classic_row(row_dict: dict) -> dict:
    """Ensure a row dict has all B0..B7 even if some are absent."""
    import numpy as np
    for col in CLASSIC_COLS:
        if col not in row_dict:
            row_dict[col] = float("nan")
    return row_dict


def columns_for_dataframe(df) -> list[str]:
    """
    Return the display byte columns for a DataFrame.
    If any row has DLC > 8, include columns up to the maximum DLC present.
    """
    import pandas as pd
    if df.empty or "DLC" not in df.columns:
        return CLASSIC_COLS
    max_dlc = int(df["DLC"].max())
    max_bytes = DLC_TO_LEN.get(max_dlc, min(max_dlc, 64))
    if max_bytes <= 8:
        return CLASSIC_COLS
    return byte_cols_for_len(max_bytes)
