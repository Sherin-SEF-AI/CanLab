"""Match a captured CAN log against real opendbc OEM DBC files.

This module scores an observed set of CAN arbitration IDs against the frame-ID
sets of the DBC files shipped in commaai/opendbc and reports the best-matching
vehicle(s).

It is designed to work OFFLINE: the network is only touched when
:func:`refresh_index` is called explicitly. The parsed index is cached as JSON
under ``~/.canlab/opendbc_cache`` so subsequent runs are fast, and every public
function degrades gracefully (returns ``[]`` / cached data) when there is no
cache and no connectivity. Nothing here requires the network at import time.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

try:  # cantools is a hard dependency of the app, but stay import-safe for tests
    import cantools
except Exception:  # pragma: no cover - only if cantools is missing
    cantools = None

from core.canid import normalize_id

# ── Locations ───────────────────────────────────────────────────────────────────

CACHE_DIR = Path.home() / ".canlab" / "opendbc_cache"
INDEX_PATH = CACHE_DIR / "index.json"

_OPENDBC_OWNER = "commaai"
_OPENDBC_REPO = "opendbc"
_DBC_DIR_PREFIX = "opendbc/dbc/"          # only OEM DBCs live here
_INDEX_VERSION = 1


# ── Index building (network) ────────────────────────────────────────────────────

def _headers(token: str = "") -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _list_remote_dbcs(token: str = "") -> list[dict]:
    """Return ``[{"name", "path", "download_url"}, ...]`` for every opendbc DBC.

    Raises on network / HTTP errors so the caller can fall back to the cache.
    """
    base = f"https://api.github.com/repos/{_OPENDBC_OWNER}/{_OPENDBC_REPO}"
    meta = requests.get(base, headers=_headers(token), timeout=15)
    meta.raise_for_status()
    branch = meta.json().get("default_branch", "master")

    tree = requests.get(
        f"{base}/git/trees/{branch}?recursive=1", headers=_headers(token), timeout=30
    )
    tree.raise_for_status()

    out = []
    for item in tree.json().get("tree", []):
        path = item.get("path", "")
        if item.get("type") != "blob" or not path.endswith(".dbc"):
            continue
        if not path.startswith(_DBC_DIR_PREFIX):
            continue
        out.append({
            "name": path[len(_DBC_DIR_PREFIX):],
            "path": path,
            "download_url": (
                f"https://raw.githubusercontent.com/"
                f"{_OPENDBC_OWNER}/{_OPENDBC_REPO}/{branch}/{path}"
            ),
        })
    return out


def _parse_dbc_file(path: Path) -> dict | None:
    """Parse a .dbc into ``{"frame_ids": [...], "message_names": [...]}``.

    Returns ``None`` if the file cannot be parsed. Frame IDs are stored in the
    canonical normalized form so lookups line up with the rest of the app.
    """
    if cantools is None:
        return None
    try:
        db = cantools.database.load_file(str(path))
    except Exception:
        return None
    frame_ids, names = [], []
    for msg in db.messages:
        frame_ids.append(normalize_id(msg.frame_id))
        names.append(msg.name)
    return {
        "frame_ids": sorted(set(frame_ids)),
        "message_names": sorted(set(names)),
    }


def refresh_index(force: bool = False, token: str = "", progress=None) -> dict:
    """Build or refresh the local opendbc index and return it.

    Downloads any missing DBC files into :data:`CACHE_DIR`, parses each into its
    frame-ID / message-name sets and writes the combined index to
    :data:`INDEX_PATH` as JSON. On any network failure the already-cached index
    is returned instead of raising.

    Args:
        force: re-download DBC files even if a local copy exists.
        token: optional GitHub token to avoid anonymous rate limits.
        progress: optional ``callable(str)`` for status messages.

    Returns:
        The index mapping ``{dbc_name: {"frame_ids": [...], "message_names": [...]}}``.
    """
    def _say(msg: str):
        if progress:
            progress(msg)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        _say("Fetching opendbc file list…")
        remote = _list_remote_dbcs(token)
    except Exception as e:  # offline / rate-limited → keep whatever we have
        _say(f"Network unavailable ({e}); using cached index.")
        return load_index()

    index: dict[str, dict] = {}
    total = len(remote)
    for i, f in enumerate(remote, 1):
        dest = CACHE_DIR / f["name"]
        if force or not dest.exists():
            try:
                _say(f"Downloading {f['name']} ({i}/{total})…")
                r = requests.get(f["download_url"], headers=_headers(token), timeout=60)
                r.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
            except Exception:
                continue  # skip this one, keep going
        parsed = _parse_dbc_file(dest)
        if parsed:
            index[f["name"]] = parsed

    if index:
        try:
            INDEX_PATH.write_text(json.dumps({"version": _INDEX_VERSION, "dbcs": index}))
        except Exception:
            pass
        _say(f"Indexed {len(index)} opendbc DBC files.")
        return index

    # Nothing parsed (e.g. cantools missing) — fall back to any cached index.
    return load_index()


# ── Index loading (offline) ─────────────────────────────────────────────────────

def load_index() -> dict:
    """Return the cached index, or ``{}`` if none exists / it is unreadable.

    Never touches the network. Shape:
    ``{dbc_name: {"frame_ids": [...], "message_names": [...]}}``.
    """
    if not INDEX_PATH.exists():
        return {}
    try:
        data = json.loads(INDEX_PATH.read_text())
    except Exception:
        return {}
    dbcs = data.get("dbcs", {})
    return dbcs if isinstance(dbcs, dict) else {}


# ── Scoring ─────────────────────────────────────────────────────────────────────

def match_capture(observed_ids, top_k: int = 5, index: dict | None = None) -> list[dict]:
    """Rank opendbc DBCs by how well their frame IDs match ``observed_ids``.

    Both sides are normalized through :func:`core.canid.normalize_id` so ID
    widths line up. Scoring uses the Jaccard index of the two ID sets
    (``|observed ∩ dbc| / |observed ∪ dbc|``), which rewards a DBC that both
    covers the observed IDs and does not carry many unrelated ones.

    Args:
        observed_ids: iterable of CAN IDs seen in the capture (any hex form).
        top_k: number of results to return.
        index: optional pre-built index (mainly for testing); defaults to the
            cached index from :func:`load_index`.

    Returns:
        Ranked list of dicts::

            [{"dbc": name,
              "score": 0.0-1.0,          # Jaccard overlap
              "coverage": 0.0-1.0,       # fraction of observed IDs explained
              "matched_ids": [...],      # sorted intersection
              "message_count": N}, ...]

        Returns ``[]`` when there is nothing to match against (no index) or no
        observed IDs.
    """
    if index is None:
        index = load_index()
    if not index:
        return []

    observed = {normalize_id(x) for x in observed_ids if str(x).strip() != ""}
    if not observed:
        return []

    results = []
    for name, entry in index.items():
        dbc_ids = {normalize_id(x) for x in entry.get("frame_ids", [])}
        if not dbc_ids:
            continue
        matched = observed & dbc_ids
        if not matched:
            continue
        union = observed | dbc_ids
        results.append({
            "dbc": name,
            "score": len(matched) / len(union),
            "coverage": len(matched) / len(observed),
            "matched_ids": sorted(matched),
            "message_count": len(entry.get("message_names", entry.get("frame_ids", []))),
        })

    # Rank by Jaccard, then by absolute match count, then name for stability.
    results.sort(key=lambda r: (r["score"], len(r["matched_ids"]), r["dbc"]), reverse=True)
    return results[:top_k]


# ── Legacy signal-name cross-reference (used by the DBC/Intelligence tabs) ──────

# Offline index: common Hyundai/Kia signals from commaai/opendbc. Kept so the
# existing signal-name cross-reference in the UI tabs keeps working.
_OFFLINE_INDEX = {
    "WHL_SPD_FL": {"file": "hyundai_kia_generic.dbc", "msg": "WHL_SPD11", "id": "0x386"},
    "WHL_SPD_FR": {"file": "hyundai_kia_generic.dbc", "msg": "WHL_SPD11", "id": "0x386"},
    "WHL_SPD_RL": {"file": "hyundai_kia_generic.dbc", "msg": "WHL_SPD11", "id": "0x386"},
    "WHL_SPD_RR": {"file": "hyundai_kia_generic.dbc", "msg": "WHL_SPD11", "id": "0x386"},
    "CR_Mdps_StrColTq":   {"file": "hyundai_kia_generic.dbc", "msg": "MDPS12",    "id": "0x018"},
    "CF_Mdps_Stat":       {"file": "hyundai_kia_generic.dbc", "msg": "MDPS12",    "id": "0x018"},
    "SAS_Angle":          {"file": "hyundai_kia_generic.dbc", "msg": "SAS11",     "id": "0x260"},
    "SAS_Speed":          {"file": "hyundai_kia_generic.dbc", "msg": "SAS11",     "id": "0x260"},
    "CV_Brake_Act":       {"file": "hyundai_kia_generic.dbc", "msg": "BRAKE11",   "id": "0x02C"},
    "CF_Clu_Vanz":        {"file": "hyundai_kia_generic.dbc", "msg": "CLU11",     "id": "0x544"},
    "CF_Lkas_Actuation":  {"file": "hyundai_kia_generic.dbc", "msg": "LKAS11",    "id": "0x050"},
    "CF_Lkas_ToiFlt":     {"file": "hyundai_kia_generic.dbc", "msg": "LKAS11",    "id": "0x050"},
}


def scan(state, repo_context: dict = None) -> dict:
    """
    Compare state.dbc_signals against offline index + optional repo DBC content.
    Returns {signal_name -> match_info_dict}.
    """
    matches = {}
    index = dict(_OFFLINE_INDEX)

    if repo_context:
        readme = repo_context.get("readme", "")
        _enrich_index_from_text(index, readme)

    for sig in state.dbc_signals:
        sname = sig.get("signal_name", "")
        if sname in index:
            matches[sname] = index[sname]
            continue
        for key, val in index.items():
            if sname.upper() in key.upper() or key.upper() in sname.upper():
                matches[sname] = {**val, "partial": True}
                break

    return matches


def _enrich_index_from_text(index: dict, text: str):
    """Very simple: pull signal names from DBC-style SG_ lines in readme/DBC text."""
    for m in re.finditer(r"SG_\s+(\w+)\s*:", text):
        name = m.group(1)
        if name not in index:
            index[name] = {"file": "repo", "msg": "?", "id": "?"}
