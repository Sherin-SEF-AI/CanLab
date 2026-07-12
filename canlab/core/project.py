"""Save/load full app state as a .canlab zip archive."""
import io
import json
import zipfile
from pathlib import Path

import pandas as pd


def save_project(state, path: str):
    """Zip: frames.csv, signals.json, memory.json, meta.json"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if not state.frames_df.empty:
            buf = io.StringIO()
            state.frames_df.to_csv(buf, index=False)
            zf.writestr("frames.csv", buf.getvalue())

        zf.writestr("signals.json", json.dumps(state.dbc_signals, indent=2))
        zf.writestr("memory.json",  json.dumps(state.ai_memory,   indent=2))
        zf.writestr("triggers.json",json.dumps(state.triggers,    indent=2))
        zf.writestr("notes.json",   json.dumps(getattr(state, "notes_by_signal", {}), indent=2))

        meta = {
            "repo_url":    state.repo_url,
            "repo_info":   state.repo_info,
            "repo_readme": state.repo_readme,
            "annotations": state.annotations,
            "periodicities": {k: float(v) for k, v in state.periodicities.items()},
            "fingerprint": state.fingerprint,
        }
        zf.writestr("meta.json", json.dumps(meta, indent=2))

    state.project_path = path


def load_project(state, path: str):
    """Restore all fields from .canlab zip; emit signals so tabs refresh."""
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()

        if "frames.csv" in names:
            # Force ID to string: the column holds hex strings ("244", "0A6");
            # without this, pandas re-infers all-numeric IDs as int64 and every
            # later string comparison (get_frames_for_id, per-ID views) silently
            # fails to match after a project is reloaded.
            df = pd.read_csv(io.StringIO(zf.read("frames.csv").decode()),
                             dtype={"ID": str})
            if "ID" in df.columns:
                from core.canid import normalize_id
                df["ID"] = df["ID"].apply(normalize_id)
            state.frames_df = df
        else:
            state.frames_df = pd.DataFrame()

        if "signals.json" in names:
            state.dbc_signals = json.loads(zf.read("signals.json"))
        if "memory.json" in names:
            state.ai_memory   = json.loads(zf.read("memory.json"))
        if "triggers.json" in names:
            state.triggers    = json.loads(zf.read("triggers.json"))
        if "notes.json" in names:
            state.notes_by_signal = json.loads(zf.read("notes.json"))

        if "meta.json" in names:
            meta = json.loads(zf.read("meta.json"))
            state.repo_url    = meta.get("repo_url", "")
            state.repo_info   = meta.get("repo_info", {})
            state.repo_readme = meta.get("repo_readme", "")
            state.annotations = meta.get("annotations", {})
            state.periodicities = {}
            for k, v in meta.get("periodicities", {}).items():
                try:
                    state.periodicities[k] = float(v)
                except (ValueError, TypeError):
                    pass   # skip corrupt entries rather than aborting the load
            state.fingerprint = meta.get("fingerprint", {})

    state.project_path = path

    # Emit refresh signals
    if not state.frames_df.empty:
        state.frames_loaded.emit(len(state.frames_df))
        state.frames_updated.emit()
    if state.dbc_signals:
        state.dbc_updated.emit()
    if state.repo_info:
        state.repo_loaded.emit(state.repo_info)
    state.project_loaded.emit()
