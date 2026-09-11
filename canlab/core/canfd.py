"""CAN FD helpers — variable-length frame support up to 64 bytes.

In the canonical frame DataFrame ``DLC`` holds the payload byte count (not the
FD DLC code), so widening the byte columns is a straight ``max(DLC)``.
"""

# CAN FD DLC code -> payload length (for sources that report the code)
DLC_TO_LEN = {
    0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7,
    8: 8, 9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64,
}

CLASSIC_COLS = [f"B{i}" for i in range(8)]


def columns_for_dataframe(df) -> list[str]:
    """Byte columns to display for ``df``: B0..B7, widened when longer frames exist."""
    if df is None or df.empty or "DLC" not in df.columns:
        return CLASSIC_COLS
    try:
        max_bytes = int(df["DLC"].max())
    except (TypeError, ValueError):
        return CLASSIC_COLS
    max_bytes = min(max(max_bytes, 8), 64)
    return [f"B{i}" for i in range(max_bytes)]
