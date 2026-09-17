"""Save/load full app state as a .canlab zip archive."""
import io
import json
import zipfile

import pandas as pd

# Bumped when the archive layout changes; readers accept anything they know.
PROJECT_FORMAT_VERSION = 2


def write_project(path: str, *, frames_chunks, signals=None, memory=None,
                  triggers=None, notes=None, annotations_json: str = "[]",
                  meta: dict | None = None) -> int:
    """Write a .canlab archive from parts, streaming the frames.

    `frames_chunks` is any iterable of DataFrames. They are written into one
    frames.csv member as they arrive, header once, so a recording of a few
    million frames never has to exist in memory as a single table. That is
    what lets the headless capture kit produce the same file the window
    saves. Returns the number of frames written.

    The layout is unchanged from save_project, so the format version stays 2
    and load_project reads both.
    """
    frames = 0
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        member = None
        text = None
        for chunk in frames_chunks:
            if chunk is None or len(chunk) == 0:
                continue
            if member is None:
                # Opened lazily: an archive with no frames must not carry an
                # empty frames.csv, which the reader would choke on.
                member = zf.open("frames.csv", "w", force_zip64=True)
                text = io.TextIOWrapper(member, encoding="utf-8", newline="")
            chunk.to_csv(text, index=False, header=frames == 0)
            frames += len(chunk)
        if text is not None:
            text.flush()
            text.detach()
            member.close()

        zf.writestr("signals.json", json.dumps(signals or [], indent=2))
        zf.writestr("memory.json", json.dumps(memory or [], indent=2))
        zf.writestr("triggers.json", json.dumps(triggers or [], indent=2))
        zf.writestr("notes.json", json.dumps(notes or {}, indent=2))
        zf.writestr("annotations.json", annotations_json or "[]")
        full_meta = {"format_version": PROJECT_FORMAT_VERSION,
                     "periodicities": {}, "vehicle_profile": "generic"}
        full_meta.update(meta or {})
        zf.writestr("meta.json", json.dumps(full_meta, indent=2))
    return frames


def save_project(state, path: str):
    """Zip: frames.csv, signals.json, memory.json, meta.json"""
    ann = getattr(state, "annotations", None)
    write_project(
        path,
        frames_chunks=[state.frames_df],
        signals=state.dbc_signals,
        memory=state.ai_memory,
        triggers=state.triggers,
        notes=getattr(state, "notes_by_signal", {}),
        annotations_json=ann.to_json() if ann is not None else "[]",
        meta={"periodicities": {k: float(v) for k, v in state.periodicities.items()},
              "vehicle_profile": getattr(state, "vehicle_profile", "generic")},
    )
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
                from canlab.core.canid import normalize_id
                df["ID"] = df["ID"].apply(normalize_id)
            state.frames_df = df
        else:
            state.frames_df = pd.DataFrame()

        if "signals.json" in names:
            state.dbc_signals = json.loads(zf.read("signals.json"))
        if "memory.json" in names:
            # Merge rather than replace: loading a project used to overwrite the
            # global AI memory, which the next save then wrote back to disk.
            from canlab.core.ai_memory import merge_entries
            state.ai_memory = merge_entries(state.ai_memory,
                                            json.loads(zf.read("memory.json")))
        if "triggers.json" in names:
            state.triggers    = json.loads(zf.read("triggers.json"))
        if "notes.json" in names:
            state.notes_by_signal = json.loads(zf.read("notes.json"))
        if "annotations.json" in names:
            from canlab.core.annotations import AnnotationSet
            state.annotations = AnnotationSet.from_json(zf.read("annotations.json").decode())

        if "meta.json" in names:
            # Older archives also carried repo_*/annotations/fingerprint keys
            # (features since removed); they are ignored.
            meta = json.loads(zf.read("meta.json"))
            state.vehicle_profile = meta.get("vehicle_profile",
                                              getattr(state, "vehicle_profile", "generic"))
            state.periodicities = {}
            for k, v in meta.get("periodicities", {}).items():
                try:
                    state.periodicities[k] = float(v)
                except (ValueError, TypeError):
                    pass   # skip corrupt entries rather than aborting the load

    state.project_path = path

    # Emit refresh signals
    if not state.frames_df.empty:
        state.frames_loaded.emit(len(state.frames_df))
        state.frames_updated.emit()
    if state.dbc_signals:
        state.dbc_updated.emit()
    state.project_loaded.emit()
