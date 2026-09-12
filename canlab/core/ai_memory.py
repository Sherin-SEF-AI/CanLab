"""Persist AI analysis conclusions to ~/.canlab/memory.json."""
import json
from pathlib import Path
from datetime import datetime
import logging

log = logging.getLogger(__name__)

MEMORY_FILE = Path.home() / ".canlab" / "memory.json"


def load_memory() -> list:
    try:
        if MEMORY_FILE.exists():
            return json.loads(MEMORY_FILE.read_text())
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return []


def save_memory(entries: list):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        MEMORY_FILE.write_text(json.dumps(entries, indent=2))
    except Exception:
        log.debug("suppressed exception", exc_info=True)


def add_entry(entries: list, hex_id: str, conclusion: str, source: str = "AI") -> list:
    entries = [e for e in entries if e.get("id") != hex_id]  # replace stale
    entries.append({
        "id":         hex_id,
        "conclusion": conclusion[:500],
        "source":     source,
        "timestamp":  datetime.now().isoformat(timespec="seconds"),
    })
    save_memory(entries)
    return entries


def get_memory_context(entries: list, max_entries: int = 20) -> str:
    """Format memory entries as text for injection into AI prompts."""
    if not entries:
        return ""
    lines = ["=== Prior Analysis Memory ==="]
    for e in entries[-max_entries:]:
        lines.append(f"ID 0x{e['id']}: {e['conclusion'][:200]}")
    return "\n".join(lines)


def merge_entries(existing: list, incoming: list) -> list:
    """Merge memory from a project archive into the session's memory.

    Loading a project used to replace the global list outright, so the next
    save wrote the project's memory over everything else the user had.
    """
    merged = list(existing or [])
    seen = {(e.get("id"), e.get("conclusion")) for e in merged if isinstance(e, dict)}
    for entry in incoming or []:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("id"), entry.get("conclusion"))
        if key not in seen:
            merged.append(entry)
            seen.add(key)
    return merged
