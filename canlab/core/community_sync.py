"""
Community Vehicle Profile Sync.

Fetches a profiles.json index from a configurable GitHub raw URL and merges
vehicle profiles + DBC fragments into ~/.canlab/profiles/.

Profile JSON format:
[
  {
    "id": "hyundai_kona_2019",
    "vehicle": "Hyundai Kona 2019",
    "can_ids": ["018", "260", "316", "544"],
    "dbc_url": "https://raw.githubusercontent.com/.../kona.dbc",
    "notes": "Collected by community"
  },
  ...
]
"""
import json
import os
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

_DEFAULT_PROFILES_URL = (
    "https://raw.githubusercontent.com/commaai/opendbc/master/opendbc/"
    "can/hyundai_kona.dbc"   # placeholder — real index would be a JSON file
)

PROFILES_DIR = Path.home() / ".canlab" / "profiles"


def list_local_profiles() -> list[dict]:
    """Return all locally cached profiles."""
    if not PROFILES_DIR.exists():
        return []
    profiles = []
    for p in PROFILES_DIR.glob("*.json"):
        try:
            profiles.append(json.loads(p.read_text()))
        except Exception:
            pass
    return profiles


def save_profile(profile: dict) -> Path:
    """Save a profile dict to ~/.canlab/profiles/<id>.json."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    pid  = profile.get("id", "unknown")
    dest = PROFILES_DIR / f"{pid}.json"
    dest.write_text(json.dumps(profile, indent=2))
    return dest


class CommunitySyncWorker(QThread):
    profiles_ready = pyqtSignal(list)    # list of profile dicts
    error          = pyqtSignal(str)
    progress       = pyqtSignal(str)     # status text

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url   = url
        self._abort = False

    def stop(self):
        self._abort = True
        self.quit()
        self.wait(2000)

    def run(self):
        try:
            self.progress.emit(f"Fetching {self._url} …")
            req  = urllib.request.Request(
                self._url,
                headers={"User-Agent": "CANLAB/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            self.error.emit(f"Fetch failed: {e}")
            return

        if self._abort:
            return

        # Try to parse as JSON array of profiles
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                profiles = data
            elif isinstance(data, dict):
                # Could be a single profile or a wrapper
                profiles = data.get("profiles", [data])
            else:
                profiles = []
        except json.JSONDecodeError:
            # Not JSON — maybe a DBC file; wrap it
            profiles = [{
                "id":      "fetched_dbc",
                "vehicle": "Fetched DBC",
                "dbc_content": raw,
                "notes": f"Fetched from {self._url}",
            }]

        self.progress.emit(f"Found {len(profiles)} profile(s).")
        self.profiles_ready.emit(profiles)

    @staticmethod
    def apply_profile(state, profile: dict) -> int:
        """
        Merge a community profile into state.dbc_signals.
        Returns number of signals added.
        """
        from core.dbc_manager import load_dbc
        import tempfile, os

        dbc_content = profile.get("dbc_content", "")
        added = 0
        if dbc_content:
            with tempfile.NamedTemporaryFile(
                suffix=".dbc", mode="w", delete=False
            ) as f:
                f.write(dbc_content)
                tmp = f.name
            try:
                sigs = load_dbc(tmp)
                existing = {s.get("signal_name") for s in state.dbc_signals}
                for sig in sigs:
                    if sig["signal_name"] not in existing:
                        state.add_dbc_signal(sig)
                        added += 1
            except Exception:
                pass
            finally:
                os.unlink(tmp)

        save_profile(profile)
        return added
