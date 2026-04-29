"""Persist AI analysis conclusions to ~/.canlab/memory.json."""
import json
from pathlib import Path
from datetime import datetime

MEMORY_FILE = Path.home() / ".canlab" / "memory.json"


def load_memory() -> list:
    try:
        if MEMORY_FILE.exists():
            return json.loads(MEMORY_FILE.read_text())
    except Exception:
        pass
    return []


def save_memory(entries: list):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        MEMORY_FILE.write_text(json.dumps(entries, indent=2))
    except Exception:
        pass


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
