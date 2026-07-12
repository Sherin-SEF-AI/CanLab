"""CanLab MCP server — expose CAN reverse-engineering tools to an MCP client
(e.g. Claude Code / Claude Desktop) so the analysis loop is agent-drivable.

Run it as a stdio MCP server:
    python canlab/mcp_server.py

Register it with Claude Code (example ~/.config claude mcp entry):
    { "command": "/path/to/.venv/bin/python", "args": ["/path/to/canlab/mcp_server.py"] }

Tools operate on a single loaded log held in this process (call load_log first).
It does NOT need the GUI running.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_SESSION: dict = {"df": None, "path": None}


# ── Plain logic functions (unit-testable without the MCP transport) ───────────

def load_log(path: str) -> dict:
    """Load a CAN capture (CSV/candump/blf/asc/pcap/rlog/mdf) for analysis."""
    from core.log_parser import parse_log_file
    df = parse_log_file(path)
    _SESSION["df"] = df
    _SESSION["path"] = path
    ids = sorted(df["ID"].unique().tolist()) if not df.empty else []
    return {"path": path, "frame_count": int(len(df)), "unique_ids": ids}


def _df():
    df = _SESSION["df"]
    if df is None or df.empty:
        raise ValueError("No log loaded. Call load_log(path) first.")
    return df


def list_ids() -> list:
    """List CAN IDs in the loaded log with frame counts and mean period."""
    df = _df()
    out = []
    for can_id, grp in df.groupby("ID"):
        ts = grp["Timestamp"].to_numpy()
        period = float((ts[-1] - ts[0]) / max(len(ts) - 1, 1)) if len(ts) > 1 else 0.0
        out.append({"id": str(can_id), "count": int(len(grp)),
                    "period_ms": round(period * 1000, 2)})
    return sorted(out, key=lambda d: -d["count"])


def detect_counters_checksums() -> dict:
    """Run counter/checksum byte detection across all IDs."""
    from core.counter_checksum_detector import detect_counters_and_checksums
    res = detect_counters_and_checksums(_df())
    return {k: v for k, v in res.items()
            if v.get("counters") or v.get("checksums")}


def correlate(id1: str, id2: str) -> list:
    """Byte-level Pearson correlation between two CAN IDs."""
    from core.correlation_engine import correlate_id_pair
    from core.canid import normalize_id
    return correlate_id_pair(_df(), normalize_id(id1), normalize_id(id2))


def match_opendbc(top_k: int = 5) -> list:
    """Match the loaded capture's IDs against the opendbc DBC library."""
    from core.opendbc_matcher import match_capture
    ids = set(_df()["ID"].unique().tolist())
    return match_capture(ids, top_k=top_k)


def detect_multiplexers() -> dict:
    """Find multiplexed (mode-dependent) messages in the loaded log."""
    from core.mux_detector import detect_all_multiplexers
    return detect_all_multiplexers(_df())


def calibrate(reference_csv: str, top_k: int = 8) -> list:
    """Reference-driven calibration: find the CAN field that linearly explains a
    physical reference. reference_csv has columns timestamp,value. Returns ranked
    candidates with scale/offset (OEM-snapped), R², and PASS/UNCONFIRMED verdict."""
    import pandas as pd
    from core.reference_calibrate import calibrate_against_reference
    ref = pd.read_csv(reference_csv)
    cols = [c.lower() for c in ref.columns]
    tcol = ref.columns[cols.index("timestamp")] if "timestamp" in cols else ref.columns[0]
    vcol = ref.columns[cols.index("value")] if "value" in cols else ref.columns[1]
    return calibrate_against_reference(_df(), ref[tcol].to_numpy(),
                                       ref[vcol].to_numpy(), top_k=top_k)


def byte_stats(can_id: str) -> dict:
    """Per-byte min/max/mean/unique-count for one CAN ID."""
    import numpy as np
    from core.canid import normalize_id
    grp = _df()
    grp = grp[grp["ID"] == normalize_id(can_id)]
    if grp.empty:
        return {"id": can_id, "bytes": {}}
    stats = {}
    for i in range(8):
        c = f"B{i}"
        if c in grp.columns:
            s = grp[c].dropna()
            if not s.empty:
                stats[c] = {"min": int(s.min()), "max": int(s.max()),
                            "mean": round(float(s.mean()), 1),
                            "unique": int(s.nunique())}
    return {"id": normalize_id(can_id), "frames": int(len(grp)), "bytes": stats}


def _register(mcp):
    for fn in (load_log, list_ids, detect_counters_checksums, correlate,
               match_opendbc, detect_multiplexers, calibrate, byte_stats):
        mcp.tool()(fn)


def main():
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("canlab")
    _register(mcp)
    mcp.run()


if __name__ == "__main__":
    main()
